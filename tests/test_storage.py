import os

import pytest

from ytt.storage import (
    ConflictError,
    atomic_write_json,
    atomic_write_text,
    read_json,
    save_history,
)


def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "a" / "x.json"
    fp = atomic_write_json(p, {"k": "é"})
    data, fp2 = read_json(p)
    assert data == {"k": "é"} and fp == fp2
    assert not list(p.parent.glob("*.tmp"))


def test_write_with_stale_expect_raises(tmp_path):
    p = tmp_path / "doc.md"
    fp = atomic_write_text(p, "one")
    p.write_text("edited outside")
    with pytest.raises(ConflictError):
        atomic_write_text(p, "two", expect=fp)
    assert p.read_text() == "edited outside"


def test_expect_none_requires_absent(tmp_path):
    p = tmp_path / "n.md"; p.write_text("x")
    with pytest.raises(ConflictError):
        atomic_write_text(p, "y", expect=None)


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"; atomic_write_text(p, "old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_text(p, "new")
    assert p.read_text() == "old"
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_history_keeps_last_n(tmp_path):
    p = tmp_path / "d.md"; h = tmp_path / ".history"
    for i in range(5):
        atomic_write_text(p, f"v{i}"); save_history(p, h, keep=3)
    kept = sorted((h / "d").iterdir())
    assert [k.read_text() for k in kept] == ["v2", "v3", "v4"]
