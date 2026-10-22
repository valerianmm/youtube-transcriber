"""Global FIFO transcription queue: one worker thread, dedup across projects."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from huggingface_hub import try_to_load_from_cache

from ytt.config import WHISPER_MODELS
from ytt.library import Library
from ytt.transcribe import Transcriber
from ytt.youtube import Aborted, VideoInfo, download_audio, friendly_error

Stage = Literal["idle", "downloading", "loading_model", "transcribing"]
LOG_LIMIT = 200


@dataclass
class Job:
    info: VideoInfo
    language: str | None
    model: str
    projects: set[str]
    force: bool = False


@dataclass
class QueueSnapshot:
    paused: bool
    current: str | None
    current_title: str | None
    stage: Stage
    done: int
    total: int
    queued: list[str]
    log: list[str] = field(default_factory=list)


def _model_cached(model_key: str) -> bool:
    repo = WHISPER_MODELS.get(model_key, model_key)
    try:
        return try_to_load_from_cache(repo, "config.json") is not None
    except Exception:  # noqa: BLE001 - treat any cache trouble as "not cached"
        return False


class TranscriptionQueue:
    def __init__(
        self,
        library: Library,
        audio_dir: Path,
        download=download_audio,
        transcriber_factory=Transcriber,
        model_cached: Callable[[str], bool] = _model_cached,
    ):
        self.library = library
        self.audio_dir = audio_dir
        self._download = download
        self._factory = transcriber_factory
        self._model_cached = model_cached
        self._cond = threading.Condition()
        self._queue: deque[Job] = deque()
        self._current: Job | None = None
        self._stage: Stage = "idle"
        self._paused = False
        self._abort = False
        self._done = 0
        self._total = 0
        self._log: deque[str] = deque(maxlen=LOG_LIMIT)
        self._transcribers: dict[str, Transcriber] = {}
        self._seen_models: set[str] = set()
        self._thread: threading.Thread | None = None
        self._shutdown = False

    # --- public API ------------------------------------------------------
    def start(self) -> None:
        with self._cond:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="tq-worker", daemon=True)
            self._thread.start()

    def shutdown(self, timeout: float = 5) -> None:
        """Terminate the worker thread (tests / app exit)."""
        with self._cond:
            self._shutdown = True
            self._abort = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)

    def enqueue(self, job: Job) -> Literal["queued", "merged", "reused"]:
        with self._cond:
            vid = job.info.id
            if not job.force and self.library.is_done(vid):
                return "reused"
            for existing in (*([self._current] if self._current else []), *self._queue):
                if existing.info.id == vid:
                    existing.projects |= job.projects
                    return "merged"
            if not self._queue and self._current is None:
                self._done = self._total = 0
            self._queue.append(job)
            self._total += 1
            self._cond.notify_all()
            return "queued"

    def stop(self) -> None:
        with self._cond:
            self._paused = True
            if self._stage == "downloading":
                self._abort = True
            self._log_line("Queue stopped")
            self._cond.notify_all()

    def resume(self) -> None:
        with self._cond:
            self._paused = False
            if not self._shutdown:
                self._abort = False  # let an in-flight download continue
            self._log_line("Queue resumed")
            self._cond.notify_all()

    def cancel_project(self, slug: str) -> list[str]:
        with self._cond:
            kept: deque[Job] = deque()
            dropped: list[str] = []
            for job in self._queue:
                job.projects.discard(slug)
                if job.projects:
                    kept.append(job)
                else:
                    dropped.append(job.info.id)
            self._queue = kept
            self._total -= len(dropped)
            self._cond.notify_all()
            return dropped

    def snapshot(self) -> QueueSnapshot:
        with self._cond:
            cur = self._current
            return QueueSnapshot(
                paused=self._paused,
                current=cur.info.id if cur else None,
                current_title=cur.info.title if cur else None,
                stage=self._stage if cur else "idle",
                done=self._done,
                total=self._total,
                queued=[j.info.id for j in self._queue],
                log=list(self._log),
            )

    def stage_of(
        self, video_id: str
    ) -> Literal["queued", "downloading", "loading_model", "transcribing"] | None:
        with self._cond:
            if self._current and self._current.info.id == video_id:
                return self._stage  # type: ignore[return-value]
            if any(j.info.id == video_id for j in self._queue):
                return "queued"
            return None

    def wait_idle(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(
                lambda: not self._queue and self._current is None, timeout
            )

    # --- worker ----------------------------------------------------------
    def _log_line(self, text: str) -> None:
        self._log.append(f"{datetime.now():%H:%M:%S} {text}")

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._shutdown and (self._paused or not self._queue):
                    self._cond.wait()
                if self._shutdown:
                    return
                job = self._queue.popleft()
                self._current = job
                self._stage = "downloading"
                self._abort = False
                self._log_line(f"Downloading {job.info.title}")
                self._cond.notify_all()
            try:
                self._process(job)
            except Exception as e:  # noqa: BLE001 - the worker must never die
                with self._cond:
                    self._log_line(f"Internal error on {job.info.title}: {e}")
            finally:
                with self._cond:
                    still_current = self._current is job
                if still_current:
                    self._finish()

    def _set_stage(self, stage: Stage) -> None:
        with self._cond:
            self._stage = stage
            self._cond.notify_all()

    def _should_abort(self) -> bool:
        with self._cond:
            return self._abort

    def _cleanup_audio(self, video_id: str) -> None:
        for p in self.audio_dir.glob(f"{video_id}.*"):
            p.unlink(missing_ok=True)

    def _finish(self, requeue: Job | None = None) -> None:
        with self._cond:
            if requeue is not None:
                # stop() already paused the queue; a resume() since then must win.
                self._queue.appendleft(requeue)
            else:
                self._done += 1
            self._current = None
            self._stage = "idle"
            self._cond.notify_all()

    def _process(self, job: Job) -> None:
        info = job.info
        requeue: Job | None = None
        try:
            if not info.available:
                self._mark_failed(info, info.error or "Unavailable")
                return
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            audio = self._download(info.id, self.audio_dir, self._should_abort)
            tx = self._transcriber(job.model)
            first_use = job.model not in self._seen_models
            if first_use:
                if not self._model_cached(job.model):
                    with self._cond:
                        self._log_line(
                            f"Downloading Whisper model {job.model} (first use, ~1.5–3 GB)…"
                        )
                    self._set_stage("loading_model")
                else:
                    self._set_stage("transcribing")
            else:
                self._set_stage("transcribing")
            with self._cond:
                self._log_line(f"Transcribing {info.title}")
            result = tx.transcribe(audio, job.language)
            self._seen_models.add(job.model)
            self.library.write_transcript(info, result, job.model)
            with self._cond:
                self._log_line(f"Done {info.title}")
        except Aborted:
            requeue = job
            with self._cond:
                self._log_line(f"Download interrupted: {info.title}")
        except Exception as e:  # noqa: BLE001
            self._mark_failed(info, friendly_error(e))
        finally:
            try:
                self._cleanup_audio(info.id)
            except Exception as e:  # noqa: BLE001
                with self._cond:
                    self._log_line(f"Could not delete audio for {info.title}: {e}")
            self._finish(requeue)

    def _mark_failed(self, info: VideoInfo, msg: str) -> None:
        try:
            self.library.mark_failed(info, msg)
            line = f"Failed {info.title}: {msg}"
        except Exception as e:  # noqa: BLE001
            line = f"Failed {info.title}: {msg} (could not record: {e})"
        with self._cond:
            self._log_line(line)

    def _transcriber(self, model: str) -> Transcriber:
        tx = self._transcribers.get(model)
        if tx is None:
            tx = self._transcribers[model] = self._factory(model)
        return tx
