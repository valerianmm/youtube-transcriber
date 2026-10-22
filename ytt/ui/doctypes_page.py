"""Document types page: list, create/duplicate/rename/delete, and an editor."""
from __future__ import annotations

from nicegui import ui

from ytt.app_state import AppState
from ytt.doctypes import DocType
from ytt.storage import ANY, ConflictError
from ytt.ui.dialogs import confirm_dialog, conflict_dialog, message_dialog


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def doctypes_content(state: AppState) -> None:
    store = state.doctypes
    cur: dict = {"slug": None, "fp": None}
    host = ui.element("div")
    ui.label("Document types").classes("text-xl")

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("w-72 gap-1"):
            ui.button("+ New type", on_click=lambda: new_dialog()).props("no-caps")
            list_slot = ui.column().classes("w-full gap-1")
        with ui.column().classes("grow") as pane:
            name = ui.input("Name").classes("w-full").mark("doctype-name")
            description = ui.input("Description").classes("w-full").mark("doctype-description")
            body = ui.codemirror("", language="Markdown").classes("w-full h-96").mark("doctype-editor")
            ui.button("Save", on_click=lambda: save()).props("no-caps").mark("doctype-save")
    placeholder = ui.label("Select a document type to edit.").classes("text-grey")
    pane.set_visibility(False)

    @ui.refreshable
    def type_list() -> None:
        for dt in store.list():
            with ui.row().classes("w-full items-center no-wrap gap-1"):
                ui.button(dt.name, on_click=lambda d=dt: select(d.slug)).props(
                    "flat no-caps align=left"
                ).classes("grow").mark(f"type-{dt.slug}")
                ui.button("Duplicate", on_click=lambda d=dt: duplicate(d.slug)).props(
                    "flat dense no-caps"
                ).mark(f"type-duplicate-{dt.slug}")
                ui.button("Rename", on_click=lambda d=dt: rename_dialog(d)).props("flat dense no-caps").mark(
                    f"type-rename-{dt.slug}"
                )
                ui.button("Delete", on_click=lambda d=dt: delete(d)).props(
                    "flat dense no-caps color=negative"
                ).mark(f"type-delete-{dt.slug}")

    with list_slot:
        type_list()

    def load(slug: str) -> bool:
        try:
            dt, fp = store.get(slug)
        except OSError as e:
            ui.notify(f"Couldn't read the document type: {e}", type="negative")
            type_list.refresh()
            return False
        cur.update(slug=slug, fp=fp)
        name.set_value(dt.name)
        description.set_value(dt.description)
        body.set_value(dt.body)
        pane.set_visibility(True)
        placeholder.set_visibility(False)
        return True

    def select(slug: str) -> None:
        load(slug)

    # --- save ------------------------------------------------------------
    def save(expect=None) -> None:
        slug = cur["slug"]
        new_name = _one_line(name.value)
        if not new_name:
            ui.notify("Name cannot be empty", type="negative")
            return
        others = {d.name.lower() for d in store.list() if d.slug != slug}
        if new_name.lower() in others:
            ui.notify(f"A document type named '{new_name}' already exists", type="negative")
            return
        dt = DocType(slug, new_name, _one_line(description.value), body.value or "")
        try:
            cur["fp"] = store.save(dt, cur["fp"] if expect is None else expect)
        except ConflictError:
            conflict_dialog(host, "document type", lambda: save(ANY), lambda: load(slug))
            return
        except OSError as e:
            ui.notify(f"Couldn't save: {e}", type="negative")
            return
        type_list.refresh()
        ui.notify("Saved")

    # --- list actions ----------------------------------------------------
    def new_dialog() -> None:
        host.clear()
        with host, ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("New document type").classes("text-lg font-medium")
            new_name = ui.input("Name").props("autofocus").mark("new-type-name")
            error = ui.label().classes("text-negative text-sm")

            def create() -> None:
                try:
                    dt = store.create(_one_line(new_name.value))
                except ValueError as e:
                    error.set_text(str(e))
                    return
                except OSError as e:
                    error.set_text(str(e))
                    return
                dialog.close()
                type_list.refresh()
                load(dt.slug)

            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Create", on_click=create).mark("new-type-create")
        dialog.open()

    def duplicate(slug: str) -> None:
        try:
            dt = store.duplicate(slug)
        except OSError as e:
            ui.notify(f"Couldn't duplicate: {e}", type="negative")
            return
        type_list.refresh()
        load(dt.slug)

    def rename_dialog(dt: DocType) -> None:
        host.clear()
        with host, ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label(f"Rename “{dt.name}”").classes("text-lg font-medium")
            new_name = ui.input("Name", value=dt.name).props("autofocus").mark("rename-type-input")
            error = ui.label().classes("text-negative text-sm")

            def rename() -> None:
                try:
                    renamed = store.rename(dt.slug, _one_line(new_name.value))
                except ValueError as e:
                    error.set_text(str(e))
                    return
                except ConflictError:
                    dialog.close()
                    type_list.refresh()
                    message_dialog(
                        host, "Changed on disk",
                        "This document type was changed outside the app. "
                        "The list was refreshed; try the rename again.",
                    )
                    return
                except OSError as e:
                    error.set_text(str(e))
                    return
                dialog.close()
                type_list.refresh()
                if cur["slug"] == dt.slug:
                    load(renamed.slug)

            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Rename", on_click=rename).mark("rename-type-confirm")
        dialog.open()

    def delete(dt: DocType) -> None:
        def go() -> None:
            try:
                store.delete(dt.slug)
            except OSError as e:
                ui.notify(f"Couldn't delete: {e}", type="negative")
                return
            if cur["slug"] == dt.slug:
                cur.update(slug=None, fp=None)
                pane.set_visibility(False)
                placeholder.set_visibility(True)
            type_list.refresh()

        confirm_dialog(host, f"Delete “{dt.name}”?", "It is moved to the .trash folder.", go,
                       action="Delete", color="negative")
