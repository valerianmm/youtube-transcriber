import json

import pytest

from ytt.config import Paths
from ytt.projects import ProjectStore, Source
from ytt.storage import ConflictError
from ytt.youtube import VideoInfo


@pytest.fixture
def store(tmp_path):
    paths = Paths(tmp_path / "root")
    paths.ensure_layout()
    return ProjectStore(paths)


def vi(vid):
    return VideoInfo(id=vid, title=f"T {vid}", url=f"https://youtu.be/{vid}")


def src(sid, videos, language=None):
    return Source(id=sid, kind="playlist", url="https://x/" + sid, title=sid,
                  language=language, videos=videos)


def test_create_rejects_bad_names(store):
    store.create("Kubernetes")
    for bad in ["", "   ", "kubernetes"]:
        with pytest.raises(ValueError):
            store.create(bad)
    p = store.create("a/b: c")
    assert p.name == "a/b: c" and "/" not in p.slug and store.project_dir(p.slug).is_dir()


def test_rename_moves_folder_and_keeps_contents(store):
    p = store.create("Old"); (store.project_dir(p.slug) / "documents" / "x.md").write_text("hi")
    q = store.rename(p.slug, "New")
    assert (store.project_dir(q.slug) / "documents" / "x.md").read_text() == "hi"
    assert not store.project_dir(p.slug).exists()
    assert store.get(q.slug).name == "New"


def test_videos_dedup_and_forced_language(store):
    p = store.create("P")
    store.add_source(p.slug, src("s1", [vi("a"), vi("b")], language="de"))
    p = store.add_source(p.slug, src("s2", [vi("b"), vi("c")]))
    assert [v.id for v in p.videos()] == ["a", "b", "c"]
    assert p.forced_language_for("b") == "de" and p.forced_language_for("c") is None


def test_merge_new_videos_only_returns_unseen(store):
    p = store.create("P")
    store.add_source(p.slug, src("s1", [vi("a"), vi("b")]))
    new = store.merge_new_videos(p.slug, "s1", [vi("b"), vi("c"), vi("c"), vi("d")])
    assert [v.id for v in new] == ["c", "d"]
    assert [v.id for v in store.get(p.slug).sources[0].videos] == ["a", "b", "c", "d"]
    assert store.merge_new_videos(p.slug, "s1", [vi("a"), vi("c")]) == []


def test_outside_edit_conflicts(store):
    p = store.get(store.create("P").slug)
    (store.project_dir(p.slug) / "project.json").write_text('{"version":1,"name":"P","language":null,"sources":[],"cancelled":[],"documents":[]} ')
    p.language = "en"
    with pytest.raises(ConflictError):
        store.save(p)


def test_delete_moves_to_trash(store, tmp_path):
    p = store.create("Gone")
    (store.project_dir(p.slug) / "documents" / "x.md").write_text("keep me")
    store.delete(p.slug)
    assert not store.project_dir(p.slug).exists()
    moved = list((tmp_path / "trash").glob("Gone *"))
    assert len(moved) == 1
    assert (moved[0] / "documents" / "x.md").read_text() == "keep me"
    assert store.list() == []


def test_roundtrip_list_and_cancelled_sorted(store):
    store.create("b"); p = store.create("A")
    p.cancelled = {"z", "a"}
    p.sources.append(src("s1", [vi("a")]))
    store.save(p)
    store.save(p)  # fingerprint refreshed after save
    data = json.loads((store.project_dir(p.slug) / "project.json").read_text())
    assert data["version"] == 1 and data["cancelled"] == ["a", "z"]
    assert [x.name for x in store.list()] == ["A", "b"]
    assert store.get(p.slug).sources[0].videos[0].id == "a"
    for sub in ("documents", "exports", ".history"):
        assert (store.project_dir(p.slug) / sub).is_dir()
