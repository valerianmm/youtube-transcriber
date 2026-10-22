from datetime import date

import pytest
from nicegui import ui

from tests import ui_support
from ytt.projects import Source
from ytt.transcribe import TranscriptResult
from ytt.urls import video_url
from ytt.youtube import VideoInfo


def vi(vid, **kw):
    return VideoInfo(id=vid, title=f"Title {vid}", url=video_url(vid), channel="Ch",
                     upload_date=date(2024, 1, 1), duration=60, **kw)


@pytest.fixture
def state(user):
    st = ui_support.STATE
    st._expand = lambda p, f, t: [vi("a1"), vi("a2"), vi("a3")]
    return st


@pytest.fixture
def slug(state):
    return state.projects.create("Demo").slug


def seed_source(state, slug, infos):
    state.projects.add_source(slug, Source(kind="playlist", url="https://www.youtube.com/playlist?list=PLx",
                                           title="My list", videos=infos))


async def test_paste_preview_add(user, state, slug):
    await user.open(f"/project/{slug}")
    user.find(marker="add-urls").type("https://www.youtube.com/playlist?list=PLx")
    user.find("Preview").click()
    await user.should_see("3 videos")
    user.find("Add 3 videos").click()
    await user.should_see("0 of 3 transcribed")
    assert len(state.projects.get(slug).videos()) == 3
    # rows are built lazily on expand
    user.find(marker="source").elements.pop().set_value(True)
    await user.should_see("queued")


async def test_invalid_url_listed_in_red(user, state, slug):
    await user.open(f"/project/{slug}")
    user.find(marker="add-urls").type("https://example.com/nope")
    user.find("Preview").click()
    await user.should_see("Invalid URL: https://example.com/nope")
    assert not user.find("Add 0 videos").elements.pop().enabled


async def test_channel_requires_start_date(user, state, slug):
    await user.open(f"/project/{slug}")
    await user.should_not_see(marker="date-from")
    user.find(marker="add-urls").type("https://www.youtube.com/@somechannel")
    await user.should_see(marker="date-from")
    user.find("Preview").click()
    await user.should_see("A start date is required for channels")
    assert state.projects.get(slug).sources == []


async def test_mismatch_badge_shown(user, state, slug):
    info = vi("m1", language="de")
    seed_source(state, slug, [info])
    state.library.write_transcript(info, TranscriptResult(text="hallo", language="en"), "large-v3-turbo")
    await user.open(f"/project/{slug}")
    user.find(marker="source").elements.pop().set_value(True)
    await user.should_see("⚠")
    await user.should_see("Whisper heard en, YouTube says de")
    await user.should_see("done")


async def test_failed_status_shows_reason(user, state, slug):
    info = vi("f1")
    seed_source(state, slug, [info])
    state.library.mark_failed(info, "Private video")
    await user.open(f"/project/{slug}")
    user.find(marker="source").elements.pop().set_value(True)
    await user.should_see("failed: Private video")


async def test_retranscribe_dialog_lists_affected_projects(user, state, slug):
    info = vi("r1")
    seed_source(state, slug, [info])
    other = state.projects.create("Other").slug
    seed_source(state, other, [info])
    await user.open(f"/project/{slug}")
    user.find(marker="source").elements.pop().set_value(True)
    user.find(kind=ui.menu_item).click()
    await user.should_see("This replaces the transcript in every project that uses this video")
    await user.should_see("• Demo")
    await user.should_see("• Other")


async def test_unknown_project(user):
    await user.open("/project/nope")
    await user.should_see("Project not found")


async def test_delete_moves_to_trash_and_navigates(user, state, slug):
    await user.open(f"/project/{slug}")
    user.find("Delete").click()
    await user.should_see("moves the project folder to the Trash")
    user.find(marker="delete-confirm").click()
    await user.should_see("Create your first project")
    assert state.projects.list() == []


async def test_rename(user, state, slug):
    await user.open(f"/project/{slug}")
    user.find("Rename").click()
    user.find(marker="rename-input").elements.pop().set_value("Renamed")
    user.find("Save").click()
    await user.should_see("Renamed")
    assert [p.name for p in state.projects.list()] == ["Renamed"]


async def test_retranscribe_while_queued_tells_user(user, state, slug, monkeypatch):
    seed_source(state, slug, [vi("r1")])
    monkeypatch.setattr(state.queue, "stage_of", lambda vid: "queued")
    await user.open(f"/project/{slug}")
    user.find(marker="source").elements.pop().set_value(True)
    user.find(kind=ui.menu_item).click()
    user.find(marker="retranscribe-confirm").click()
    await user.should_see("This video is already in the queue; re-transcribe it again after it finishes.")


async def test_project_conflict_on_save_is_shown(user, state, slug, monkeypatch):
    from ytt.storage import ConflictError

    def conflict(_slug):
        raise ConflictError(state.projects._file(slug), None)

    monkeypatch.setattr(state, "retry_failed", conflict)
    await user.open(f"/project/{slug}")
    user.find("Retry failed").click()
    await user.should_see("This project changed on disk — reload the page and try again.")


async def test_unexpected_handler_error_is_shown(user, state, slug, monkeypatch, caplog):
    def boom(_slug):
        raise OSError("disk full")

    monkeypatch.setattr(state, "cancel_queued", boom)
    await user.open(f"/project/{slug}")
    user.find("Cancel queued").click()
    await user.should_see("Something went wrong: disk full")
    caplog.clear()  # NiceGUI still logs the traceback, as it should
