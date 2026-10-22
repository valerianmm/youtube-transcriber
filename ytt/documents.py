"""Project documents: create, edit with conflict detection, history, export."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from ytt.library import sanitize, trim_utf8_bytes
from ytt.pdf import markdown_to_pdf
from ytt.projects import DocumentMeta, ProjectStore
from ytt.storage import (
    ANY,
    ConflictError,
    Fingerprint,
    fingerprint,
    atomic_write_text,
    read_text,
    save_history,
)

# macOS filename limit is 255 bytes; reserve room for " N", ".md" and storage's
# temp-file wrapper (".<name>.<12hex>.tmp", +23 bytes).
_MAX_STEM_BYTES = 200


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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

    def _update_project(self, slug: str, mutate) -> None:
        """Apply mutate(project) and save; retry once if project.json changed meanwhile."""
        for attempt in (1, 2):
            project = self.projects.get(slug)
            mutate(project)
            try:
                self.projects.save(project)
                return
            except ConflictError:
                if attempt == 2:
                    raise

    def _update_meta(self, slug: str, file: str, **changes) -> None:
        def mutate(project):
            for meta in project.documents:
                if meta.file == file:
                    for k, v in changes.items():
                        setattr(meta, k, v)

        self._update_project(slug, mutate)

    def create(self, slug: str, name: str, doc_type: str, markdown: str) -> DocumentMeta:
        with self._lock:
            project = self.projects.get(slug)
            used = {m.file.casefold() for m in project.documents}
            stem = trim_utf8_bytes(sanitize(name), _MAX_STEM_BYTES) or "untitled"
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

    def save(self, slug: str, file: str, text: str, expect: Fingerprint | object) -> Fingerprint:
        with self._lock:
            path = self._path(slug, file)
            current = fingerprint(path)
            if expect is not ANY and current != expect:
                raise ConflictError(path, current)
            self._snapshot(slug, file)
            fp = atomic_write_text(path, text, expect=expect)
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
            self._update_project(
                slug,
                lambda p: setattr(p, "documents", [m for m in p.documents if m.file != file]),
            )

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
