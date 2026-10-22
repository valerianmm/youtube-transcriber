"""Single-instance guard using an OS file lock (released automatically on process death)."""
from __future__ import annotations

import fcntl
import os

from ytt.config import app_support_dir
from ytt.storage import atomic_write_json, read_json


def _lock_path():
    return app_support_dir() / "instance.lock"


def _info_path():
    return app_support_dir() / "instance.json"


class InstanceLock:
    def __init__(self) -> None:
        self._fd: int | None = None

    def acquire(self, port: int) -> bool:
        if self._fd is not None:
            return True
        _lock_path().parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        atomic_write_json(_info_path(), {"port": port})
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def running_port() -> int | None:
    """Port of the running instance, or None if no one holds the lock."""
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pass  # held by someone else
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
    finally:
        os.close(fd)
    try:
        data, _ = read_json(_info_path())
        return int(data["port"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None
