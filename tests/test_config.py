import json

from ytt.config import (
    CLAUDE_MODELS,
    LANGUAGES,
    Paths,
    Settings,
    app_support_dir,
    load_data_root,
    load_settings,
    save_settings,
    set_data_root,
)


def test_settings_defaults_and_roundtrip(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure_layout()
    assert load_settings(paths) == Settings()
    s = Settings(claude_mode="api_key", api_key="sk-x")
    save_settings(paths, s)
    assert load_settings(paths) == s
    assert oct(paths.settings_file.stat().st_mode)[-3:] == "600"


def test_settings_ignores_unknown_keys_and_fills_missing(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure_layout()
    paths.settings_file.write_text(json.dumps({"api_key": "k", "bogus": 1}))
    assert load_settings(paths) == Settings(api_key="k")


def test_data_root_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("YTT_DATA_ROOT")
    set_data_root(tmp_path / "elsewhere")
    assert load_data_root() == tmp_path / "elsewhere"


def test_data_root_env_wins_and_default(tmp_path, monkeypatch):
    assert load_data_root() == tmp_path / "data_root"
    monkeypatch.delenv("YTT_DATA_ROOT")
    assert load_data_root().name == "YouTube Transcriber"


def test_paths_layout(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure_layout()
    assert paths.index == tmp_path / "transcripts" / "index.json"
    assert paths.audio_cache == tmp_path / ".cache" / "audio"
    assert paths.doc_types == tmp_path / "document-types"
    for d in (paths.transcripts, paths.doc_types, paths.projects, paths.audio_cache):
        assert d.is_dir()


def test_constants():
    assert app_support_dir().name == "app_support"
    assert CLAUDE_MODELS == {"Sonnet": "claude-sonnet-5-5", "Opus": "claude-opus-5-5"}
    assert next(iter(LANGUAGES.items())) == ("auto", "Auto-detect")
