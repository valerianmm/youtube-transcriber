"""Projects: named collections of sources, stored as folders with a project.json."""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from ytt.config import Paths
from ytt.library import sanitize
from ytt.storage import Fingerprint, atomic_write_json, read_json
from ytt.youtube import VideoInfo

SubDirs = ("documents", "exports", ".history")


@dataclass
class Source:
    kind: Literal["video", "playlist", "channel"]
    url: str
    title: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    language: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    videos: list[VideoInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "url": self.url,
            "title": self.title,
            "language": self.language,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "videos": [v.to_dict() for v in self.videos],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Source:
        return cls(
            id=d["id"],
            kind=d["kind"],
            url=d["url"],
            title=d["title"],
            language=d.get("language"),
            date_from=d.get("date_from"),
            date_to=d.get("date_to"),
            videos=[VideoInfo.from_dict(v) for v in d.get("videos", [])],
        )


@dataclass
class DocumentMeta:
    name: str
    file: str
    doc_type: str
    generated_at: str
    edited_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "doc_type": self.doc_type,
            "generated_at": self.generated_at,
            "edited_at": self.edited_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DocumentMeta:
        return cls(
            name=d["name"],
            file=d["file"],
            doc_type=d["doc_type"],
            generated_at=d["generated_at"],
            edited_at=d.get("edited_at"),
        )


@dataclass
class Project:
    slug: str
    name: str
    language: str | None = None
    sources: list[Source] = field(default_factory=list)
    cancelled: set[str] = field(default_factory=set)
    documents: list[DocumentMeta] = field(default_factory=list)
    fingerprint: Fingerprint | None = field(default=None, compare=False, repr=False)

    def videos(self) -> list[VideoInfo]:
        seen: set[str] = set()
        out: list[VideoInfo] = []
        for s in self.sources:
            for v in s.videos:
                if v.id not in seen:
                    seen.add(v.id)
                    out.append(v)
        return out

    def forced_language_for(self, video_id: str) -> str | None:
        for s in self.sources:
            if s.language and any(v.id == video_id for v in s.videos):
                return s.language
        return self.language

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "name": self.name,
            "language": self.language,
            "sources": [s.to_dict() for s in self.sources],
            "cancelled": sorted(self.cancelled),
            "documents": [d.to_dict() for d in self.documents],
        }

    @classmethod
    def from_dict(cls, slug: str, d: dict, fp: Fingerprint | None = None) -> Project:
        return cls(
            slug=slug,
            name=d["name"],
            language=d.get("language"),
            sources=[Source.from_dict(s) for s in d.get("sources", [])],
            cancelled=set(d.get("cancelled", [])),
            documents=[DocumentMeta.from_dict(x) for x in d.get("documents", [])],
            fingerprint=fp,
        )


class ProjectStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    def project_dir(self, slug: str) -> Path:
        return self.paths.projects / slug

    def _file(self, slug: str) -> Path:
        return self.project_dir(slug) / "project.json"

    def list(self) -> list[Project]:
        out: list[Project] = []
        if self.paths.projects.is_dir():
            for d in self.paths.projects.iterdir():
                if (d / "project.json").is_file():
                    try:
                        out.append(self.get(d.name))
                    except (ValueError, KeyError):
                        continue
        return sorted(out, key=lambda p: p.name.lower())

    def get(self, slug: str) -> Project:
        data, fp = read_json(self._file(slug))
        return Project.from_dict(slug, data, fp)

    def _check_name(self, name: str, exclude_slug: str | None = None) -> str:
        if not name.strip():
            raise ValueError("Project name must not be empty")
        slug = sanitize(name, 60)
        taken: set[str] = set()
        for p in self.list():
            if p.slug != exclude_slug:
                taken.add(p.name.casefold())
                taken.add(p.slug.casefold())
        if name.strip().casefold() in taken or slug.casefold() in taken:
            raise ValueError(f"A project named {name!r} already exists")
        return slug

    def create(self, name: str) -> Project:
        name = name.strip()
        slug = self._check_name(name)
        folder = self.project_dir(slug)
        folder.mkdir(parents=True, exist_ok=False)
        for sub in SubDirs:
            (folder / sub).mkdir(exist_ok=True)
        project = Project(slug=slug, name=name)
        project.fingerprint = atomic_write_json(
            self._file(slug), project.to_dict(), expect=None
        )
        return project

    def save(self, project: Project) -> None:
        project.fingerprint = atomic_write_json(
            self._file(project.slug), project.to_dict(), expect=project.fingerprint
        )

    def rename(self, slug: str, new_name: str) -> Project:
        project = self.get(slug)
        new_name = new_name.strip()
        new_slug = self._check_name(new_name, exclude_slug=slug)
        if new_slug != slug:
            os.rename(self.project_dir(slug), self.project_dir(new_slug))
            project.slug = new_slug
        project.name = new_name
        self.save(project)
        return project

    def delete(self, slug: str) -> None:
        trash = Path(os.environ.get("YTT_TRASH_DIR") or Path.home() / ".Trash")
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H.%M.%S.%f")
        shutil.move(str(self.project_dir(slug)), str(trash / f"{slug} {stamp}"))

    def add_source(self, slug: str, source: Source) -> Project:
        project = self.get(slug)
        project.sources.append(source)
        self.save(project)
        return project

    def remove_source(self, slug: str, source_id: str) -> Project:
        project = self.get(slug)
        project.sources = [s for s in project.sources if s.id != source_id]
        self.save(project)
        return project

    def merge_new_videos(
        self, slug: str, source_id: str, infos: list[VideoInfo]
    ) -> list[VideoInfo]:
        project = self.get(slug)
        source = next(s for s in project.sources if s.id == source_id)
        seen = {v.id for v in source.videos}
        new: list[VideoInfo] = []
        for info in infos:
            if info.id not in seen:
                seen.add(info.id)
                new.append(info)
        source.videos.extend(new)
        self.save(project)
        return new
