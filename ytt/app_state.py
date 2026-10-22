"""Application wiring: one object that owns the stores and the queue."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from ytt.claude_client import backend_for
from ytt.config import Paths, Settings, load_settings, save_settings, set_data_root
from ytt.doctypes import DocTypeStore
from ytt.documents import DocumentStore
from ytt.generate import generate_document
from ytt.library import Library
from ytt.projects import DocumentMeta, Project, ProjectStore, Source
from ytt.tq import Job, TranscriptionQueue
from ytt.urls import ParsedUrl, classify, split_urls
from ytt.youtube import VideoInfo, expand as youtube_expand


ALREADY_QUEUED = "This video is already in the queue; re-transcribe it again after it finishes."


class AppState:
    def __init__(self, paths: Paths, *, queue_factory=TranscriptionQueue, expand=youtube_expand):
        self.paths = paths
        self._expand = expand
        self.settings: Settings = load_settings(paths)
        self.library = Library(paths)
        self.projects = ProjectStore(paths)
        self.queue = queue_factory(self.library, paths.audio_cache)
        self.doctypes = DocTypeStore(paths)
        self.documents = DocumentStore(self.projects)
        self.generating: set[str] = set()  # project slugs with a Claude run in flight

    # --- startup ---------------------------------------------------------
    def startup(self) -> None:
        self.paths.ensure_layout()
        shutil.rmtree(self.paths.audio_cache, ignore_errors=True)
        self.paths.audio_cache.mkdir(parents=True, exist_ok=True)
        self.library.cleanup_partials()
        self.doctypes.seed_defaults()
        self.requeue_pending()
        self.queue.start()

    def requeue_pending(self) -> None:
        for project in self.projects.list():
            for info in project.videos():
                if info.id in project.cancelled or self.library.is_done(info.id):
                    continue
                rec = self.library.get(info.id)
                if rec is not None and rec.status == "failed":
                    continue
                self._enqueue(project, info)

    # --- helpers ---------------------------------------------------------
    def _language_for(self, project: Project, video_id: str) -> str | None:
        lang = project.forced_language_for(video_id) or self.settings.default_language
        return None if lang in (None, "", "auto") else lang

    def _enqueue(self, project: Project, info: VideoInfo, force: bool = False) -> None:
        self.queue.enqueue(
            Job(
                info=info,
                language=self._language_for(project, info.id),
                model=self.settings.whisper_model,
                projects={project.slug},
                force=force,
            )
        )

    # --- sources ---------------------------------------------------------
    def preview_source(
        self, text: str, date_from: date | None, date_to: date | None
    ) -> list[tuple[ParsedUrl, list[VideoInfo], str | None]]:
        out: list[tuple[ParsedUrl, list[VideoInfo], str | None]] = []
        for raw in split_urls(text):
            parsed = classify(raw)
            if parsed.kind == "invalid":
                out.append((parsed, [], None))
                continue
            if parsed.kind == "channel" and date_from is None:
                raise ValueError("A start date is required for channels")
            try:
                out.append((parsed, self._expand(parsed, date_from, date_to), None))
            except Exception as e:  # noqa: BLE001 - one bad URL must not fail the preview
                out.append((parsed, [], str(e) or type(e).__name__))
        return out

    def add_sources(
        self,
        slug: str,
        previews,
        language: str | None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> None:
        """previews: items of preview_source (extra tuple members are ignored)."""
        project = self.projects.get(slug)
        added: list[Source] = []
        for item in previews:
            parsed, infos = item[0], item[1]
            if parsed.kind == "invalid" or not infos:
                continue
            # expand() does not expose playlist titles, so playlists fall back to the URL.
            if parsed.kind == "video":
                title = infos[0].title
            elif parsed.kind == "channel":
                title = infos[0].channel or parsed.url
            else:
                title = parsed.url
            is_channel = parsed.kind == "channel"
            source = Source(
                    kind=parsed.kind,
                    url=parsed.url,
                    title=title,
                    language=None if language in (None, "", "auto") else language,
                    date_from=date_from.isoformat() if is_channel and date_from else None,
                    date_to=date_to.isoformat() if is_channel and date_to else None,
                    videos=list(infos),
            )
            project.sources.append(source)
            added.append(source)
        self.projects.save(project)
        project = self.projects.get(slug)
        seen: set[str] = set()
        for source in added:
            for info in source.videos:
                if info.id in seen or info.id in project.cancelled:
                    continue
                seen.add(info.id)
                rec = self.library.get(info.id)
                if rec is not None and rec.status == "failed":
                    continue
                self._enqueue(project, info)

    def refresh_source(self, slug: str, source_id: str) -> list[VideoInfo]:
        source = next(s for s in self.projects.get(slug).sources if s.id == source_id)
        parsed = classify(source.url)
        d_from = date.fromisoformat(source.date_from) if source.date_from else None
        d_to = date.fromisoformat(source.date_to) if source.date_to else None
        infos = self._expand(parsed, d_from, d_to)
        new = self.projects.merge_new_videos(slug, source_id, infos)
        project = self.projects.get(slug)
        for info in new:
            self._enqueue(project, info)
        return new

    def remove_source(self, slug: str, source_id: str) -> None:
        before = {v.id for v in self.projects.get(slug).videos()}
        project = self.projects.remove_source(slug, source_id)
        gone = before - {v.id for v in project.videos()}
        if gone:
            self.queue.cancel_project(slug, gone)

    def delete_project(self, slug: str) -> None:
        self.projects.delete(slug)
        self.queue.cancel_project(slug)

    # --- status / queue actions -----------------------------------------
    def video_status(self, slug: str) -> dict[str, str]:
        project = self.projects.get(slug)
        videos = project.videos()
        records = self.library.records([v.id for v in videos])
        out: dict[str, str] = {}
        for info in videos:
            stage = self.queue.stage_of(info.id)
            rec = records.get(info.id)
            if stage is not None:
                out[info.id] = "transcribing" if stage == "loading_model" else stage
            elif self.library.is_done_record(rec):
                out[info.id] = "done"
            elif info.id in project.cancelled:
                out[info.id] = "cancelled"
            else:
                out[info.id] = "failed" if rec and rec.status == "failed" else "queued"
        return out

    def _is_pending(self, project: Project, info: VideoInfo) -> bool:
        if info.id in project.cancelled or self.library.is_done(info.id):
            return False
        rec = self.library.get(info.id)
        return not (rec and rec.status == "failed")

    def retry_failed(self, slug: str) -> int:
        project = self.projects.get(slug)
        todo: list[VideoInfo] = []
        for info in project.videos():
            if self.library.is_done(info.id):
                continue
            rec = self.library.get(info.id)
            if info.id in project.cancelled or (rec and rec.status == "failed"):
                todo.append(info)
        project.cancelled -= {i.id for i in todo}
        self.projects.save(project)
        for info in todo:
            self._enqueue(project, info, force=True)
        return len(todo)

    def cancel_queued(self, slug: str) -> list[str]:
        project = self.projects.get(slug)
        queued = {v.id for v in project.videos() if self.queue.stage_of(v.id) == "queued"}
        dropped = self.queue.cancel_project(slug)
        cancelled = queued | set(dropped)
        if cancelled:
            project = self.projects.get(slug)
            project.cancelled |= cancelled
            self.projects.save(project)
        return dropped

    def retranscribe(self, video_id: str, model: str, language: str | None) -> None:
        if self.queue.stage_of(video_id) is not None:
            raise ValueError(ALREADY_QUEUED)
        info: VideoInfo | None = None
        slugs: set[str] = set()
        for project in self.projects.list():
            for v in project.videos():
                if v.id == video_id:
                    info = info or v
                    slugs.add(project.slug)
        if info is None:
            raise KeyError(video_id)
        self.queue.enqueue(
            Job(
                info=info,
                language=None if language in (None, "", "auto") else language,
                model=model,
                projects=slugs,
                force=True,
            )
        )

    # --- documents -------------------------------------------------------
    def ready_to_generate(self, slug: str) -> tuple[bool, list[VideoInfo]]:
        project = self.projects.get(slug)
        videos = project.videos()
        left_out = [v for v in videos if not self.library.is_done(v.id)]
        busy = any(
            self.queue.stage_of(v.id) is not None or self._is_pending(project, v)
            for v in left_out
        )
        has_done = len(left_out) < len(videos)
        return (has_done and not busy), left_out

    def generate(
        self, slug: str, doc_type_slug: str, name: str, replace_file: str | None = None
    ) -> DocumentMeta:
        """Runs Claude synchronously; call from a worker thread, never an event loop."""
        project = self.projects.get(slug)
        items = [
            (v, self.library.read_transcript(v.id))
            for v in project.videos()
            if self.library.is_done(v.id)
        ]
        dt, _ = self.doctypes.get(doc_type_slug)
        markdown = generate_document(
            backend_for(self.settings), self.settings.claude_model, dt, items
        )
        if replace_file is None:
            return self.documents.create(slug, name, doc_type_slug, markdown)
        self.documents.replace_generated(slug, replace_file, markdown)
        return next(m for m in self.projects.get(slug).documents if m.file == replace_file)

    # --- settings --------------------------------------------------------
    def update_settings(self, new: Settings) -> None:
        save_settings(self.paths, new)
        for k, v in vars(new).items():
            setattr(self.settings, k, v)

    def change_data_root(self, path: Path) -> None:
        """Persist the new root; it takes effect after an app restart (no hot-swap)."""
        set_data_root(path)
