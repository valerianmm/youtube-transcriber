"""App shell: page registration, left drawer, and top queue bar."""
from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui

from ytt.app_state import AppState

STAGE_LABELS = {
    "idle": "Idle",
    "downloading": "Downloading",
    "loading_model": "Loading model",
    "transcribing": "Transcribing",
}


def _counts(project) -> str:
    return f"{len(project.videos())} videos · {len(project.documents)} docs"


def _new_project_dialog(state: AppState, on_created) -> ui.dialog:
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("New project").classes("text-lg font-medium")
        name = ui.input("Project name").props("autofocus").mark("new-project-name")
        error = ui.label().classes("text-negative text-sm").mark("new-project-error")
        error.set_visibility(False)

        def create() -> None:
            try:
                state.projects.create(name.value or "")
            except ValueError as e:
                error.set_text(str(e))
                error.set_visibility(True)
                return
            name.set_value("")
            error.set_visibility(False)
            dialog.close()
            on_created()

        name.on("keydown.enter", create)
        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Create", on_click=create)
    return dialog


def _queue_bar(state: AppState) -> None:
    with ui.row().classes("items-center gap-4 w-full"):
        ui.label("YouTube Transcriber").classes("text-lg font-medium")
        title = ui.label().classes("grow truncate")
        stage = ui.label()
        progress = ui.label()
        button = ui.button().props("flat dense color=white")

    def toggle() -> None:
        if state.queue.snapshot().paused:
            state.queue.resume()
        else:
            state.queue.stop()
        refresh()

    button.on_click(toggle)

    def refresh() -> None:
        snap = state.queue.snapshot()
        title.set_text(snap.current_title or "")
        stage.set_text(STAGE_LABELS.get(snap.stage, snap.stage) if snap.current else "")
        progress.set_text(f"{snap.done}/{snap.total}")
        button.set_text("Resume" if snap.paused else "Stop")

    refresh()
    ui.timer(1.0, refresh)


@contextmanager
def frame(state: AppState, active: str | None):
    """Render the drawer and queue bar, then yield for the page content."""
    with ui.header().classes("items-center"):
        _queue_bar(state)

    with ui.left_drawer(fixed=True).classes("gap-1"):

        @ui.refreshable
        def project_list() -> None:
            for project in state.projects.list():
                with ui.link(target=f"/project/{project.slug}").classes("no-underline w-full"):
                    with ui.column().classes("gap-0" + (" font-bold" if active == project.slug else "")):
                        ui.label(project.name)
                        ui.label(_counts(project)).classes("text-xs text-grey")

        project_list()
        dialog = _new_project_dialog(state, project_list.refresh)
        ui.button("+ New project", on_click=dialog.open).props("flat dense no-caps")
        ui.separator()
        ui.link("Document types", "/doctypes")
        ui.link("Settings", "/settings")

    with ui.column().classes("w-full max-w-4xl mx-auto"):
        yield


def register_pages(state: AppState) -> None:
    from ytt.ui.settings_page import settings_content

    @ui.page("/")
    def index() -> None:
        projects = state.projects.list()
        if projects:
            ui.navigate.to(f"/project/{projects[0].slug}")
            return
        with frame(state, None):
            ui.label("Create your first project").classes("text-xl")
            ui.label("Use “+ New project” in the sidebar to get started.")

    @ui.page("/settings")
    def settings() -> None:
        with frame(state, "settings"):
            settings_content(state)
