"""Settings page content."""
from __future__ import annotations

from pathlib import Path

from nicegui import ui

from ytt.app_state import AppState
from ytt.config import CLAUDE_MODELS, LANGUAGES, WHISPER_MODELS, Settings

SUBSCRIPTION = "Use my Claude subscription (via Claude Code)"
API_KEY = "Use an API key"


def settings_content(state: AppState) -> None:
    s = state.settings
    ui.label("Settings").classes("text-xl")

    ui.label("Data folder").classes("font-medium")
    with ui.row().classes("items-center w-full"):
        folder = ui.input(value=str(state.paths.root)).classes("grow").mark("data-folder")
        notice_slot = ui.row()

        def change() -> None:
            new = (folder.value or "").strip()
            if not new:
                ui.notify("Enter a folder path", type="negative")
                return
            state.change_data_root(Path(new).expanduser())
            notice_slot.clear()
            with notice_slot:
                ui.label("Restart the app to use the new data folder.").classes("text-warning")

        ui.button("Change…", on_click=change)

    defaults = Settings()
    whisper_value = s.whisper_model if s.whisper_model in WHISPER_MODELS else defaults.whisper_model
    language_value = s.default_language if s.default_language in LANGUAGES else defaults.default_language
    claude_options = {model_id: label for label, model_id in CLAUDE_MODELS.items()}
    claude_value = s.claude_model if s.claude_model in claude_options else defaults.claude_model

    whisper = ui.select(list(WHISPER_MODELS), label="Whisper model", value=whisper_value).classes("w-64")
    language = ui.select(LANGUAGES, label="Default language", value=language_value).classes("w-64")

    mode = ui.radio(
        {"subscription": SUBSCRIPTION, "api_key": API_KEY}, value=s.claude_mode
    ).mark("claude-mode")
    key = ui.input("API key", password=True, password_toggle_button=True, value=s.api_key)
    key.classes("w-96").mark("api-key")
    key.bind_visibility_from(mode, "value", backward=lambda v: v == "api_key")
    model = ui.select(claude_options, label="Claude model", value=claude_value).classes("w-64")

    def save() -> None:
        state.update_settings(
            Settings(
                whisper_model=whisper.value,
                default_language=language.value,
                claude_mode=mode.value,
                api_key=key.value or "",
                claude_model=model.value,
            )
        )
        ui.notify("Settings saved", type="positive")

    ui.button("Save", on_click=save)
