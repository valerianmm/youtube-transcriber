import pytest


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("YTT_APP_SUPPORT", str(tmp_path / "app_support"))
    monkeypatch.setenv("YTT_DATA_ROOT", str(tmp_path / "data_root"))
    monkeypatch.setenv("YTT_TRASH_DIR", str(tmp_path / "trash"))
