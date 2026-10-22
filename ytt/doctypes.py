"""Editable document types: markdown files with a small front-matter header."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ytt.config import Paths
from ytt.library import sanitize
from ytt.storage import Fingerprint, atomic_write_text, file_lock, read_text

_COMMON_INSTRUCTIONS = """## Instructions
- Merge overlapping content across videos; do not repeat it once per video.
- Write a standalone document, not "in this video..." commentary.
- Use only what the transcripts say; do not add outside knowledge.
- Do not include timestamps.
- Do not include a Sources section; the app adds it.
"""

DEFAULT_SUMMARY = """## Structure
- Title
- TL;DR: 3-5 bullets
- Key ideas: one `###` heading per idea
- Notable details and numbers
- Takeaways

""" + _COMMON_INSTRUCTIONS

DEFAULT_GUIDE = """## Structure
- Title
- Who this is for and prerequisites
- Numbered steps: one `##` heading per phase, numbered actions inside it, commands in code blocks
- Common mistakes
- Next steps

""" + _COMMON_INSTRUCTIONS

DEFAULTS = [
    ("Summary", "A concise overview of the key ideas.", DEFAULT_SUMMARY),
    ("Step-by-step guide", "A practical guide someone can follow from start to finish.", DEFAULT_GUIDE),
]


@dataclass
class DocType:
    slug: str
    name: str
    description: str
    body: str


def make_slug(name: str) -> str:
    return sanitize(name, 60).lower().replace(" ", "-")


def parse_doctype(slug: str, text: str) -> DocType:
    plain = DocType(slug, slug, "", text)
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return plain
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return plain
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("name", "description"):
            meta[key.strip()] = value.strip()
    return DocType(slug, meta.get("name") or slug, meta.get("description", ""), "\n".join(lines[end + 1 :]))


def render_doctype(dt: DocType) -> str:
    return f"---\nname: {dt.name}\ndescription: {dt.description}\n---\n{dt.body}"


class DocTypeStore:
    def __init__(self, paths: Paths):
        self.paths = paths

    @property
    def _dir(self):
        return self.paths.doc_types

    def _file(self, slug: str):
        return self._dir / f"{slug}.md"

    def list(self) -> list[DocType]:
        if not self._dir.is_dir():
            return []
        out = []
        for f in self._dir.glob("*.md"):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                text, _ = read_text(f)
            except (OSError, UnicodeDecodeError):
                continue
            out.append(parse_doctype(f.stem, text))
        return sorted(out, key=lambda d: d.name.lower())

    def get(self, slug: str) -> tuple[DocType, Fingerprint]:
        text, fp = read_text(self._file(slug))
        return parse_doctype(slug, text), fp

    def save(self, dt: DocType, expect: Fingerprint) -> Fingerprint:
        return atomic_write_text(self._file(dt.slug), render_doctype(dt), expect=expect)

    def _check_new_name(self, name: str, ignore_slug: str | None = None) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Name cannot be empty")
        slug = make_slug(name)
        for d in self.list():
            if d.slug != ignore_slug and (d.name.lower() == name.lower() or d.slug == slug):
                raise ValueError(f"A document type named '{name}' already exists")
        if slug != ignore_slug and self._file(slug).exists():
            raise ValueError(f"A document type named '{name}' already exists")
        return slug

    def create(self, name: str) -> DocType:
        with file_lock(self._dir):
            slug = self._check_new_name(name)
            dt = DocType(slug, name.strip(), "", "## Structure\n\n## Instructions\n")
            atomic_write_text(self._file(slug), render_doctype(dt), expect=None)
        return dt

    def duplicate(self, slug: str) -> DocType:
        src, _ = self.get(slug)
        with file_lock(self._dir):
            name = f"{src.name} (copy)"
            new_slug = self._check_new_name(name)
            dt = DocType(new_slug, name, src.description, src.body)
            atomic_write_text(self._file(new_slug), render_doctype(dt), expect=None)
        return dt

    def rename(self, slug: str, new_name: str) -> DocType:
        with file_lock(self._dir):
            old, _ = self.get(slug)
            new_slug = self._check_new_name(new_name, ignore_slug=slug)
            dt = DocType(new_slug, new_name.strip(), old.description, old.body)
            if new_slug == slug:
                atomic_write_text(self._file(slug), render_doctype(dt))
            else:
                atomic_write_text(self._file(new_slug), render_doctype(dt), expect=None)
                self._file(slug).unlink(missing_ok=True)
        return dt

    def delete(self, slug: str) -> None:
        trash = self._dir / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self._file(slug).replace(trash / f"{slug} {stamp}.md")

    def seed_defaults(self) -> None:
        if self._dir.is_dir() and any(f.suffix == ".md" for f in self._dir.glob("*.md")):
            return
        for name, description, body in DEFAULTS:
            dt = DocType(make_slug(name), name, description, body)
            atomic_write_text(self._file(dt.slug), render_doctype(dt), expect=None)
