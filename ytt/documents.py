"""Project documents: create, edit with conflict detection, history, export."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from ytt.library import sanitize
from ytt.pdf import markdown_to_pdf
from ytt.projects import DocumentMeta, ProjectStore
from ytt.storage import (
    Fingerprint,
    atomic_write_text,
    read_text,
    save_history,
)

# macOS filename limit is 255 bytes; reserve room for " N", ".md" and storage's
# temp-file wrapper (".<name>.<12hex>.tmp", +23 bytes).
_MAX_STEM_BYTES = 200


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _trim(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip(". ")


class DocumentStore:
    def __init__(self, projects: ProjectStore):
        self.projects = projects
        self._lock = threading.RLock()

    def _docs_dir(self, slug: str) -> Path:
        return self.projects.project_dir(slug) / "documents"

    def _path(self, slug: str, file: str) -> Path:
        return self._docs_dir(slug) / file

    def _snapshot(self, slug: str, file: str) -> None:
        save_history(self._path(slug, file), self.projects.project_dir(slug) / ".history")

    def _update_meta(self, slug: str, file: str, **changes) -> None:
        project = self.projects.get(slug)
        for meta in project.documents:
            if meta.file == file:
                for k, v in changes.items():
                    setattr(meta, k, v)
        self.projects.save(project)

    def create(self, slug: str, name: str, doc_type: str, markdown: str) -> DocumentMeta:
        with self._lock:
            project = self.projects.get(slug)
            used = {m.file.casefold() for m in project.documents}
            stem = _trim(sanitize(name), _MAX_STEM_BYTES) or "untitled"
            file, n = f"{stem}.md", 1
            while file.casefold() in used or self._path(slug, file).exists():
                n += 1
                file = f"{stem} {n}.md"
            atomic_write_text(self._path(slug, file), markdown, expect=None)
            meta = DocumentMeta(
                name=name.strip() or stem, file=file, doc_type=doc_type, generated_at=_now()
            )
            project.documents.append(meta)
            self.projects.save(project)
            return meta

    def read(self, slug: str, file: str) -> tuple[str, Fingerprint]:
        return read_text(self._path(slug, file))

    def save(self, slug: str, file: str, text: str, expect: Fingerprint) -> Fingerprint:
        with self._lock:
            self._snapshot(slug, file)
            fp = atomic_write_text(self._path(slug, file), text, expect=expect)
            self._update_meta(slug, file, edited_at=_now())
            return fp

    def replace_generated(self, slug: str, file: str, text: str) -> Fingerprint:
        with self._lock:
            self._snapshot(slug, file)
            fp = atomic_write_text(self._path(slug, file), text)
            self._update_meta(slug, file, generated_at=_now(), edited_at=None)
            return fp

    def delete(self, slug: str, file: str) -> None:
        with self._lock:
            self._snapshot(slug, file)
            self._path(slug, file).unlink(missing_ok=True)
            project = self.projects.get(slug)
            project.documents = [m for m in project.documents if m.file != file]
            self.projects.save(project)

    def _export_path(self, slug: str, file: str, suffix: str) -> Path:
        return self.projects.project_dir(slug) / "exports" / f"{Path(file).stem}{suffix}"

    def export_md(self, slug: str, file: str) -> Path:
        text, _ = self.read(slug, file)
        out = self._export_path(slug, file, ".md")
        atomic_write_text(out, text)
        return out

    def export_pdf(self, slug: str, file: str) -> Path:
        text, _ = self.read(slug, file)
        out = self._export_path(slug, file, ".pdf")
        markdown_to_pdf(text, out)
        return out
