import sys

import pytest


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("YTT_APP_SUPPORT", str(tmp_path / "app_support"))
    monkeypatch.setenv("YTT_DATA_ROOT", str(tmp_path / "data_root"))
    monkeypatch.setenv("YTT_TRASH_DIR", str(tmp_path / "trash"))


class FakeYdl:
    """Stand-in for yt_dlp.YoutubeDL; tests set canned extract_info results."""

    def __init__(self):
        self.info = None
        self.calls = []
        self.opts = None

    def playlist(self, entries):
        self.info = {"entries": entries}

    def __call__(self, opts):
        self.opts = opts
        outer = self

        class _Ydl:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                outer.calls.append((url, download))
                if isinstance(outer.info, BaseException):
                    raise outer.info
                return outer.info

        return _Ydl()


@pytest.fixture
def fake_ydl(monkeypatch):
    fake = FakeYdl()
    monkeypatch.setattr("ytt.youtube._ydl", fake)
    return fake


@pytest.fixture(autouse=True)
def _keep_ytt_modules():
    """NiceGUI's test reset pops page-defining modules (and parents, incl. `ytt`) from
    sys.modules; put back only what it removed."""
    saved = {k: v for k, v in sys.modules.items() if k == "ytt" or k.startswith("ytt.")}
    yield
    for k in saved.keys() - sys.modules.keys():
        sys.modules[k] = saved[k]
