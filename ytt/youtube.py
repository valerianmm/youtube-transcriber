"""yt-dlp access: expanding sources into videos, and downloading audio."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import yt_dlp

from ytt.urls import ParsedUrl, video_url


class DownloadError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Aborted(Exception):
    pass


@dataclass
class VideoInfo:
    id: str
    title: str
    url: str
    channel: str = ""
    upload_date: date | None = None
    duration: int | None = None
    language: str | None = None
    available: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "channel": self.channel,
            "upload_date": self.upload_date.isoformat() if self.upload_date else None,
            "duration": self.duration,
            "language": self.language,
            "available": self.available,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> VideoInfo:
        ud = d.get("upload_date")
        return cls(
            id=d["id"],
            title=d["title"],
            url=d["url"],
            channel=d.get("channel", ""),
            upload_date=date.fromisoformat(ud) if ud else None,
            duration=d.get("duration"),
            language=d.get("language"),
            available=d.get("available", True),
            error=d.get("error"),
        )


def _ydl(opts: dict) -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL(opts)


_FRIENDLY = [
    (("private video",), "Private video"),
    (("members-only", "members only", "join this channel"), "Members-only"),
    (("confirm your age", "age-restricted", "age restricted"), "Age-restricted"),
    (("your country", "geo restricted", "geo-restricted", "region"), "Region-blocked"),
    (("unavailable", "removed", "deleted", "does not exist", "no longer available"),
     "Removed or unavailable"),
]


def friendly_error(exc: BaseException) -> str:
    text = str(exc.reason) if isinstance(exc, DownloadError) else str(exc)
    low = text.lower()
    for needles, label in _FRIENDLY:
        if any(n in low for n in needles):
            return label
    lines = text.strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        s = str(value)
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _info_from_entry(entry: dict) -> VideoInfo:
    vid = entry["id"]
    return VideoInfo(
        id=vid,
        title=entry.get("title") or vid,
        url=video_url(vid),
        channel=entry.get("channel") or entry.get("uploader") or "",
        upload_date=_parse_date(entry.get("upload_date")),
        duration=entry.get("duration"),
        language=entry.get("language"),
    )


def fetch_video(video_id: str) -> VideoInfo:
    url = video_url(video_id)
    try:
        with _ydl({"quiet": True, "noprogress": True, "skip_download": True}) as ydl:
            entry = ydl.extract_info(url, download=False)
        return _info_from_entry({**entry, "id": entry.get("id") or video_id})
    except Exception as e:  # noqa: BLE001 - any extractor failure becomes a failed item
        return VideoInfo(id=video_id, title=video_id, url=url, available=False,
                         error=friendly_error(e))


_PLACEHOLDERS = {"[private video]": "Private video", "[deleted video]": "Removed or unavailable"}


def list_playlist(url: str) -> list[VideoInfo]:
    opts = {"quiet": True, "noprogress": True, "extract_flat": "in_playlist"}
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise DownloadError(friendly_error(e)) from e
    out: list[VideoInfo] = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        title = entry.get("title")
        error = _PLACEHOLDERS.get((title or "").strip().lower()) or (
            None if title else "Removed or unavailable")
        if error:
            out.append(VideoInfo(id=entry["id"], title=entry["id"], url=video_url(entry["id"]),
                                 available=False, error=error))
        else:
            out.append(_info_from_entry(entry))
    return out


def take_in_range(infos: Iterable[VideoInfo], start: date, end: date | None) -> list[VideoInfo]:
    """infos is newest first: skip those after `end`, stop at the first before `start`."""
    out: list[VideoInfo] = []
    for info in infos:
        d = info.upload_date
        if d is not None:
            if end is not None and d > end:
                continue
            if d < start:
                break
        out.append(info)
    return out


def list_channel(url: str, date_from: date | None = None,
                 date_to: date | None = None) -> list[VideoInfo]:
    opts = {"quiet": True, "noprogress": True, "extract_flat": False,
            "lazy_playlist": True, "ignoreerrors": True}
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            lazy = (_info_from_entry(e) for e in info.get("entries") or [] if e and e.get("id"))
            return take_in_range(lazy, date_from or date.min, date_to)
    except Exception as e:  # noqa: BLE001
        raise DownloadError(friendly_error(e)) from e


def expand(parsed: ParsedUrl, date_from: date | None = None,
           date_to: date | None = None) -> list[VideoInfo]:
    if parsed.kind == "video" and parsed.video_id:
        return [fetch_video(parsed.video_id)]
    if parsed.kind == "playlist":
        return list_playlist(parsed.url)
    if parsed.kind == "channel":
        return list_channel(parsed.url, date_from, date_to)
    raise DownloadError("Not a valid YouTube URL")


def with_download_info(info: VideoInfo, meta: dict) -> VideoInfo:
    """Fill in what a flat playlist entry lacks from the full info dict of the download."""
    return replace(
        info,
        title=meta.get("title") or info.title,
        channel=meta.get("channel") or meta.get("uploader") or info.channel,
        upload_date=_parse_date(meta.get("upload_date")) or info.upload_date,
        duration=meta.get("duration") or info.duration,
        language=meta.get("language") or info.language,
    )


def download_audio(
    video_id: str, dest_dir: Path, should_abort: Callable[[], bool]
) -> tuple[Path, dict]:
    """Returns the audio file and yt-dlp's full info dict for the video."""
    def hook(_status: dict) -> None:
        if should_abort():
            raise Aborted()

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "noprogress": True,
    }
    try:
        with _ydl(opts) as ydl:
            meta = ydl.extract_info(video_url(video_id), download=True) or {}
    except Aborted:
        raise
    except Exception as e:  # noqa: BLE001
        if isinstance(e, DownloadError):
            raise
        raise DownloadError(friendly_error(e)) from e
    files = [p for p in dest_dir.glob(f"{video_id}.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise DownloadError("Download produced no audio file")
    return files[0], meta
