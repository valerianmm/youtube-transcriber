"""Small shared dialogs: confirm, message, and the save-conflict dialog."""
from __future__ import annotations

import inspect
from collections.abc import Callable

from nicegui import ui


def confirm_dialog(host: ui.element, title: str, text: str, on_confirm: Callable[[], None],
                   action: str = "Confirm", color: str = "primary") -> None:
    host.clear()
    with host, ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(title).classes("text-lg font-medium")
        ui.label(text)

        async def go() -> None:
            dialog.close()
            result = on_confirm()
            if inspect.isawaitable(result):
                await result

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button(action, on_click=go, color=color).mark("confirm-action")
    dialog.open()


def message_dialog(host: ui.element, title: str, message: str, settings_link: bool = False) -> None:
    host.clear()
    with host, ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(title).classes("text-lg font-medium")
        ui.label(message).classes("whitespace-pre-wrap")
        with ui.row().classes("justify-end w-full items-center"):
            if settings_link:
                ui.link("Open Settings", "/settings")
            ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()


def conflict_dialog(host: ui.element, what: str, on_overwrite: Callable[[], None],
                    on_reload: Callable[[], None]) -> None:
    """Shared by the document and document-type editors."""
    host.clear()
    with host, ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Changed on disk").classes("text-lg font-medium")
        ui.label(f"This {what} was changed outside the app since you opened it.")

        def overwrite() -> None:
            dialog.close()
            on_overwrite()

        def reload() -> None:
            dialog.close()
            confirm_dialog(
                host, "Reload from disk?", "Your unsaved changes will be discarded.",
                on_reload, action="Reload", color="negative",
            )

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Reload from disk", on_click=reload).props("flat")
            ui.button("Overwrite", on_click=overwrite, color="negative").mark("conflict-overwrite")
    dialog.open()
