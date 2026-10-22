"""Safe file storage: atomic writes with optional conflict detection."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Fingerprint:
    mtime_ns: int
    size: int
    sha256: str


class ConflictError(Exception):
    def __init__(self, path: Path, current: Fingerprint | None):
        super().__init__(f"{path} changed on disk")
        self.path = path
        self.current = current


class _Any:
    def __repr__(self) -> str:
        return "ANY"


ANY = _Any()

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def file_lock(path: Path) -> threading.Lock:
    key = Path(path).resolve()
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def fingerprint(path: Path) -> Fingerprint | None:
    path = Path(path)
    try:
        data = path.read_bytes()
        st = path.stat()
    except FileNotFoundError:
        return None
    return Fingerprint(st.st_mtime_ns, st.st_size, hashlib.sha256(data).hexdigest())


def read_text(path: Path) -> tuple[str, Fingerprint]:
    path = Path(path)
    data = path.read_bytes()
    st = path.stat()
    fp = Fingerprint(st.st_mtime_ns, st.st_size, hashlib.sha256(data).hexdigest())
    return data.decode("utf-8"), fp


def read_json(path: Path) -> tuple[Any, Fingerprint]:
    text, fp = read_text(path)
    return json.loads(text), fp


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_text(
    path: Path, text: str, *, expect: Fingerprint | None | object = ANY
) -> Fingerprint:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        if expect is not ANY:
            current = fingerprint(path)
            if current != expect:
                raise ConflictError(path, current)
        tmp = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        _fsync_dir(path.parent)
        fp = fingerprint(path)
        assert fp is not None
        return fp


def atomic_write_json(
    path: Path, obj: Any, *, expect: Fingerprint | None | object = ANY
) -> Fingerprint:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return atomic_write_text(path, text, expect=expect)


def save_history(path: Path, history_dir: Path, keep: int = 20) -> Path | None:
    path = Path(path)
    if not path.exists():
        return None
    folder = Path(history_dir) / path.stem
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = folder / f"{stamp}{path.suffix}"
    shutil.copy2(path, dest)
    for old in sorted(folder.iterdir())[:-keep] if keep > 0 else sorted(folder.iterdir()):
        old.unlink(missing_ok=True)
    return dest
