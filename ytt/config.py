"""App configuration: model catalogs, data-root pointer, paths, and settings."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ytt.storage import atomic_write_json, read_json

WHISPER_MODELS: dict[str, str] = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
}

CLAUDE_MODELS: dict[str, str] = {"Sonnet": "claude-sonnet-5-5", "Opus": "claude-opus-5-5"}

LANGUAGES: dict[str, str] = {
    "auto": "Auto-detect",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "sv": "Swedish",
    "pl": "Polish",
    "ru": "Russian",
    "uk": "Ukrainian",
    "tr": "Turkish",
    "ar": "Arabic",
    "he": "Hebrew",
    "hi": "Hindi",
    "id": "Indonesian",
    "vi": "Vietnamese",
    "th": "Thai",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}


def app_support_dir() -> Path:
    env = os.environ.get("YTT_APP_SUPPORT")
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "YouTube Transcriber"


def _pointer_file() -> Path:
    return app_support_dir() / "config.json"


def load_data_root() -> Path:
    env = os.environ.get("YTT_DATA_ROOT")
    if env:
        return Path(env)
    try:
        data, _ = read_json(_pointer_file())
        root = data.get("data_root")
        if root:
            return Path(root)
    except (FileNotFoundError, ValueError, AttributeError):
        pass
    return Path.home() / "YouTube Transcriber"


def set_data_root(path: Path) -> None:
    atomic_write_json(_pointer_file(), {"data_root": str(path)})


@dataclass
class Paths:
    root: Path

    @property
    def transcripts(self) -> Path:
        return self.root / "transcripts"

    @property
    def index(self) -> Path:
        return self.transcripts / "index.json"

    @property
    def doc_types(self) -> Path:
        return self.root / "document-types"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def audio_cache(self) -> Path:
        return self.root / ".cache" / "audio"

    def ensure_layout(self) -> None:
        for d in (self.transcripts, self.doc_types, self.projects, self.audio_cache):
            d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    whisper_model: str = "large-v3-turbo"
    default_language: str = "auto"
    claude_mode: str = "subscription"
    api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"


def load_settings(paths: Paths) -> Settings:
    try:
        data, _ = read_json(paths.settings_file)
    except (FileNotFoundError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save_settings(paths: Paths, s: Settings) -> None:
    atomic_write_json(paths.settings_file, asdict(s))
    os.chmod(paths.settings_file, 0o600)
