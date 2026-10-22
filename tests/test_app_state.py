from datetime import date

import pytest

from ytt.app_state import AppState
from ytt.claude_client import ClaudeError
from ytt.config import Paths, Settings, app_support_dir
from ytt.projects import Source
from ytt.urls import classify, video_url
from ytt.youtube import VideoInfo


class FakeQueue:
    def __init__(self, library, audio_dir):
        self.jobs = []
        self.jobs_running = set()
        self.started = False

    def enqueue(self, job):
        self.jobs.append(job)
        return "queued"

    def start(self):
        self.started = True

    def stage_of(self, vid):
        if vid in self.jobs_running:
            return "loading_model" if vid.startswith("L") else "transcribing"
        if any(j.info.id == vid for j in self.jobs):
            return "queued"
        return None

    def cancel_project(self, slug, video_ids=None):
        hit = [j for j in self.jobs if slug in j.projects
               and (video_ids is None or j.info.id in video_ids)]
        self.jobs = [j for j in self.jobs if j not in hit]
        return [j.info.id for j in hit]


class FakeBackend:
    def __init__(self, error=None):
        self.error = error
        self.prompts = []

    def generate(self, prompt, model):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return "# Generated"


def info(vid):
    return VideoInfo(id=vid, title=f"T {vid}", url=video_url(vid), channel="Ch",
                     upload_date=date(2024, 1, 1))


@pytest.fixture
def state(tmp_path):
    return AppState(Paths(tmp_path / "data_root"), queue_factory=FakeQueue,
                    expand=lambda p, f, t: [info(p.video_id or "x")])


@pytest.fixture
def make_project(state):
    state.paths.ensure_layout()
    state.doctypes.seed_defaults()

    def make(videos, done=(), failed=(), cancelled=(), name="P", language=None):
        p = state.projects.create(name)
        p.language = language
        p.sources.append(Source(kind="playlist", url="u", title="S",
                                videos=[info(v) for v in videos]))
        p.cancelled = set(cancelled)
        state.projects.save(p)
        for v in done:
            state.library.write_transcript(info(v), type("R", (), {"text": f"text {v}", "language": "en"})(), "large-v3-turbo")
        for v in failed:
            state.library.mark_failed(info(v), "boom")
        return p.slug

    return make


@pytest.fixture
def fake_backend(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr("ytt.app_state.backend_for", lambda s: backend)
    return backend


@pytest.fixture
def failing_backend(monkeypatch):
    monkeypatch.setattr("ytt.app_state.backend_for", lambda s: FakeBackend(ClaudeError("nope")))


def test_startup_requeues_only_pending(state, make_project):
    make_project(videos=["a", "b", "c", "d"], done=["a"], failed=["b"], cancelled=["d"])
    state.startup()
    assert [j.info.id for j in state.queue.jobs] == ["c"]
    assert state.queue.started


def test_startup_clears_audio_cache(state):
    state.paths.ensure_layout()
    (state.paths.audio_cache / "x.m4a").write_text("x")
    state.startup()
    assert list(state.paths.audio_cache.iterdir()) == []


def test_job_language_and_model(state, make_project):
    make_project(videos=["a"], language="fr")
    make_project(videos=["z"], name="Q")
    state.settings.default_language = "de"
    state.settings.whisper_model = "large-v3"
    state.requeue_pending()
    langs = {j.info.id: j.language for j in state.queue.jobs}
    assert langs == {"a": "fr", "z": "de"}
    assert all(j.model == "large-v3" for j in state.queue.jobs)
    state.queue.jobs.clear()
    state.settings.default_language = "auto"
    make_project(videos=["q"], name="R")
    state.requeue_pending()
    assert {j.language for j in state.queue.jobs if j.info.id == "q"} == {None}


def test_status_mapping(state, make_project):
    slug = make_project(videos=["a", "b", "c", "d", "e", "Lf", "g"], done=["a"],
                        failed=["b"], cancelled=["d"])
    state.queue.jobs.append(type("J", (), {"info": info("c")})())
    state.queue.jobs_running = {"e", "Lf"}
    assert state.video_status(slug) == {
        "a": "done", "b": "failed", "c": "queued", "d": "cancelled",
        "e": "transcribing", "Lf": "transcribing", "g": "queued",
    }


def test_done_beats_cancelled(state, make_project):
    # cancelled here, but finished by another project: it is included in generation
    slug = make_project(videos=["a"], done=["a"], cancelled=["a"])
    assert state.video_status(slug) == {"a": "done"}


def test_video_status_is_fast_for_large_projects(state, make_project):
    import time
    from dataclasses import asdict
    from ytt.library import VideoRecord
    from ytt.storage import atomic_write_json

    ids = [f"v{n:04d}" for n in range(1000)]
    slug = make_project(videos=ids[:300])
    videos = {}
    for n, vid in enumerate(ids):
        rel = f"Ch/{vid}.txt"
        if n % 2 == 0:
            (state.paths.transcripts / rel).parent.mkdir(parents=True, exist_ok=True)
            (state.paths.transcripts / rel).write_text("t")
        videos[vid] = asdict(VideoRecord(vid, vid, "Ch", "u", None, None,
                                         "done" if n % 2 == 0 else "failed", path=rel))
    atomic_write_json(state.paths.index, {"version": 1, "videos": videos})
    start = time.perf_counter()
    statuses = state.video_status(slug)
    elapsed = time.perf_counter() - start
    assert statuses["v0000"] == "done" and statuses["v0001"] == "failed"
    assert elapsed < 0.05, elapsed


def test_retry_failed_forces_and_clears_cancelled(state, make_project):
    slug = make_project(videos=["a", "b", "c"], done=["a"], failed=["b"], cancelled=["c"])
    assert state.retry_failed(slug) == 2
    assert {j.info.id for j in state.queue.jobs} == {"b", "c"}
    assert all(j.force for j in state.queue.jobs)
    assert state.projects.get(slug).cancelled == set()


def test_cancel_queued_records_ids(state, make_project):
    slug = make_project(videos=["a", "b"])
    state.requeue_pending()
    assert sorted(state.cancel_queued(slug)) == ["a", "b"]
    assert state.projects.get(slug).cancelled == {"a", "b"}
    assert state.video_status(slug) == {"a": "cancelled", "b": "cancelled"}


def test_retranscribe_targets_all_projects(state, make_project):
    s1 = make_project(videos=["a"], name="One")
    s2 = make_project(videos=["a", "b"], name="Two")
    state.retranscribe("a", "large-v2", "es")
    (job,) = state.queue.jobs
    assert job.projects == {s1, s2} and job.force
    assert (job.model, job.language) == ("large-v2", "es")


def test_preview_source(state):
    out = state.preview_source("junk\nhttps://youtu.be/abc", None, None)
    assert out[0][1] == [] and out[0][0].kind == "invalid"
    assert [v.id for v in out[1][1]] == ["abc"] and out[1][2] is None


def test_preview_channel_requires_date(state):
    with pytest.raises(ValueError, match="start date"):
        state.preview_source("https://www.youtube.com/@foo", None, None)


def test_preview_expand_error_is_per_url(tmp_path):
    def boom(p, f, t):
        if p.video_id == "bad":
            raise RuntimeError("kaput")
        return [info(p.video_id)]

    st = AppState(Paths(tmp_path / "r"), queue_factory=FakeQueue, expand=boom)
    out = st.preview_source("https://youtu.be/bad https://youtu.be/ok", None, None)
    assert out[0][1] == [] and out[0][2] == "kaput"
    assert [v.id for v in out[1][1]] == ["ok"]


def test_add_sources_and_refresh(state, make_project):
    slug = make_project(videos=[])
    previews = [(classify("https://youtu.be/a"), [info("a")], None),
                (classify("junk"), [], None)]
    state.add_sources(slug, previews, "en")
    project = state.projects.get(slug)
    assert [s.kind for s in project.sources[-1:]] == ["video"]
    assert project.sources[-1].language == "en"
    assert [j.info.id for j in state.queue.jobs] == ["a"]
    state.queue.jobs.clear()
    state.add_sources(slug, [(classify("https://www.youtube.com/playlist?list=PL1"),
                              [info("p1")], None)], None)
    sid = state.projects.get(slug).sources[-1].id
    state._expand = lambda p, f, t: [info("p1"), info("p2")]
    assert [v.id for v in state.refresh_source(slug, sid)] == ["p2"]
    assert state.queue.jobs[-1].info.id == "p2"


def test_generate_not_ready_while_queued(state, make_project):
    slug = make_project(videos=["a", "b"], done=["a"])
    state.queue.jobs_running = {"b"}
    assert state.ready_to_generate(slug)[0] is False


def test_not_ready_without_done_videos(state, make_project):
    slug = make_project(videos=["a"], failed=["a"])
    assert state.ready_to_generate(slug)[0] is False


def test_generate_excludes_failed_and_appends_sources(state, make_project, fake_backend):
    slug = make_project(videos=["a", "b"], done=["a"], failed=["b"])
    ok, left_out = state.ready_to_generate(slug)
    assert ok and [v.id for v in left_out] == ["b"]
    meta = state.generate(slug, "summary", "Summary")
    text, _ = state.documents.read(slug, meta.file)
    assert "watch?v=a" in text and "watch?v=b" not in text


def test_generate_replace_snapshots_history(state, make_project, fake_backend):
    slug = make_project(videos=["a"], done=["a"])
    meta = state.generate(slug, "summary", "Summary")
    meta2 = state.generate(slug, "summary", "ignored", replace_file=meta.file)
    assert meta2.file == meta.file
    assert len(state.projects.get(slug).documents) == 1
    assert any((state.projects.project_dir(slug) / ".history").iterdir())


def test_claude_error_leaves_existing_document_untouched(
    state, make_project, fake_backend, monkeypatch
):
    slug = make_project(videos=["a"], done=["a"])
    meta = state.generate(slug, "summary", "Summary")
    before, _ = state.documents.read(slug, meta.file)
    monkeypatch.setattr("ytt.app_state.backend_for", lambda s: FakeBackend(ClaudeError("nope")))
    with pytest.raises(ClaudeError):
        state.generate(slug, "summary", "Summary", replace_file=meta.file)
    after, _ = state.documents.read(slug, meta.file)
    assert after == before and len(state.projects.get(slug).documents) == 1


def test_claude_error_creates_nothing(state, make_project, failing_backend):
    slug = make_project(videos=["a"], done=["a"])
    with pytest.raises(ClaudeError):
        state.generate(slug, "summary", "Summary")
    assert state.projects.get(slug).documents == []


def test_update_settings_and_data_root(state, tmp_path):
    ref = state.settings
    state.update_settings(Settings(whisper_model="large-v2"))
    assert state.settings is ref and ref.whisper_model == "large-v2"
    assert (state.paths.root / "settings.json").exists()
    state.change_data_root(tmp_path / "elsewhere")
    assert str(tmp_path / "elsewhere") in (app_support_dir() / "config.json").read_text()
    assert state.paths.root == tmp_path / "data_root"


def test_add_sources_does_not_retry_existing_failed(state, make_project):
    slug = make_project(videos=["a", "b"], failed=["a"])
    state.add_sources(slug, [(classify("https://youtu.be/n"), [info("n")], None)], None)
    assert [j.info.id for j in state.queue.jobs] == ["n"]


def test_cancel_queued_records_shared_video(state, make_project):
    s1 = make_project(videos=["a"], name="One")
    s2 = make_project(videos=["a"], name="Two")
    state.requeue_pending()
    state.queue.jobs[0].projects = {s1, s2}
    state.cancel_queued(s1)
    assert state.projects.get(s1).cancelled == {"a"}


def test_not_ready_with_unenqueued_pending_video(state, make_project):
    slug = make_project(videos=["a", "b"], done=["a"])
    assert state.ready_to_generate(slug)[0] is False


def test_generate_sends_only_done_transcripts_in_order(state, make_project, fake_backend):
    slug = make_project(videos=["c", "b", "a"], done=["a", "c"], failed=["b"])
    state.generate(slug, "summary", "S")
    prompt = fake_backend.prompts[0]
    assert "text a" in prompt and "text c" in prompt and "text b" not in prompt
    assert prompt.index("text c") < prompt.index("text a")


def test_source_language_beats_project_language(state, make_project):
    slug = make_project(videos=["a"], language="fr")
    p = state.projects.get(slug)
    p.sources[0].language = "es"
    state.projects.save(p)
    state.requeue_pending()
    assert state.queue.jobs[0].language == "es"


def test_delete_project_cancels_its_queued_jobs(state, make_project):
    slug = make_project(videos=["a", "b"])
    state.requeue_pending()
    state.delete_project(slug)
    assert state.queue.jobs == [] and state.projects.list() == []


def test_remove_source_cancels_only_videos_left_without_a_source(state, make_project):
    slug = make_project(videos=["a", "b"])
    p = state.projects.get(slug)
    p.sources.append(Source(kind="video", url=video_url("b"), title="B", videos=[info("b")]))
    p.sources.append(Source(kind="video", url=video_url("c"), title="C", videos=[info("c")]))
    state.projects.save(p)
    state.requeue_pending()
    state.remove_source(slug, p.sources[0].id)
    assert {j.info.id for j in state.queue.jobs} == {"b", "c"}


def test_retranscribe_refuses_video_already_in_queue(state, make_project):
    make_project(videos=["a"])
    state.requeue_pending()
    with pytest.raises(ValueError, match="already in the queue"):
        state.retranscribe("a", "large-v2", None)
    assert len(state.queue.jobs) == 1 and not state.queue.jobs[0].force
