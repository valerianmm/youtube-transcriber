"""Shared transcript library: transcript files plus an index.json of records."""
from __future__ import annotations

import os
import re
import threading
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Literal

from ytt.config import Paths
from ytt.storage import atomic_write_json, atomic_write_text, read_json
from ytt.transcribe import TranscriptResult
from ytt.youtube import VideoInfo

_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_ID_IN_NAME = re.compile(r"\[([^\[\]]+)\]\.txt\.part$")


def sanitize(name: str, max_len: int = 80) -> str:
    cleaned = _FORBIDDEN.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(". ")
    cleaned = cleaned[:max_len].strip(". ")
    return cleaned or "untitled"


def trim_utf8_bytes(text: str, max_bytes: int) -> str:
    """Truncate on a character boundary so the UTF-8 encoding fits max_bytes."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip(". ")


# macOS limits a filename component to 255 bytes. The longest name written is
# storage's temp file ".<final>.part.<12 hex>.tmp" = len(final) + 23 bytes.
_MAX_NAME_BYTES = 255 - 23
_MAX_DIR_BYTES = 200


def languages_mismatch(whisper: str | None, youtube: str | None) -> bool:
    if not whisper or not youtube:
        return False
    primary = lambda code: re.split(r"[-_]", code.strip().lower())[0]  # noqa: E731
    return primary(whisper) != primary(youtube)


@dataclass
class VideoRecord:
    video_id: str
    title: str
    channel: str
    url: str
    upload_date: str | None
    duration: int | None
    status: Literal["done", "failed"]
    path: str | None = None
    model: str | None = None
    language: str | None = None
    youtube_language: str | None = None
    language_mismatch: bool = False
    transcribed_at: str | None = None
    error: str | None = None


class Library:
    def __init__(self, paths: Paths):
        self.paths = paths
        self._lock = threading.Lock()

    # --- index -----------------------------------------------------------
    def _load(self) -> dict[str, dict]:
        try:
            data, _ = read_json(self.paths.index)
        except FileNotFoundError:
            return {}
        return data.get("videos", {})

    def _save(self, videos: dict[str, dict]) -> None:
        atomic_write_json(self.paths.index, {"version": 1, "videos": videos})

    @staticmethod
    def _record(d: dict) -> VideoRecord:
        known = {f.name for f in fields(VideoRecord)}
        return VideoRecord(**{k: v for k, v in d.items() if k in known})

    # --- queries ---------------------------------------------------------
    def get(self, video_id: str) -> VideoRecord | None:
        with self._lock:
            d = self._load().get(video_id)
        return self._record(d) if d else None

    def is_done(self, video_id: str) -> bool:
        rec = self.get(video_id)
        return bool(
            rec and rec.status == "done" and rec.path
            and (self.paths.transcripts / rec.path).is_file()
        )

    def transcript_path_for(self, info: VideoInfo) -> Path:
        channel = sanitize(info.channel) if info.channel.strip() else "Unknown channel"
        channel = trim_utf8_bytes(channel, _MAX_DIR_BYTES) or "Unknown channel"
        date_part = info.upload_date.isoformat() if info.upload_date else "unknown-date"
        prefix, suffix = f"{date_part}_", f" [{info.id}].txt"
        budget = _MAX_NAME_BYTES - len((prefix + suffix).encode("utf-8"))
        title = trim_utf8_bytes(sanitize(info.title), max(budget, 0)) or "untitled"
        name = f"{prefix}{title}{suffix}"
        return self.paths.transcripts / channel / name

    def read_transcript(self, video_id: str) -> str:
        rec = self.get(video_id)
        if rec is None or not rec.path:
            raise FileNotFoundError(f"No transcript for {video_id}")
        return (self.paths.transcripts / rec.path).read_text(encoding="utf-8")

    # --- writes ----------------------------------------------------------
    def write_transcript(
        self, info: VideoInfo, result: TranscriptResult, model_key: str
    ) -> VideoRecord:
        final = self.transcript_path_for(info)
        part = final.with_name(final.name + ".part")
        atomic_write_text(part, result.text)
        os.replace(part, final)
        rel = final.relative_to(self.paths.transcripts).as_posix()
        rec = VideoRecord(
            video_id=info.id,
            title=info.title,
            channel=info.channel,
            url=info.url,
            upload_date=info.upload_date.isoformat() if info.upload_date else None,
            duration=info.duration,
            status="done",
            path=rel,
            model=model_key,
            language=result.language,
            youtube_language=info.language,
            language_mismatch=languages_mismatch(result.language, info.language),
            transcribed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        with self._lock:
            videos = self._load()
            previous = videos.get(info.id)
            videos[info.id] = asdict(rec)
            self._save(videos)
            if previous and previous.get("path") and previous["path"] != rel:
                (self.paths.transcripts / previous["path"]).unlink(missing_ok=True)
        return rec

    def mark_failed(self, info: VideoInfo, error: str) -> VideoRecord:
        with self._lock:
            videos = self._load()
            existing = videos.get(info.id)
            if existing and existing.get("status") == "done":
                return self._record(existing)
            rec = VideoRecord(
                video_id=info.id,
                title=info.title,
                channel=info.channel,
                url=info.url,
                upload_date=info.upload_date.isoformat() if info.upload_date else None,
                duration=info.duration,
                status="failed",
                youtube_language=info.language,
                error=error,
            )
            videos[info.id] = asdict(rec)
            self._save(videos)
        return rec

    def cleanup_partials(self) -> list[str]:
        ids: list[str] = []
        if not self.paths.transcripts.exists():
            return ids
        for part in self.paths.transcripts.rglob("*.txt.part"):
            m = _ID_IN_NAME.search(part.name)
            if m:
                ids.append(m.group(1))
            part.unlink(missing_ok=True)
        return ids
