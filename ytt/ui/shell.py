"""App shell: page registration, left drawer, and top queue bar."""
from __future__ import annotations

from contextlib import contextmanager

from nicegui import app, ui

from ytt.app_state import AppState

STAGE_LABELS = {
    "idle": "Idle",
    "downloading": "Downloading",
    "loading_model": "Loading model",
    "transcribing": "Transcribing",
}
LOG_LINES = 50

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
                project = state.projects.create(name.value or "")
            except ValueError as e:
                error.set_text(str(e))
                error.set_visibility(True)
                return
            name.set_value("")
            error.set_visibility(False)
            dialog.close()
            on_created(project)

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
    with ui.expansion("Log").props("dense").classes("w-full text-sm"):
        log = ui.label().classes("whitespace-pre-wrap font-mono text-xs max-h-48 overflow-auto")
    shown: dict = {"log": None}

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
        lines = snap.log[-LOG_LINES:]
        if lines != shown["log"]:
            shown["log"] = lines
            log.set_text("\n".join(lines) or "Nothing yet")

    refresh()
    ui.timer(1.0, refresh)


@contextmanager
def frame(state: AppState, active: str | None):
    """Render the drawer and queue bar, then yield for the page content."""
    with ui.header().classes("items-center"), ui.column().classes("w-full gap-0"):
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
        def created(project) -> None:
            project_list.refresh()
            ui.navigate.to(f"/project/{project.slug}")

        dialog = _new_project_dialog(state, created)

        seen = {"counts": [_counts(p) for p in state.projects.list()]}

        def refresh_counts() -> None:
            counts = [_counts(p) for p in state.projects.list()]
            if counts != seen["counts"]:
                seen["counts"] = counts
                project_list.refresh()

        ui.timer(5.0, refresh_counts)
        ui.button("+ New project", on_click=dialog.open).props("flat dense no-caps")
        ui.separator()
        ui.link("Document types", "/doc-types")
        ui.link("Settings", "/settings")

    with ui.column().classes("w-full max-w-4xl mx-auto"):
        yield


def _show_exception(e: Exception) -> None:
    """Last-resort feedback for handler errors; NiceGUI still logs the traceback."""
    try:
        ui.notify(f"Something went wrong: {e}", type="negative")
    except RuntimeError:
        pass  # no client context (e.g. a background task of a closed page)


def register_pages(state: AppState) -> None:
    app.on_exception(_show_exception)
    from ytt.ui.doctypes_page import doctypes_content
    from ytt.ui.project_page import project_content
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

    @ui.page("/doc-types")
    def doc_types() -> None:
        with frame(state, "doc-types"):
            doctypes_content(state)

    @ui.page("/project/{slug}")
    def project(slug: str) -> None:
        with frame(state, slug):
            project_content(state, slug)
