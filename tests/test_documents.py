import pytest

from ytt.config import Paths
from ytt.documents import DocumentStore
from ytt.pdf import PdfUnavailable, markdown_to_pdf
from ytt.projects import ProjectStore
from ytt.storage import ConflictError


@pytest.fixture
def docs(tmp_path):
    paths = Paths(tmp_path / "root")
    paths.ensure_layout()
    return DocumentStore(ProjectStore(paths))


@pytest.fixture
def slug(docs):
    return docs.projects.create("Proj").slug


def test_save_detects_outside_edit(docs, slug):
    meta = docs.create(slug, "Guide", "step-by-step-guide", "# G")
    text, fp = docs.read(slug, meta.file)
    (docs.projects.project_dir(slug) / "documents" / meta.file).write_text("# edited in VS Code")
    with pytest.raises(ConflictError):
        docs.save(slug, meta.file, "# mine", expect=fp)


def test_save_keeps_history(docs, slug):
    meta = docs.create(slug, "Guide", "t", "v1")
    _, fp = docs.read(slug, meta.file)
    fp = docs.save(slug, meta.file, "v2", expect=fp)
    docs.replace_generated(slug, meta.file, "v3")
    hist = sorted((docs.projects.project_dir(slug) / ".history" / "Guide").iterdir())
    assert [h.read_text() for h in hist] == ["v1", "v2"]


def test_unique_names(docs, slug):
    assert docs.create(slug, "Guide", "t", "a").file == "Guide.md"
    assert docs.create(slug, "Guide", "t", "b").file == "Guide 2.md"


def test_long_name_fits_filename_limit(docs, slug):
    meta = docs.create(slug, "🎉" * 80, "t", "a")
    assert len(meta.file[: -len(".md")].encode()) <= 200
    assert len(meta.file.encode()) + 23 <= 255


def test_meta_timestamps_and_delete(docs, slug):
    meta = docs.create(slug, "Guide", "t", "v1")
    _, fp = docs.read(slug, meta.file)
    docs.save(slug, meta.file, "v2", expect=fp)
    assert docs.projects.get(slug).documents[0].edited_at
    docs.replace_generated(slug, meta.file, "v3")
    assert docs.projects.get(slug).documents[0].edited_at is None
    docs.delete(slug, meta.file)
    assert docs.projects.get(slug).documents == []
    assert not (docs.projects.project_dir(slug) / "documents" / meta.file).exists()
    hist = sorted((docs.projects.project_dir(slug) / ".history" / "Guide").iterdir())
    assert hist[-1].read_text() == "v3"


def test_export_md_and_pdf(docs, slug):
    meta = docs.create(slug, "Guide", "t", "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert docs.export_md(slug, meta.file).read_text().startswith("# Title")
    pdf = docs.export_pdf(slug, meta.file)
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_pdf_unavailable_message(tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "weasyprint":
            raise OSError("no pango")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(PdfUnavailable, match="brew install pango"):
        markdown_to_pdf("# x", tmp_path / "a.pdf")


def test_pdf_filesystem_error_is_not_pdf_unavailable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(OSError) as ei:
        markdown_to_pdf("# x", blocker / "a.pdf")
    assert not isinstance(ei.value, PdfUnavailable)


def _conflict_once(docs, slug, monkeypatch):
    real = docs.projects.save
    calls = []

    def save(project):
        if not calls:
            calls.append(1)
            f = docs.projects.project_dir(slug) / "project.json"
            f.write_text(f.read_text() + "\n")  # outside edit
        return real(project)

    monkeypatch.setattr(docs.projects, "save", save)


def test_save_survives_project_json_conflict(docs, slug, monkeypatch):
    meta = docs.create(slug, "Guide", "t", "v1")
    _, fp = docs.read(slug, meta.file)
    _conflict_once(docs, slug, monkeypatch)
    new_fp = docs.save(slug, meta.file, "v2", expect=fp)
    assert docs.read(slug, meta.file) == ("v2", new_fp)
    assert docs.projects.get(slug).documents[0].edited_at


def test_delete_survives_project_json_conflict(docs, slug, monkeypatch):
    meta = docs.create(slug, "Guide", "t", "v1")
    _conflict_once(docs, slug, monkeypatch)
    docs.delete(slug, meta.file)
    assert docs.projects.get(slug).documents == []


def test_rejected_save_adds_no_history(docs, slug):
    meta = docs.create(slug, "Guide", "t", "v1")
    _, fp = docs.read(slug, meta.file)
    (docs.projects.project_dir(slug) / "documents" / meta.file).write_text("x")
    with pytest.raises(ConflictError):
        docs.save(slug, meta.file, "mine", expect=fp)
    assert not (docs.projects.project_dir(slug) / ".history" / "Guide").exists()
