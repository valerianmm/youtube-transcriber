import pytest

from ytt.config import Paths
from ytt.doctypes import DocType, DocTypeStore, parse_doctype, render_doctype
from ytt.storage import ConflictError, atomic_write_text


@pytest.fixture
def store(tmp_path):
    paths = Paths(tmp_path / "root")
    paths.ensure_layout()
    return DocTypeStore(paths)


def test_roundtrip():
    dt = DocType("summary", "Summary", "Short overview", "## Structure\n- TL;DR\n")
    assert parse_doctype("summary", render_doctype(dt)) == dt


def test_tolerates_missing_front_matter():
    dt = parse_doctype("notes", "Just write notes.")
    assert (dt.name, dt.body) == ("notes", "Just write notes.")


def test_unclosed_front_matter_is_plain_body():
    dt = parse_doctype("x", "---\nname: Oops\nno closing")
    assert dt.name == "x" and dt.body == "---\nname: Oops\nno closing"


def test_seed_only_when_empty(store):
    store.seed_defaults()
    assert {d.slug for d in store.list()} == {"summary", "step-by-step-guide"}
    store.delete("summary")
    store.seed_defaults()
    assert {d.slug for d in store.list()} == {"step-by-step-guide"}


def test_defaults_content(store):
    store.seed_defaults()
    guide, _ = store.get("step-by-step-guide")
    assert guide.name == "Step-by-step guide"
    assert "TL;DR" in store.get("summary")[0].body


def test_create_and_duplicate_names_rejected(store):
    dt = store.create("Meeting Notes")
    assert dt.slug == "meeting-notes"
    with pytest.raises(ValueError):
        store.create("meeting notes")
    with pytest.raises(ValueError):
        store.create("   ")


def test_duplicate_rename_delete(store):
    store.seed_defaults()
    copy = store.duplicate("summary")
    assert copy.name == "Summary (copy)" and copy.slug == "summary-(copy)"
    assert store.get("summary-(copy)")[0].body == store.get("summary")[0].body

    renamed = store.rename("summary-(copy)", "Brief")
    assert renamed.slug == "brief" and renamed.name == "Brief"
    assert not (store.paths.doc_types / "summary-(copy).md").exists()
    assert store.get("brief")[0].body == renamed.body
    with pytest.raises(ValueError):
        store.rename("brief", "SUMMARY")

    store.delete("brief")
    assert "brief" not in {d.slug for d in store.list()}
    trash = list((store.paths.doc_types / ".trash").iterdir())
    assert len(trash) == 1 and trash[0].name.startswith("brief ")


def test_rename_case_only_keeps_file(store):
    store.create("notes")
    dt = store.rename("notes", "Notes")
    assert dt.slug == "notes" and store.get("notes")[0].name == "Notes"


def test_save_conflict_on_outside_edit(store):
    dt = store.create("Draft")
    dt, fp = store.get("draft")
    atomic_write_text(store.paths.doc_types / "draft.md", "edited outside")
    with pytest.raises(ConflictError):
        store.save(DocType(dt.slug, dt.name, dt.description, "mine"), fp)
    dt2, fp2 = store.get("draft")
    store.save(DocType(dt2.slug, "Draft", "d", "mine"), fp2)
    assert store.get("draft")[0].body == "mine"


def test_list_ignores_trash_and_non_md_and_bad_files(store):
    store.create("B")
    store.create("a")
    (store.paths.doc_types / "readme.txt").write_text("x")
    atomic_write_text(store.paths.doc_types / "weird.md", "---\nname\n---\nbody")
    names = [d.name for d in store.list()]
    assert names == ["a", "B", "weird"]
    store.delete("a")
    assert [d.slug for d in store.list()] == ["b", "weird"]
