import threading
from datetime import date

import pytest

from ytt.config import Paths
from ytt.library import Library
from ytt.transcribe import TranscriptResult
from ytt.tq import Job, TranscriptionQueue
from ytt.youtube import Aborted, DownloadError, VideoInfo

M = "large-v3-turbo"


def _info(vid):
    return VideoInfo(id=vid, title=f"Title {vid}", url=f"https://youtu.be/{vid}",
                     channel="Chan", upload_date=date(2024, 3, 5), duration=60, language="en")


@pytest.fixture
def info():
    return _info("abc123")


@pytest.fixture
def info2():
    return _info("def456")


@pytest.fixture
def lib(tmp_path):
    paths = Paths(tmp_path / "root")
    paths.ensure_layout()
    return Library(paths)


@pytest.fixture
def audio_dir(tmp_path):
    d = tmp_path / "audio"
    d.mkdir()
    return d


class FakeTx:
    def __init__(self):
        self.calls = 0
        self.languages = []
        self.created = []

    def __call__(self, model_key):
        self.created.append(model_key)
        outer = self

        class _T:
            def transcribe(self, audio, language):
                assert audio.exists()
                outer.calls += 1
                outer.languages.append(language)
                return TranscriptResult("t", "en")

        return _T()


@pytest.fixture
def fake_tx():
    return FakeTx()


class Downloads:
    """Fake download: writes a dummy file; can fail or block per id."""

    def __init__(self, audio_dir):
        self.audio_dir = audio_dir
        self.failures = {}
        self.calls = []
        self.block = None  # threading.Event the first download waits on
        self.entered = threading.Event()
        self.meta = {}  # video id -> yt-dlp info dict returned with the audio

    def __call__(self, video_id, dest_dir, should_abort):
        self.calls.append(video_id)
        path = dest_dir / f"{video_id}.m4a"
        path.write_bytes(b"x")
        if video_id in self.failures:
            raise self.failures[video_id]
        if self.block is not None:
            self.entered.set()
            while not self.block.wait(0.01):
                if should_abort():
                    self.block = None  # a retry after abort does not block
                    raise Aborted()
            self.block = None
        return path, self.meta.get(video_id, {})


@pytest.fixture
def downloads(audio_dir):
    return Downloads(audio_dir)


class Fetches:
    """Fake fetch_video: unavailable unless a fresh info is set for the id."""

    def __init__(self):
        self.fresh = {}
        self.calls = []

    def __call__(self, video_id):
        self.calls.append(video_id)
        return self.fresh.get(video_id) or VideoInfo(
            id=video_id, title=video_id, url=f"https://youtu.be/{video_id}",
            available=False, error="Still private")


@pytest.fixture
def fetches():
    return Fetches()


@pytest.fixture
def q(lib, audio_dir, downloads, fake_tx, fetches):
    queue = TranscriptionQueue(lib, audio_dir, download=downloads, fetch=fetches,
                               transcriber_factory=fake_tx, model_cached=lambda k: True)
    yield queue
    queue.shutdown()
    assert not (queue._thread and queue._thread.is_alive())


@pytest.fixture
def failing_download_for(downloads):
    def _set(vid, exc):
        downloads.failures[vid] = exc
    return _set


@pytest.fixture
def download_spy(downloads):
    return downloads


@pytest.fixture
def blocking_download(downloads):
    downloads.block = threading.Event()
    return downloads


def test_enqueue_dedups_across_projects(q, lib, fake_tx, info):
    assert q.enqueue(Job(info, None, M, {"A"})) == "queued"
    assert q.enqueue(Job(info, None, M, {"B"})) == "merged"
    q.start()
    assert q.wait_idle(5)
    assert fake_tx.calls == 1 and lib.is_done(info.id)
    assert q.enqueue(Job(info, None, M, {"C"})) == "reused"
    snap = q.snapshot()
    assert (snap.done, snap.total, snap.stage) == (1, 1, "idle")


def test_force_retranscribes(q, lib, fake_tx, info):
    q.enqueue(Job(info, "es", M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    assert q.enqueue(Job(info, "es", M, {"A"}, force=True)) == "queued"
    assert q.wait_idle(5)
    assert fake_tx.calls == 2 and fake_tx.languages == ["es", "es"]
    assert fake_tx.created == [M]  # transcriber cached per model


def test_failure_does_not_stop_batch(q, lib, audio_dir, info, info2, failing_download_for):
    failing_download_for(info.id, DownloadError("Private video"))
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    assert lib.get(info.id).error == "Private video" and lib.is_done(info2.id)
    assert not any(audio_dir.iterdir())


def test_unavailable_refetched_then_marked_without_download(q, lib, download_spy, fetches, info):
    bad = VideoInfo(id=info.id, title="x", url=info.url, available=False, error="Removed")
    q.enqueue(Job(bad, None, M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    assert fetches.calls == [info.id] and download_spy.calls == []
    rec = lib.get(bad.id)
    assert (rec.error, rec.title) == ("Still private", "x")


def test_retry_recovers_video_that_became_available(q, lib, fetches, info):
    bad = VideoInfo(id=info.id, title=info.id, url=info.url, available=False, error="Private video")
    fetches.fresh[info.id] = info
    q.enqueue(Job(bad, None, M, {"A"}, force=True))
    q.start()
    assert q.wait_idle(5)
    assert lib.is_done(info.id) and lib.get(info.id).title == info.title


def test_download_metadata_fills_flat_playlist_info(q, lib, downloads):
    flat = VideoInfo(id="pl1", title="Flat title", url="https://youtu.be/pl1")
    downloads.meta["pl1"] = {"upload_date": "20240607", "language": "de", "channel": "Kanal",
                             "title": "Full title", "duration": 125}
    q.enqueue(Job(flat, None, M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    rec = lib.get("pl1")
    assert rec.path == "Kanal/2024-06-07_Full title [pl1].txt"
    assert (rec.upload_date, rec.youtube_language, rec.duration) == ("2024-06-07", "de", 125)
    assert rec.language_mismatch  # Whisper heard "en"


def test_stop_during_download_requeues(q, lib, audio_dir, blocking_download, info, info2):
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A"}))
    q.start()
    assert blocking_download.entered.wait(5)
    assert q.stage_of(info.id) == "downloading" and q.stage_of(info2.id) == "queued"
    q.stop()
    deadline = threading.Event()
    for _ in range(500):
        s = q.snapshot()
        if s.current is None:
            break
        deadline.wait(0.01)
    s = q.snapshot()
    assert s.paused and s.queued == [info.id, info2.id] and s.current is None
    assert lib.get(info.id) is None
    assert not any(audio_dir.iterdir())
    assert not q.wait_idle(0.05)  # paused with items is not idle
    q.resume()
    assert q.wait_idle(5)
    assert lib.is_done(info.id) and lib.is_done(info2.id)


def test_cancel_project_keeps_shared_jobs(q, info, info2):
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A", "B"}))
    assert q.cancel_project("A") == [info.id]
    assert q.snapshot().queued == [info2.id]
    assert q.snapshot().total == 1


def test_cancel_project_limited_to_videos(q, info, info2):
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A"}))
    assert q.cancel_project("A", {info2.id}) == [info2.id]
    assert q.snapshot().queued == [info.id]


def test_first_use_model_download_stage(lib, audio_dir, downloads, info):
    stages = []

    class Tx:
        def transcribe(self, audio, language):
            stages.append(queue.stage_of(info.id))
            return TranscriptResult("t", "en")

    queue = TranscriptionQueue(lib, audio_dir, download=downloads,
                               transcriber_factory=lambda k: Tx(), model_cached=lambda k: False)
    try:
        queue.enqueue(Job(info, None, M, {"A"}))
        queue.start()
        assert queue.wait_idle(5)
        assert stages == ["loading_model"]
        assert any("first use" in line for line in queue.snapshot().log)
    finally:
        queue.shutdown()


def test_mark_failed_raising_does_not_kill_worker(q, lib, info, info2, failing_download_for, monkeypatch):
    failing_download_for(info.id, DownloadError("Private video"))
    real = lib.mark_failed
    calls = []

    def flaky(i, err):
        calls.append(i.id)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(i, err)

    monkeypatch.setattr(lib, "mark_failed", flaky)
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    assert lib.is_done(info2.id)
    assert any("could not record" in line for line in q.snapshot().log)


def test_cleanup_raising_still_goes_idle(q, lib, info, info2, monkeypatch):
    def boom(_vid):
        raise PermissionError("nope")

    monkeypatch.setattr(q, "_cleanup_audio", boom)
    q.enqueue(Job(info, None, M, {"A"}))
    q.enqueue(Job(info2, None, M, {"A"}))
    q.start()
    assert q.wait_idle(5)
    assert lib.is_done(info.id) and lib.is_done(info2.id)


def test_stop_then_resume_during_download_keeps_running(q, lib, blocking_download, info):
    q.enqueue(Job(info, None, M, {"A"}))
    q.start()
    release = blocking_download.block
    assert blocking_download.entered.wait(5)
    q.stop()
    q.resume()
    release.set()
    assert q.wait_idle(5)
    assert not q.snapshot().paused
    assert lib.is_done(info.id)
