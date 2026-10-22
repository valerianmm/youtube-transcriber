from datetime import date

import pytest
from nicegui import ui

from tests import ui_support
from tests.test_app_state import FakeBackend
from ytt.claude_client import ClaudeError
from ytt.pdf import PdfUnavailable
from ytt.projects import Source
from ytt.transcribe import TranscriptResult
from ytt.urls import video_url
from ytt.youtube import VideoInfo


def vi(vid):
    return VideoInfo(id=vid, title=f"Title {vid}", url=video_url(vid), channel="Ch",
                     upload_date=date(2024, 1, 1), duration=60)


@pytest.fixture
def state(user):
    st = ui_support.STATE
    st.doctypes.seed_defaults()
    return st


def make_project(state, name, done=(), queued=()):
    slug = state.projects.create(name).slug
    infos = [vi(v) for v in (*done, *queued)]
    state.projects.add_source(slug, Source(kind="playlist", url="https://www.youtube.com/playlist?list=PLx",
                                           title="List", videos=infos))
    for v in done:
        state.library.write_transcript(vi(v), TranscriptResult(text=f"text {v}", language="en"), "large-v3-turbo")
    return slug


@pytest.fixture
def ready_slug(state):
    return make_project(state, "Ready", done=["r1"])


@pytest.fixture
def fake_backend(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr("ytt.app_state.backend_for", lambda s: backend)
    return backend


def editor(user, marker="doc-editor"):
    return user.find(marker=marker).elements.pop()


async def generate(user, slug):
    await user.open(f"/project/{slug}")
    user.find("+ Generate new…").click()
    user.find(marker="gen-go").click()
    await user.should_see(marker="doc-editor")


async def test_generate_disabled_until_ready(user, state):
    slug = make_project(state, "Busy", done=["d1"], queued=["q1"])
    await user.open(f"/project/{slug}")
    user.find("+ Generate new…").click()
    await user.should_see("Waiting for 1 videos to finish")
    await user.should_see("The transcripts will be sent to Claude")
    await user.should_see("Title q1")
    assert not user.find(marker="gen-go").elements.pop().enabled


async def test_generate_edit_save_export(user, state, ready_slug, fake_backend):
    await generate(user, ready_slug)
    meta = state.projects.get(ready_slug).documents[0]
    assert meta.name == "Step-by-step guide"
    await user.should_see("Step-by-step guide")
    ed = editor(user)
    assert "## Sources" in ed.value and "Title r1" in ed.value
    ed.set_value(ed.value + "\nmy edit")
    await user.should_see("●")
    user.find(marker="doc-save").click()
    await user.should_not_see("●")
    text, _ = state.documents.read(ready_slug, meta.file)
    assert text.endswith("my edit")
    assert state.projects.get(ready_slug).documents[0].edited_at
    user.find(marker="doc-export-md").click()
    await user.should_see("Exported the saved version")
    exported = state.projects.project_dir(ready_slug) / "exports" / f"{meta.file[:-3]}.md"
    assert exported.read_text().endswith("my edit")


async def test_export_pdf_unavailable_shown(user, state, ready_slug, fake_backend, monkeypatch):
    await generate(user, ready_slug)

    def boom(text, out):
        raise PdfUnavailable("PDF export needs pango: run `brew install pango`")

    monkeypatch.setattr("ytt.documents.markdown_to_pdf", boom)
    user.find(marker="doc-export-pdf").click()
    await user.should_see("PDF export needs pango")


async def test_save_conflict_dialog(user, state, ready_slug, fake_backend):
    await generate(user, ready_slug)
    meta = state.projects.get(ready_slug).documents[0]
    path = state.documents._path(ready_slug, meta.file)
    path.write_text("outside edit")
    ed = editor(user)
    ed.set_value("mine")
    user.find(marker="doc-save").click()
    await user.should_see("Changed on disk")
    user.find(marker="conflict-overwrite").click()
    await user.should_see("Saved")
    assert path.read_text() == "mine"


async def test_conflict_reload_discards_after_confirm(user, state, ready_slug, fake_backend):
    await generate(user, ready_slug)
    meta = state.projects.get(ready_slug).documents[0]
    state.documents._path(ready_slug, meta.file).write_text("outside edit")
    ed = editor(user)
    ed.set_value("mine")
    user.find(marker="doc-save").click()
    user.find("Reload from disk").click()
    await user.should_see("Your unsaved changes will be discarded.")
    user.find(marker="confirm-action").click()
    await user.should_not_see("●")
    assert ed.value == "outside edit"


async def test_claude_error_shown(user, state, ready_slug, monkeypatch):
    monkeypatch.setattr("ytt.app_state.backend_for", lambda s: FakeBackend(ClaudeError("nope nope")))
    await user.open(f"/project/{ready_slug}")
    user.find("+ Generate new…").click()
    user.find(marker="gen-go").click()
    await user.should_see("nope nope")
    await user.should_see("Open Settings")
    assert state.projects.get(ready_slug).documents == []


async def test_regenerate_replaces_and_confirms(user, state, ready_slug, fake_backend):
    await generate(user, ready_slug)
    editor(user).set_value("edited")
    user.find(marker="doc-regenerate").click()
    await user.should_see("The current version goes to history")
    user.find(marker="confirm-action").click()
    await user.should_see("## Sources")
    assert len(fake_backend.prompts) == 2
    assert len(state.projects.get(ready_slug).documents) == 1
    assert state.projects.get(ready_slug).documents[0].edited_at is None


async def test_delete_document(user, state, ready_slug, fake_backend):
    await generate(user, ready_slug)
    user.find(marker="doc-delete").click()
    user.find(marker="confirm-action").click()
    await user.should_see("No documents yet")
    assert state.projects.get(ready_slug).documents == []


async def test_new_doctype_appears_in_generate_dialog(user, state, ready_slug):
    await user.open("/doc-types")
    user.find("+ New type").click()
    user.find(marker="new-type-name").type("Meeting notes")
    user.find(marker="new-type-create").click()
    await user.should_see(marker="doctype-editor")
    await user.open(f"/project/{ready_slug}")
    user.find("+ Generate new…").click()
    user.find(marker="gen-type").click()
    await user.should_see("Meeting notes")


async def test_doctype_save_and_conflict(user, state):
    await user.open("/doc-types")
    user.find(marker="type-summary").click()
    await user.should_see(marker="doctype-editor")
    user.find(marker="doctype-description").type("line1\nline2")
    editor(user, "doctype-editor").set_value("## Structure\nnew body")
    user.find(marker="doctype-save").click()
    dt, fp = state.doctypes.get("summary")
    assert dt.body.startswith("## Structure\nnew body") and "\n" not in dt.description
    # outside edit, then Save -> conflict -> Overwrite
    state.doctypes._file("summary").write_text("---\nname: Summary\ndescription: x\n---\noutside")
    editor(user, "doctype-editor").set_value("mine")
    user.find(marker="doctype-save").click()
    await user.should_see("Changed on disk")
    user.find(marker="conflict-overwrite").click()
    assert state.doctypes.get("summary")[0].body == "mine"


async def test_doctype_duplicate_rename_delete(user, state):
    await user.open("/doc-types")
    user.find(marker="type-summary").click()
    user.find(marker="type-duplicate-summary").click()
    await user.should_see("Summary (copy)")
    assert "Summary (copy)" in [d.name for d in state.doctypes.list()]
    user.find(marker="type-rename-summary").click()
    user.find(marker="rename-type-input").clear().type("Briefing")
    user.find(marker="rename-type-confirm").click()
    await user.should_see(marker="type-delete-briefing")
    assert "Briefing" in [d.name for d in state.doctypes.list()]
    user.find(marker="type-delete-briefing").click()
    user.find(marker="confirm-action").click()
    await user.should_not_see(marker="type-delete-briefing")
    assert [d.name for d in state.doctypes.list()] == ["Step-by-step guide", "Summary (copy)"]
