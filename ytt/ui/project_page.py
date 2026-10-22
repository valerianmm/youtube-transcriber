"""Project page: sources, videos, statuses, languages, re-transcribe."""
from __future__ import annotations

from datetime import date

from nicegui import events, run, ui

from ytt.app_state import AppState
from ytt.config import LANGUAGES, WHISPER_MODELS
from ytt.library import VideoRecord
from ytt.ui.documents_panel import documents_panel
from ytt.urls import classify, split_urls
from ytt.youtube import VideoInfo

STATUS_COLORS = {
    "queued": "grey",
    "downloading": "blue",
    "transcribing": "blue",
    "done": "positive",
    "failed": "negative",
    "cancelled": "grey-6",
}
FLAGS = {
    "en": "🇬🇧", "es": "🇪🇸", "fr": "🇫🇷", "de": "🇩🇪", "it": "🇮🇹", "pt": "🇵🇹",
    "nl": "🇳🇱", "sv": "🇸🇪", "pl": "🇵🇱", "ru": "🇷🇺", "uk": "🇺🇦", "tr": "🇹🇷",
    "ar": "🇸🇦", "he": "🇮🇱", "hi": "🇮🇳", "id": "🇮🇩", "vi": "🇻🇳", "th": "🇹🇭",
    "zh": "🇨🇳", "ja": "🇯🇵", "ko": "🇰🇷",
}


def _duration(seconds: int | None) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _parse_date(text: str | None, label: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{label} must be a date like 2024-01-31") from None


def _status_text(status: str, rec: VideoRecord | None) -> str:
    if status == "failed":
        return f"failed: {(rec.error if rec else None) or 'unknown error'}"
    return status


def _range_text(source) -> str:
    if source.kind != "channel":
        return ""
    return f"{source.date_from or '?'} → {source.date_to or 'today'}"


def project_content(state: AppState, slug: str) -> None:
    try:
        project = state.projects.get(slug)
    except FileNotFoundError:
        ui.label("Project not found").classes("text-xl")
        ui.link("Back to projects", "/")
        return

    _header(state, slug)
    holder: dict = {}
    _add_box(state, slug, lambda: holder["list"].refresh())
    holder["list"] = _sources_section(state, slug)
    with ui.row().classes("gap-2"):
        ui.button("Retry failed", on_click=lambda: _retry(state, slug)).props("flat no-caps")
        ui.button("Cancel queued", on_click=lambda: _cancel(state, slug)).props("flat no-caps")
    documents_panel(state, slug)


def _retry(state: AppState, slug: str) -> None:
    ui.notify(f"Retrying {state.retry_failed(slug)} videos")


def _cancel(state: AppState, slug: str) -> None:
    state.cancel_queued(slug)
    ui.notify("Cancelled queued videos")


# --- header --------------------------------------------------------------
def _header(state: AppState, slug: str) -> None:
    project = state.projects.get(slug)

    with ui.dialog() as rename_dialog, ui.card().classes("w-96"):
        ui.label("Rename project").classes("text-lg font-medium")
        new_name = ui.input("Project name", value=project.name).mark("rename-input")
        error = ui.label().classes("text-negative text-sm")

        def rename() -> None:
            try:
                renamed = state.projects.rename(slug, new_name.value or "")
            except ValueError as e:
                error.set_text(str(e))
                return
            ui.navigate.to(f"/project/{renamed.slug}")

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=rename_dialog.close).props("flat")
            ui.button("Save", on_click=rename)

    with ui.dialog() as delete_dialog, ui.card().classes("w-96"):
        ui.label(f"Delete “{project.name}”?").classes("text-lg font-medium")
        ui.label(
            "This moves the project folder to the Trash; transcripts stay in the library."
        )

        def delete() -> None:
            state.delete_project(slug)
            ui.navigate.to("/")

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=delete_dialog.close).props("flat")
            ui.button("Delete", on_click=delete, color="negative").mark("delete-confirm")

    with ui.row().classes("items-center w-full gap-2"):
        ui.label(project.name).classes("text-2xl grow")
        ui.button("Rename", on_click=rename_dialog.open).props("flat no-caps")
        ui.button("Delete", on_click=delete_dialog.open).props("flat no-caps color=negative")

    def set_language(e) -> None:
        current = state.projects.get(slug)
        current.language = None if e.value in (None, "", "auto") else e.value
        state.projects.save(current)

    current_lang = project.language if project.language in LANGUAGES else "auto"
    ui.select(LANGUAGES, label="Project language", value=current_lang, on_change=set_language).classes(
        "w-64"
    ).mark("project-language")


# --- add sources -----------------------------------------------------------
def _add_box(state: AppState, slug: str, on_added) -> None:
    ui.separator()
    ui.label("Add sources").classes("text-lg font-medium")
    urls = ui.textarea(
        "YouTube URLs (video, playlist or channel), one per line",
    ).classes("w-full").props("autogrow outlined").mark("add-urls")

    async def load_file(e: events.UploadEventArguments) -> None:
        text = await e.file.text()
        urls.set_value(((urls.value or "").rstrip("\n") + "\n" + text).strip())

    ui.upload(label="…or a .txt list of URLs", on_upload=load_file, auto_upload=True).props(
        'accept=".txt"'
    ).classes("w-full").mark("add-file")

    with ui.row().classes("items-center gap-2") as date_row:
        date_from = ui.input("From (YYYY-MM-DD, required for channels)").classes("w-72").mark("date-from")
        date_to = ui.input("To (empty = today)").classes("w-48").mark("date-to")
    date_row.set_visibility(False)

    def has_channel() -> bool:
        return any(classify(u).kind == "channel" for u in split_urls(urls.value or ""))

    urls.on_value_change(lambda _: date_row.set_visibility(has_channel()))

    language = ui.select(LANGUAGES, label="Source language", value="auto").classes("w-64")
    language.mark("source-language")
    error = ui.label().classes("text-negative text-sm")

    async def preview() -> None:
        error.set_text("")
        try:
            d_from = _parse_date(date_from.value, "Start date")
            d_to = _parse_date(date_to.value, "End date")
            if has_channel() and d_from is None:
                raise ValueError("A start date is required for channels")
        except ValueError as e:
            error.set_text(str(e))
            return
        text = urls.value or ""
        if not split_urls(text):
            error.set_text("Paste at least one URL")
            return
        with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[40rem] max-w-full"):
            ui.label("Preview").classes("text-lg font-medium")
            body = ui.column().classes("w-full")
            with body:
                ui.spinner()
        dialog.open()
        try:
            previews = await run.io_bound(state.preview_source, text, d_from, d_to)
        except ValueError as e:
            dialog.close()
            error.set_text(str(e))
            return
        body.clear()
        with body:
            _render_preview(state, slug, previews, language, d_from, d_to, dialog, urls, on_added)

    ui.button("Preview", on_click=preview)


def _render_preview(state, slug, previews, language, d_from, d_to, dialog, urls, on_added) -> None:
    unique: dict[str, VideoInfo] = {}
    for parsed, infos, _ in previews:
        for v in infos:
            unique.setdefault(v.id, v)
    count = len(unique)
    total = sum(v.duration or 0 for v in unique.values())
    ui.label(f"{count} videos · {_duration(total)} total").classes("font-medium")
    with ui.scroll_area().classes("w-full h-80"):
        for parsed, infos, err in previews:
            if parsed.kind == "invalid":
                ui.label(f"Invalid URL: {parsed.url}").classes("text-negative")
            elif err:
                ui.label(f"{parsed.url}: {err}").classes("text-negative")
            else:
                for v in infos:
                    with ui.row().classes("w-full items-center no-wrap gap-2"):
                        ui.label(v.title).classes("grow truncate")
                        ui.label(v.upload_date.isoformat() if v.upload_date else "").classes("text-grey")
                        ui.label(_duration(v.duration)).classes("text-grey")

    def add() -> None:
        state.add_sources(slug, previews, language.value, date_from=d_from, date_to=d_to)
        urls.set_value("")
        dialog.close()
        on_added()

    with ui.row().classes("justify-end w-full"):
        ui.button("Cancel", on_click=dialog.close).props("flat")
        add_button = ui.button(f"Add {count} videos", on_click=add)
        add_button.set_enabled(count > 0)


# --- sources list ----------------------------------------------------------
def _sources_section(state: AppState, slug: str):
    chips: dict[str, list[dict]] = {}  # video id -> rows ("chip", "lang", "key")
    counts: dict[str, ui.label] = {}  # source id -> label
    dialog_host = ui.element("div")  # re-transcribe dialogs live here, not inside a menu

    @ui.refreshable
    def sources_list() -> None:
        chips.clear()
        counts.clear()
        project = state.projects.get(slug)
        statuses = state.video_status(slug)
        if not project.sources:
            ui.label("No sources yet. Add some below.").classes("text-grey")
        for source in project.sources:
            _source_row(state, slug, source, statuses, chips, counts, sources_list.refresh, dialog_host)

    sources_list()

    def tick() -> None:
        try:
            project = state.projects.get(slug)
            statuses = state.video_status(slug)
        except FileNotFoundError:
            return
        records = state.library.records(list(chips))
        for vid, rows in chips.items():
            for parts in rows:
                _update_video(vid, statuses.get(vid, "queued"), records.get(vid), parts)
        for source in project.sources:
            label = counts.get(source.id)
            if label is not None:
                label.set_text(_count_text(source, statuses))

    ui.timer(1.0, tick)
    return sources_list


def _count_text(source, statuses: dict[str, str]) -> str:
    done = sum(1 for v in source.videos if statuses.get(v.id) == "done")
    return f"{done} of {len(source.videos)} transcribed"


def _source_row(state, slug, source, statuses, chips, counts, refresh, dialog_host) -> None:
    header = f"{source.title} · {source.kind}"
    if _range_text(source):
        header += f" · {_range_text(source)}"
    with ui.expansion().classes("w-full border rounded").mark("source") as expansion:
        with expansion.add_slot("header"):
            with ui.row().classes("items-center w-full gap-2"):
                ui.label(header).classes("grow truncate")
                counts[source.id] = ui.label(_count_text(source, statuses)).classes("text-grey")
        with ui.row().classes("gap-2"):
            if source.kind != "video":

                async def do_refresh(s=source) -> None:
                    try:
                        new = await run.io_bound(state.refresh_source, slug, s.id)
                    except Exception as e:  # noqa: BLE001
                        ui.notify(f"Refresh failed: {e}", type="negative")
                        return
                    ui.notify(f"{len(new)} new videos")
                    refresh()

                ui.button("Refresh", on_click=do_refresh).props("flat dense no-caps")

            def do_remove(s=source) -> None:
                state.remove_source(slug, s.id)
                refresh()

            ui.button("Remove", on_click=do_remove).props("flat dense no-caps color=negative")
        rows = ui.column().classes("w-full gap-1")
        built = {"done": False}

        def build(e) -> None:  # lazily build video rows on first expand
            if e.value and not built["done"]:
                built["done"] = True
                statuses = state.video_status(slug)
                records = state.library.records([v.id for v in source.videos])
                with rows:
                    for info in source.videos:
                        _video_row(state, info, statuses, records, chips, dialog_host)

        expansion.on_value_change(build)


def _video_row(state, info: VideoInfo, statuses, records, chips, dialog_host) -> None:
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        ui.label(info.title).classes("grow truncate")
        lang = ui.row().classes("items-center gap-1")
        chip = ui.badge("queued")
        with ui.button(icon="more_horiz").props("flat dense round").mark(f"video-menu-{info.id}"):
            with ui.menu():
                ui.menu_item(
                    "Re-transcribe…", on_click=lambda i=info: _retranscribe_dialog(state, i, dialog_host)
                )
    parts = {"chip": chip, "lang": lang, "key": None}
    chips.setdefault(info.id, []).append(parts)
    _update_video(info.id, statuses.get(info.id, "queued"), records.get(info.id), parts)


def _update_video(vid: str, status: str, rec: VideoRecord | None, parts: dict) -> None:
    text = _status_text(status, rec)
    lang_key = (rec.language, rec.youtube_language, rec.language_mismatch) if rec else None
    key = (text, status, lang_key)
    if parts["key"] == key:
        return
    parts["key"] = key
    chip = parts["chip"]
    chip.set_text(text)
    chip.props(f"color={STATUS_COLORS.get(status, 'grey')}")
    lang = parts["lang"]
    lang.clear()
    if rec and rec.language:
        with lang:
            ui.label(f"{FLAGS.get(rec.language, '🌐')} {rec.language}")
            if rec.language_mismatch:
                with ui.label("⚠").classes("text-warning"):
                    ui.tooltip(f"Whisper heard {rec.language}, YouTube says {rec.youtube_language}")


def _retranscribe_dialog(state: AppState, info: VideoInfo, host) -> None:
    affected = [
        p.name for p in state.projects.list() if any(v.id == info.id for v in p.videos())
    ]
    current = state.settings.whisper_model
    host.clear()
    with host, ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(f"Re-transcribe “{info.title}”").classes("text-lg font-medium")
        model = ui.select(
            list(WHISPER_MODELS),
            label="Model",
            value=current if current in WHISPER_MODELS else next(iter(WHISPER_MODELS)),
        ).classes("w-full")
        language = ui.select(LANGUAGES, label="Language", value="auto").classes("w-full")
        ui.label("This replaces the transcript in every project that uses this video").classes(
            "text-warning"
        )
        for name in affected:
            ui.label(f"• {name}")

        def go() -> None:
            dialog.close()
            try:
                state.retranscribe(info.id, model.value, language.value)
            except ValueError as e:
                ui.notify(str(e), type="warning")
                return
            ui.notify("Re-transcription queued")

        with ui.row().classes("justify-end w-full"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Re-transcribe", on_click=go).mark("retranscribe-confirm")
    dialog.open()
