"""Documents panel: list, generate, Markdown editor, export."""
from __future__ import annotations

from nicegui import run, ui

from ytt.app_state import AppState
from ytt.claude_client import ClaudeError
from ytt.pdf import PdfUnavailable
from ytt.storage import ANY, ConflictError
from ytt.ui.dialogs import confirm_dialog, conflict_dialog, message_dialog

_WAITING = ("queued", "downloading", "transcribing")


def _day(stamp: str | None) -> str:
    return (stamp or "")[:10]


def documents_panel(state: AppState, slug: str) -> None:
    cur: dict = {"file": None, "fp": None, "saved": "", "busy": False}
    host = ui.element("div")  # dialogs live here
    gate_buttons: list[ui.button] = []  # disabled while a generation runs

    ui.separator()
    ui.label("Documents").classes("text-lg font-medium")
    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("w-64 gap-1"):
            new_button = ui.button("+ Generate new…", on_click=lambda: generate_dialog())
            new_button.props("no-caps")
            gate_buttons.append(new_button)
            spinner = ui.spinner(size="lg")
            spinner.set_visibility(False)
            doc_list_slot = ui.column().classes("w-full gap-1")
        with ui.column().classes("grow") as pane:
            with ui.row().classes("items-center gap-2"):
                title = ui.label().classes("font-medium")
                dot = ui.label("●").classes("text-warning")
                dot.tooltip("Unsaved changes")
                dot.set_visibility(False)
            editor = ui.codemirror("", language="Markdown").classes("w-full h-96").mark("doc-editor")
            with ui.row().classes("gap-2"):
                ui.button("Save", on_click=lambda: save()).props("no-caps").mark("doc-save")
                regen = ui.button("Regenerate", on_click=lambda: regenerate()).props("no-caps flat")
                regen.mark("doc-regenerate")
                gate_buttons.append(regen)
                ui.button("Export .md", on_click=lambda: export("md")).props("no-caps flat").mark("doc-export-md")
                ui.button("Export .pdf", on_click=lambda: export("pdf")).props("no-caps flat").mark(
                    "doc-export-pdf"
                )
                ui.button("Delete", on_click=lambda: delete()).props("no-caps flat color=negative").mark(
                    "doc-delete"
                )
    placeholder = ui.label("Generate a document, or select one to edit.").classes("text-grey")
    pane.set_visibility(False)

    def dirty() -> bool:
        return cur["file"] is not None and editor.value != cur["saved"]

    editor.on_value_change(lambda _: dot.set_visibility(dirty()))

    def type_name(doc_type: str) -> str:
        try:
            return state.doctypes.get(doc_type)[0].name
        except OSError:
            return doc_type

    @ui.refreshable
    def doc_list() -> None:
        docs = state.projects.get(slug).documents
        if not docs:
            ui.label("No documents yet").classes("text-grey")
        for meta in docs:
            sub = f"{type_name(meta.doc_type)} · generated {_day(meta.generated_at)}"
            if meta.edited_at:
                sub += " · edited"
            with ui.item(on_click=lambda m=meta: select(m.file)).classes("w-full").mark(
                f"doc-{meta.file}"
            ):
                with ui.item_section():
                    ui.item_label(meta.name)
                    ui.item_label(sub).props("caption")

    with doc_list_slot:
        doc_list()

    def load(file: str) -> bool:
        try:
            text, fp = state.documents.read(slug, file)
        except OSError as e:
            ui.notify(f"Couldn't read {file}: {e}", type="negative")
            return False
        cur.update(file=file, fp=fp, saved=text)
        editor.set_value(text)
        dot.set_visibility(False)
        return True

    def select(file: str) -> None:
        def go() -> None:
            meta = next((m for m in state.projects.get(slug).documents if m.file == file), None)
            if meta is None or not load(file):
                return
            title.set_text(meta.name)
            pane.set_visibility(True)
            placeholder.set_visibility(False)

        if dirty() and file != cur["file"]:
            confirm_dialog(host, "Discard changes?", "This document has unsaved changes.", go,
                           action="Discard", color="negative")
        else:
            go()

    # --- save ------------------------------------------------------------
    def save(expect=None) -> None:
        file = cur["file"]
        text = editor.value
        try:
            fp = state.documents.save(slug, file, text, cur["fp"] if expect is None else expect)
        except ConflictError:
            conflict_dialog(host, "document", lambda: save(ANY), lambda: load(file))
            return
        except OSError as e:
            ui.notify(f"Couldn't save: {e}", type="negative")
            return
        cur.update(fp=fp, saved=text)
        dot.set_visibility(dirty())
        doc_list.refresh()
        ui.notify("Saved")

    # --- delete ----------------------------------------------------------
    def delete() -> None:
        file = cur["file"]

        def go() -> None:
            try:
                state.documents.delete(slug, file)
            except OSError as e:
                ui.notify(f"Couldn't delete: {e}", type="negative")
                return
            cur.update(file=None, fp=None, saved="")
            pane.set_visibility(False)
            placeholder.set_visibility(True)
            doc_list.refresh()

        confirm_dialog(host, "Delete document?", "The last version is kept in history.", go,
                       action="Delete", color="negative")

    # --- export ----------------------------------------------------------
    async def export(kind: str) -> None:
        file = cur["file"]
        try:
            if kind == "md":
                path = state.documents.export_md(slug, file)
            else:
                path = await run.io_bound(state.documents.export_pdf, slug, file)
        except PdfUnavailable as e:
            message_dialog(host, "PDF export unavailable", str(e))
            return
        except Exception as e:  # noqa: BLE001 - never let a handler fail silently
            ui.notify(f"Export failed: {e}", type="negative")
            return
        ui.notify(f"Exported the saved version to {path}")
        ui.download(path)

    # --- generate --------------------------------------------------------
    async def run_generate(doc_type: str, name: str, replace_file: str | None = None) -> bool:
        if cur["busy"]:
            return False
        cur["busy"] = True
        for b in gate_buttons:
            b.set_enabled(False)
        spinner.set_visibility(True)
        try:
            meta = await run.io_bound(state.generate, slug, doc_type, name, replace_file)
        except ClaudeError as e:
            message_dialog(host, "Claude couldn't generate the document", str(e), settings_link=True)
            return False
        except Exception as e:  # noqa: BLE001
            message_dialog(host, "Generation failed", str(e))
            return False
        finally:
            spinner.set_visibility(False)
            cur["busy"] = False
            for b in gate_buttons:
                b.set_enabled(True)
        doc_list.refresh()
        select_after(meta.file)
        return True

    def select_after(file: str) -> None:
        cur["file"] = None  # a fresh result replaces whatever was open
        select(file)

    def regenerate() -> None:
        file = cur["file"]
        meta = next((m for m in state.projects.get(slug).documents if m.file == file), None)
        if meta is None:
            return
        confirm_dialog(
            host, "Regenerate?",
            "The current version goes to history and is replaced by a new one. "
            "Unsaved edits are lost.",
            lambda: run_generate(meta.doc_type, meta.name, file),
            action="Regenerate", color="negative",
        )

    def generate_dialog() -> None:
        types = state.doctypes.list()
        if not types:
            ui.notify("Create a document type first (Document types in the sidebar)", type="warning")
            return
        ready, left_out = state.ready_to_generate(slug)
        statuses = state.video_status(slug)
        waiting = sum(1 for v in left_out if statuses.get(v.id) in _WAITING)
        names = {t.slug: t.name for t in types}
        host.clear()
        with host, ui.dialog() as dialog, ui.card().classes("w-[32rem] max-w-full"):
            ui.label("Generate document").classes("text-lg font-medium")
            first = types[0]
            kind = ui.select(names, label="Document type", value=first.slug).classes("w-full")
            kind.mark("gen-type")
            name = ui.input("Name", value=first.name).classes("w-full").mark("gen-name")
            kind.on_value_change(lambda e: name.set_value(names.get(e.value, "")))
            if left_out:
                ui.label("Left out (no transcript yet):").classes("text-sm font-medium")
                for v in left_out:
                    ui.label(f"• {v.title} ({statuses.get(v.id, 'queued')})").classes("text-sm")
            ui.label("The transcripts will be sent to Claude").classes("text-grey text-sm")
            if not ready:
                reason = (
                    f"Waiting for {waiting} videos to finish" if waiting
                    else "No transcribed videos yet"
                )
                ui.label(reason).classes("text-warning")

            async def go() -> None:
                dialog.close()
                await run_generate(kind.value, (name.value or "").strip() or names[kind.value])

            with ui.row().classes("justify-end w-full"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                go_button = ui.button("Generate", on_click=go).mark("gen-go")
                go_button.set_enabled(ready and not cur["busy"])
        dialog.open()
