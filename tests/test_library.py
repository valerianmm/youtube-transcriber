from dataclasses import replace
from datetime import date

import pytest

from ytt.config import Paths
from ytt.library import Library, languages_mismatch, sanitize
from ytt.transcribe import TranscriptResult
from ytt.youtube import VideoInfo


@pytest.fixture
def lib(tmp_path):
    paths = Paths(tmp_path / "root")
    paths.ensure_layout()
    return Library(paths)


@pytest.fixture
def info():
    return VideoInfo(
        id="abc123", title="My Video", url="https://youtu.be/abc123",
        channel="Chan", upload_date=date(2024, 3, 5), duration=60, language="en",
    )


def test_sanitize_hostile_titles():
    assert sanitize("a/b: c?*") == "ab c"
    assert sanitize("   ") == "untitled"
    assert len(sanitize("é" * 300)) == 80
    assert sanitize("🎉 Party!") == "🎉 Party!"


def test_languages_mismatch():
    assert languages_mismatch("en", "en-US") is False
    assert languages_mismatch("es", "en-US") is True
    assert languages_mismatch(None, "en") is False
    assert languages_mismatch("en", None) is False


def test_path_layout(lib, info):
    p = lib.transcript_path_for(info)
    assert p == lib.paths.transcripts / "Chan" / "2024-03-05_My Video [abc123].txt"
    p2 = lib.transcript_path_for(replace(info, channel="", upload_date=None))
    assert p2 == lib.paths.transcripts / "Unknown channel" / "unknown-date_My Video [abc123].txt"


def test_write_then_reuse(lib, info):
    rec = lib.write_transcript(info, TranscriptResult("Hello.", "en"), "large-v3-turbo")
    assert lib.is_done(info.id) and lib.read_transcript(info.id) == "Hello."
    assert rec.path.endswith(f"[{info.id}].txt")
    assert not list(lib.paths.transcripts.rglob("*.part"))
    assert rec.upload_date == "2024-03-05"
    assert Library(lib.paths).get(info.id) == rec


def test_is_done_requires_file(lib, info):
    lib.write_transcript(info, TranscriptResult("x", "en"), "large-v3-turbo")
    (lib.paths.transcripts / lib.get(info.id).path).unlink()
    assert not lib.is_done(info.id)


def test_retranscribe_replaces_and_removes_old_file(lib, info):
    lib.write_transcript(info, TranscriptResult("old", "en"), "large-v3-turbo")
    old = lib.paths.transcripts / lib.get(info.id).path
    renamed = replace(info, title="New title")
    lib.write_transcript(renamed, TranscriptResult("neu", "de"), "large-v3")
    assert not old.exists() and lib.read_transcript(info.id) == "neu"
    assert lib.get(info.id).language == "de"


def test_mismatch_flag(lib, info):
    rec = lib.write_transcript(replace(info, language="en-US"), TranscriptResult("x", "es"), "large-v3-turbo")
    assert rec.language_mismatch is True


def test_failed_does_not_overwrite_done(lib, info):
    lib.write_transcript(info, TranscriptResult("x", "en"), "large-v3-turbo")
    lib.mark_failed(info, "Private video")
    assert lib.get(info.id).status == "done"


def test_mark_failed_records_error(lib, info):
    rec = lib.mark_failed(info, "Private video")
    assert rec.status == "failed" and rec.error == "Private video" and rec.path is None
    assert not lib.is_done(info.id)


def test_cleanup_partials(lib, info):
    p = lib.transcript_path_for(info).with_suffix(".txt.part"); p.parent.mkdir(parents=True); p.write_text("half")
    assert lib.cleanup_partials() == [info.id] and not p.exists()


@pytest.mark.parametrize("title", ["日本語" * 40, "🎉" * 80])
def test_long_non_latin_titles_fit_filesystem_limits(lib, info, title):
    rec = lib.write_transcript(replace(info, title=title), TranscriptResult("x", "ja"), "large-v3-turbo")
    assert lib.read_transcript(info.id) == "x"
    assert len((lib.paths.transcripts / rec.path).name.encode()) <= 232


def test_outside_change_to_index_is_picked_up(lib, info):
    assert lib.get(info.id) is None  # caches the (missing) index
    other = Library(lib.paths)  # e.g. a second process writing the same library
    other.write_transcript(info, TranscriptResult("x", "en"), "large-v3-turbo")
    assert lib.get(info.id) is not None
    other.mark_failed(replace(info, id="zzz"), "boom")
    assert lib.records([info.id, "zzz", "nope"]).keys() == {info.id, "zzz"}


def test_returned_records_do_not_alias_the_cache(lib, info):
    lib.write_transcript(info, TranscriptResult("x", "en"), "large-v3-turbo")
    lib.get(info.id).title = "changed"
    lib.records([info.id])[info.id].status = "failed"
    assert lib.get(info.id).title == "My Video" and lib.is_done(info.id)
