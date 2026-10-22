"""YouTube URL splitting and classification."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}


@dataclass(frozen=True)
class ParsedUrl:
    kind: Literal["video", "playlist", "channel", "invalid"]
    url: str
    video_id: str | None = None


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def split_urls(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        for tok in re.split(r"[\s,]+", line):
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def _video(video_id: str) -> ParsedUrl:
    if not _ID.match(video_id):
        return ParsedUrl("invalid", video_id)
    return ParsedUrl("video", video_url(video_id), video_id)


def classify(url: str) -> ParsedUrl:
    raw = url.strip()
    try:
        p = urlparse(raw)
    except ValueError:
        return ParsedUrl("invalid", raw)
    host = (p.hostname or "").lower()
    parts = [s for s in p.path.split("/") if s]
    query = parse_qs(p.query)

    if p.scheme not in ("http", "https"):
        return ParsedUrl("invalid", raw)
    if host == "youtu.be":
        return _video(parts[0]) if parts else ParsedUrl("invalid", raw)
    if host not in _HOSTS or not parts:
        return ParsedUrl("invalid", raw)

    first = parts[0]
    if first == "watch" and query.get("v"):
        return _video(query["v"][0])
    if first in ("shorts", "live") and len(parts) >= 2:
        return _video(parts[1])
    if first == "playlist" and query.get("list"):
        return ParsedUrl("playlist", f"https://www.youtube.com/playlist?list={query['list'][0]}")
    if first.startswith("@") and len(first) > 1:
        return ParsedUrl("channel", f"https://www.youtube.com/{first}/videos")
    if first in ("channel", "c", "user") and len(parts) >= 2:
        return ParsedUrl("channel", f"https://www.youtube.com/{first}/{parts[1]}/videos")
    return ParsedUrl("invalid", raw)
