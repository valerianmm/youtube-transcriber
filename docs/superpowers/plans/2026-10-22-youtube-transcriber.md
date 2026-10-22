# YouTube Transcriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local NiceGUI app that gathers YouTube sources into projects, transcribes each video once on the Apple GPU into a shared library, and has Claude write one editable, exportable (.md/.pdf) combined document per project from user-editable document types.

**Architecture:** A Python package `ytt/` of small, UI-free modules (storage, config, URLs, YouTube, transcription, library, projects, queue, document types, Claude, generation, documents) wired together by one `AppState` object. A single background thread runs the global transcription queue. NiceGUI pages under `ytt/ui/` only read `AppState` and call its methods, and poll a queue snapshot once a second for live progress.

**Tech Stack:** Python 3.12 (uv-managed), NiceGUI (on FastAPI), mlx-whisper, yt-dlp, claude-agent-sdk, anthropic, markdown, WeasyPrint, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-youtube-transcriber-design.md`

## Global Constraints

- Python pinned via `.python-version` = `3.12`; `requires-python = ">=3.11,<3.13"`. Run everything through `uv run`.
- No database. All state is plain files written **only** through `ytt/storage.py` (atomic temp+fsync+`os.replace`).
- Data root default `~/YouTube Transcriber/`; pointer file `~/Library/Application Support/YouTube Transcriber/config.json`; both overridable in tests by env vars `YTT_APP_SUPPORT` and `YTT_DATA_ROOT`.
- Whisper models (key → HF repo): `large-v3-turbo` → `mlx-community/whisper-large-v3-turbo` (default), `large-v3` → `mlx-community/whisper-large-v3-mlx`, `large-v2` → `mlx-community/whisper-large-v2-mlx`.
- Claude models: `claude-sonnet-5-5` (default), `claude-opus-5-5`. Claude mode default `"subscription"`, alternative `"api_key"`.
- Transcript text goes to Claude **only** inside `generate_document`, which is called only from the Generate button handler.
- Transcripts: no timestamps, no header. Document Sources section: title + YouTube URL, no timestamps, appended by the app.
- Channel sources: Videos tab only. Playlists: all entries.
- Audio deleted after each video (success or failure); `<data root>/.cache/audio/` cleared on startup.
- Unit tests never hit the network, GPU, or Claude. Real-service checks are marked `@pytest.mark.live` and excluded by default (`addopts = "-m 'not live'"`).
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Hostile video titles** (`/`, `:`, emoji, 300 chars, empty) must yield a valid, unique transcript filename. Test in Task 5 (`test_sanitize_hostile_titles`).
2. **The same video in two projects or two sources at once** must be transcribed exactly once. Test in Task 7 (`test_enqueue_dedups_across_projects`).
3. **Project names** that are empty, duplicates (case-insensitive), or contain `/` must be rejected or sanitized, never create a broken folder. Test in Task 6 (`test_create_rejects_bad_names`).
4. **A document edited outside the app while it's open in the editor** must raise a conflict, not be overwritten. Test in Task 10 (`test_save_detects_outside_edit`).
5. **Unavailable entries inside a playlist** (`[Private video]`, `[Deleted video]`) must become failed videos with a reason, not crash the listing. Test in Task 3 (`test_playlist_unavailable_entries_become_failed`).

---

## File Structure

```
pyproject.toml, .python-version, .gitignore, run.sh, "YouTube Transcriber.command", README.md
ytt/
  __main__.py        entry point: instance lock, AppState.startup(), ui.run
  storage.py         atomic writes, fingerprints/conflicts, per-file locks, history
  config.py          constants, Settings, Paths, data-root pointer, settings load/save
  instance.py        single-instance flock + running port
  urls.py            split pasted text, classify/normalize URLs
  youtube.py         yt-dlp: expand playlist/channel, fetch video, download audio, friendly errors
  transcribe.py      mlx-whisper wrapper, segments→paragraphs
  library.py         shared transcripts + index.json
  projects.py        Project/Source model, ProjectStore (project.json)
  tq.py              global TranscriptionQueue (background thread)
  doctypes.py        template format + DocTypeStore + default types
  claude_client.py   subscription / API-key backends behind one interface
  generate.py        prompt building, large-project fallback, Sources section
  documents.py       DocumentStore: create/read/save/history/export
  pdf.py             Markdown → PDF
  app_state.py       wires everything; statuses, enqueue, retry, re-transcribe, recovery
  ui/__init__.py     register_pages(state)
  ui/shell.py        shared frame: sidebar, queue bar
  ui/project_page.py sources + videos
  ui/documents_panel.py generate, editor, export
  ui/doctypes_page.py
  ui/settings_page.py
tests/  test_<module>.py per module, conftest.py (tmp data root + app-support fixtures, fakes)
```

(`tq.py` rather than `queue.py` to avoid shadowing the stdlib `queue` module.)

---

### Task 1: Project setup and safe storage

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `ytt/__init__.py`, `ytt/storage.py`, `tests/conftest.py`, `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `class ConflictError(Exception)` with attrs `path: Path`, `current: Fingerprint | None`
  - `@dataclass(frozen=True) class Fingerprint: mtime_ns: int; size: int; sha256: str`
  - `ANY` sentinel (skip conflict check)
  - `fingerprint(path: Path) -> Fingerprint | None`
  - `read_text(path: Path) -> tuple[str, Fingerprint]`; `read_json(path: Path) -> tuple[Any, Fingerprint]`
  - `atomic_write_text(path: Path, text: str, *, expect: Fingerprint | None | object = ANY) -> Fingerprint` (`expect=None` means "file must not exist")
  - `atomic_write_json(path: Path, obj: Any, *, expect=ANY) -> Fingerprint` (indent=2, utf-8, `ensure_ascii=False`)
  - `file_lock(path: Path) -> threading.Lock` (one lock per resolved path, from a module-level dict)
  - `save_history(path: Path, history_dir: Path, keep: int = 20) -> Path | None`

- [ ] **Step 1: Create the project files**

`pyproject.toml`: name `youtube-transcriber`; deps `nicegui`, `mlx-whisper`, `yt-dlp`, `anthropic`, `claude-agent-sdk`, `markdown`, `weasyprint`; dev group `pytest`, `pytest-asyncio`; `[tool.pytest.ini_options]` with `markers = ["live: hits network/GPU/Claude"]`, `addopts = "-m 'not live'"`, `asyncio_mode = "auto"`. `.gitignore`: `.venv/`, `__pycache__/`, `.superpowers/`, `.pytest_cache/`. `tests/conftest.py`: an autouse fixture setting `YTT_APP_SUPPORT` and `YTT_DATA_ROOT` to subfolders of `tmp_path`.

Run: `uv sync && uv run python -c "import nicegui, mlx_whisper, yt_dlp, anthropic, claude_agent_sdk, markdown"`
Expected: no error.

- [ ] **Step 2: Write the failing tests** in `tests/test_storage.py`

```python
def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "a" / "x.json"
    fp = atomic_write_json(p, {"k": "é"})
    data, fp2 = read_json(p)
    assert data == {"k": "é"} and fp == fp2
    assert not list(p.parent.glob("*.tmp"))

def test_write_with_stale_expect_raises(tmp_path):
    p = tmp_path / "doc.md"
    fp = atomic_write_text(p, "one")
    p.write_text("edited outside")
    with pytest.raises(ConflictError):
        atomic_write_text(p, "two", expect=fp)
    assert p.read_text() == "edited outside"

def test_expect_none_requires_absent(tmp_path):
    p = tmp_path / "n.md"; p.write_text("x")
    with pytest.raises(ConflictError):
        atomic_write_text(p, "y", expect=None)

def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"; atomic_write_text(p, "old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_text(p, "new")
    assert p.read_text() == "old"
    assert not list(tmp_path.glob("*.tmp"))

def test_history_keeps_last_n(tmp_path):
    p = tmp_path / "d.md"; h = tmp_path / ".history"
    for i in range(5):
        atomic_write_text(p, f"v{i}"); save_history(p, h, keep=3)
    kept = sorted((h / "d").iterdir())
    assert [k.read_text() for k in kept] == ["v2", "v3", "v4"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 4: Implement `ytt/storage.py`**

Temp file `.<name>.<random>.tmp` in the same directory; `flush` + `os.fsync`, then `os.replace`; delete the temp in `finally` on failure; fsync the directory after the replace. The conflict check and the write happen under `file_lock(path)`. History filename: `<stem>/<YYYYmmdd-HHMMSS-ffffff><suffix>`; prune the oldest beyond `keep`; return `None` if `path` doesn't exist.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore ytt tests
git commit -m "feat: project setup and atomic, conflict-checked storage"
```

---

### Task 2: Config, settings, and single-instance lock

**Files:**
- Create: `ytt/config.py`, `ytt/instance.py`, `tests/test_config.py`, `tests/test_instance.py`

**Interfaces:**
- Consumes: `atomic_write_json`, `read_json` (Task 1)
- Produces (`ytt/config.py`):
  - `WHISPER_MODELS: dict[str, str]`, `CLAUDE_MODELS: dict[str, str]` (label → id: `{"Sonnet": "claude-sonnet-5-5", "Opus": "claude-opus-5-5"}`), `LANGUAGES: dict[str, str]` (code → name; `"auto"` → `"Auto-detect"` first, then ~20 common languages)
  - `app_support_dir() -> Path` (env `YTT_APP_SUPPORT` or `~/Library/Application Support/YouTube Transcriber`)
  - `load_data_root() -> Path` (env `YTT_DATA_ROOT`, else the pointer `config.json["data_root"]`, else `~/YouTube Transcriber`); `set_data_root(path: Path) -> None`
  - `@dataclass class Paths: root: Path` with properties `transcripts`, `index` (`transcripts/index.json`), `doc_types`, `projects`, `settings_file`, `audio_cache` (`root/.cache/audio`); `ensure_layout() -> None`
  - `@dataclass class Settings: whisper_model="large-v3-turbo"; default_language="auto"; claude_mode="subscription"; api_key=""; claude_model="claude-sonnet-5-5"`
  - `load_settings(paths: Paths) -> Settings` (missing or unknown keys → defaults); `save_settings(paths: Paths, s: Settings) -> None` (chmod 0600, because it holds the API key)
- Produces (`ytt/instance.py`): `class InstanceLock: acquire(port: int) -> bool; release() -> None`; `running_port() -> int | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_settings_defaults_and_roundtrip(tmp_path):
    paths = Paths(tmp_path); paths.ensure_layout()
    assert load_settings(paths) == Settings()
    s = Settings(claude_mode="api_key", api_key="sk-x")
    save_settings(paths, s)
    assert load_settings(paths) == s
    assert oct(paths.settings_file.stat().st_mode)[-3:] == "600"

def test_data_root_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("YTT_DATA_ROOT")
    set_data_root(tmp_path / "elsewhere")
    assert load_data_root() == tmp_path / "elsewhere"

def test_second_lock_fails_and_reports_port():
    a, b = InstanceLock(), InstanceLock()
    assert a.acquire(8765) is True
    assert b.acquire(9999) is False
    assert running_port() == 8765
    a.release()
    assert b.acquire(9999) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_instance.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `ytt/config.py` and `ytt/instance.py`**

The lock uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `app_support_dir()/instance.lock` (the OS releases it when the process dies, so stale locks take care of themselves); the port is written to `instance.json` beside it. `running_port()` returns the port only if the lock is currently held by someone else (try a non-blocking flock and release it immediately).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_instance.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/config.py ytt/instance.py tests/test_config.py tests/test_instance.py
git commit -m "feat: settings, data-root pointer, single-instance lock"
```

---

### Task 3: URL parsing and YouTube access

**Files:**
- Create: `ytt/urls.py`, `ytt/youtube.py`, `tests/test_urls.py`, `tests/test_youtube.py`

**Interfaces:**
- Produces (`ytt/urls.py`):
  - `@dataclass(frozen=True) class ParsedUrl: kind: Literal["video","playlist","channel","invalid"]; url: str; video_id: str | None = None`
  - `split_urls(text: str) -> list[str]`: split on newlines/spaces/commas, drop blank lines and lines starting with `#`, dedupe keeping order
  - `classify(url: str) -> ParsedUrl`: `watch?v=`, `youtu.be/ID`, `/shorts/ID`, `/live/ID` → video (canonical `https://www.youtube.com/watch?v=ID`; a `watch` URL that also has `list=` counts as a **video**, my call); `/playlist?list=` → playlist; `/@handle`, `/channel/UC…`, `/c/name`, `/user/name` (with any trailing tab) → channel, normalized to `https://www.youtube.com/<base>/videos`; everything else → invalid
  - `video_url(video_id: str) -> str`
- Produces (`ytt/youtube.py`):
  - `@dataclass class VideoInfo: id: str; title: str; url: str; channel: str = ""; upload_date: date | None = None; duration: int | None = None; language: str | None = None; available: bool = True; error: str | None = None` with `to_dict() -> dict` / `from_dict(d) -> VideoInfo` (dates as ISO strings)
  - `expand(parsed: ParsedUrl, date_from: date | None = None, date_to: date | None = None) -> list[VideoInfo]`: dispatches to `fetch_video` / `list_playlist` / `list_channel`
  - `take_in_range(infos: Iterable[VideoInfo], start: date, end: date | None) -> list[VideoInfo]`: input is newest first; skip entries after `end`; **stop** at the first entry before `start`; include entries with no date
  - `download_audio(video_id: str, dest_dir: Path, should_abort: Callable[[], bool]) -> Path`; raises `DownloadError(reason: str)` or `Aborted`
  - `friendly_error(exc: BaseException) -> str`
  - Seam for tests: `_ydl(opts: dict) -> yt_dlp.YoutubeDL` (tests monkeypatch it with a fake whose `extract_info` returns canned dicts)

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("url,kind,norm", [
    ("https://youtu.be/abc123def45", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/shorts/abc123def45", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/watch?v=abc123def45&list=PLx", "video", "https://www.youtube.com/watch?v=abc123def45"),
    ("https://www.youtube.com/playlist?list=PLx", "playlist", "https://www.youtube.com/playlist?list=PLx"),
    ("https://www.youtube.com/@TechWorld/streams", "channel", "https://www.youtube.com/@TechWorld/videos"),
    ("https://www.youtube.com/channel/UC123", "channel", "https://www.youtube.com/channel/UC123/videos"),
    ("https://example.com/x", "invalid", None),
])
def test_classify(url, kind, norm): ...

def test_split_urls_messy():
    text = "# my list\nhttps://youtu.be/a1, https://youtu.be/b2  https://youtu.be/a1\n\n"
    assert split_urls(text) == ["https://youtu.be/a1", "https://youtu.be/b2"]

def test_take_in_range_stops_early():
    infos = [vi("n", date(2026,4,1)), vi("a", date(2026,3,1)), vi("b", date(2026,1,15)),
             vi("old", date(2025,12,1)), Boom()]  # Boom raises if iterated
    got = take_in_range(iter(infos), date(2026,1,1), date(2026,3,31))
    assert [i.id for i in got] == ["a", "b"]

def test_playlist_unavailable_entries_become_failed(fake_ydl):
    fake_ydl.playlist([{"id": "ok1", "title": "Good"}, {"id": "p1", "title": "[Private video]"},
                       {"id": "d1", "title": "[Deleted video]"}])
    infos = list_playlist("https://www.youtube.com/playlist?list=PLx")
    assert [(i.id, i.available) for i in infos] == [("ok1", True), ("p1", False), ("d1", False)]
    assert infos[1].error == "Private video"

@pytest.mark.parametrize("msg,friendly", [
    ("ERROR: Private video. Sign in", "Private video"),
    ("Join this channel to get access to members-only content", "Members-only"),
    ("Sign in to confirm your age", "Age-restricted"),
    ("The uploader has not made this video available in your country", "Region-blocked"),
    ("Video unavailable", "Removed or unavailable"),
])
def test_friendly_error(msg, friendly):
    assert friendly_error(DownloadError(msg)) == friendly
```

Add one `@pytest.mark.live` test that expands a real short public playlist.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_urls.py tests/test_youtube.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `ytt/urls.py` and `ytt/youtube.py`**

Playlist: `extract_flat="in_playlist"`; entries titled `[Private video]` / `[Deleted video]` (or with no title) → `available=False` with error `"Private video"` / `"Removed or unavailable"`. Channel: `extract_flat=False` with `lazy_playlist=True` so entries arrive newest first with `upload_date`, fed through `take_in_range` (which stops the iteration, so old history isn't crawled). Download: `format="bestaudio/best"`, `outtmpl=dest_dir/"%(id)s.%(ext)s"`, a progress hook that raises `Aborted` when `should_abort()` is true; `quiet=True`, `noprogress=True`. `friendly_error` falls back to the exception's first line, truncated to 200 characters.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_urls.py tests/test_youtube.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/urls.py ytt/youtube.py tests/test_urls.py tests/test_youtube.py
git commit -m "feat: URL classification and yt-dlp source expansion/download"
```

---

### Task 4: Whisper wrapper

**Files:**
- Create: `ytt/transcribe.py`, `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `WHISPER_MODELS` (Task 2)
- Produces:
  - `@dataclass class TranscriptResult: text: str; language: str`
  - `segments_to_paragraphs(segments: list[dict]) -> str`: join stripped segment texts with spaces; start a new paragraph (`"\n\n"`) when the gap from the previous segment's `end` to this `start` is > 2.0 s, or when the paragraph is ≥ 600 chars and the previous segment ended with `.`, `?` or `!`
  - `class Transcriber: __init__(self, model_key: str)`; `transcribe(self, audio: Path, language: str | None) -> TranscriptResult` (`language=None` → auto-detect; result language from `result["language"]`)

- [ ] **Step 1: Write the failing tests**

```python
def seg(s, e, t): return {"start": s, "end": e, "text": t}

def test_paragraph_on_gap():
    assert segments_to_paragraphs([seg(0,2," Hi."), seg(2,4," There."), seg(7,9," Next.")]) == "Hi. There.\n\nNext."

def test_paragraph_on_length_at_sentence_end():
    long = "x" * 600 + "."
    assert segments_to_paragraphs([seg(0,1,long), seg(1,2,"Then"), seg(2,3,"more.")]) == long + "\n\nThen more."

def test_transcriber_passes_repo_and_language(monkeypatch):
    calls = {}
    def fake(path, **kw):
        calls.update(kw); return {"language": "de", "segments": [seg(0,1,"Hallo.")]}
    monkeypatch.setattr("mlx_whisper.transcribe", fake)
    r = Transcriber("large-v3").transcribe(Path("a.m4a"), None)
    assert r == TranscriptResult("Hallo.", "de")
    assert calls["path_or_hf_repo"] == "mlx-community/whisper-large-v3-mlx" and calls["language"] is None
```

Add one `@pytest.mark.live` test that transcribes a 5-second generated tone or a fixture clip with `large-v3-turbo`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcribe.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/transcribe.py`**

Call `mlx_whisper.transcribe(str(audio), path_or_hf_repo=repo, language=language, verbose=None)`. mlx-whisper caches the loaded model between calls with the same repo, which satisfies "load once".

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcribe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/transcribe.py tests/test_transcribe.py
git commit -m "feat: mlx-whisper wrapper with paragraphing and language detection"
```

---

### Task 5: Shared transcript library

**Files:**
- Create: `ytt/library.py`, `tests/test_library.py`

**Interfaces:**
- Consumes: storage (Task 1), `Paths` (Task 2), `VideoInfo` (Task 3), `TranscriptResult` (Task 4)
- Produces:
  - `sanitize(name: str, max_len: int = 80) -> str`: strip `/\:*?"<>|` and control characters, collapse whitespace, trim dots/spaces, truncate; empty → `"untitled"`
  - `languages_mismatch(whisper: str | None, youtube: str | None) -> bool`: compares primary subtags (`en-US` → `en`); False if either is None
  - `@dataclass class VideoRecord: video_id, title, channel, url: str; upload_date: str | None; duration: int | None; status: Literal["done","failed"]; path: str | None` (relative to `transcripts/`)`; model: str | None; language: str | None; youtube_language: str | None; language_mismatch: bool; transcribed_at: str | None; error: str | None`
  - `class Library(paths: Paths)`: `get(video_id) -> VideoRecord | None`; `is_done(video_id) -> bool` (status done **and** the file exists); `transcript_path_for(info: VideoInfo) -> Path` = `transcripts/<sanitize(channel) or "Unknown channel">/<YYYY-MM-DD or "unknown-date">_<sanitize(title)> [<id>].txt`; `write_transcript(info, result, model_key) -> VideoRecord`; `mark_failed(info, error: str) -> VideoRecord` (never downgrades an existing `done` record); `read_transcript(video_id) -> str`; `cleanup_partials() -> list[str]` (deletes `*.txt.part`, returns their video ids parsed from `[id]`)

- [ ] **Step 1: Write the failing tests**

```python
def test_sanitize_hostile_titles():
    assert sanitize("a/b: c?*") == "ab c"
    assert sanitize("   ") == "untitled"
    assert len(sanitize("é" * 300)) == 80
    assert sanitize("🎉 Party!") == "🎉 Party!"

def test_write_then_reuse(lib, info):
    rec = lib.write_transcript(info, TranscriptResult("Hello.", "en"), "large-v3-turbo")
    assert lib.is_done(info.id) and lib.read_transcript(info.id) == "Hello."
    assert rec.path.endswith(f"[{info.id}].txt")
    assert not list(lib.paths.transcripts.rglob("*.part"))

def test_retranscribe_replaces_and_removes_old_file(lib, info):
    lib.write_transcript(info, TranscriptResult("old", "en"), "large-v3-turbo")
    old = lib.paths.transcripts / lib.get(info.id).path
    renamed = replace(info, title="New title")
    lib.write_transcript(renamed, TranscriptResult("neu", "de"), "large-v3")
    assert not old.exists() and lib.read_transcript(info.id) == "neu"
    assert lib.get(info.id).language == "de"

def test_mismatch_flag(lib, info):
    rec = lib.write_transcript(replace(info, language="en-US"), TranscriptResult("x", "es"), "large-v3-turbo")
    assert rec.language_mismatch is True

def test_failed_does_not_overwrite_done(lib, info):
    lib.write_transcript(info, TranscriptResult("x", "en"), "large-v3-turbo")
    lib.mark_failed(info, "Private video")
    assert lib.get(info.id).status == "done"

def test_cleanup_partials(lib, info):
    p = lib.transcript_path_for(info).with_suffix(".txt.part"); p.parent.mkdir(parents=True); p.write_text("half")
    assert lib.cleanup_partials() == [info.id] and not p.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_library.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/library.py`**

`write_transcript`: write `<path>.part` with fsync, `os.replace` to the final path, delete the previous record's file if its path differs, then update `index.json` with `atomic_write_json`. All index read-modify-write happens under one `threading.Lock` held by the instance.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_library.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/library.py tests/test_library.py
git commit -m "feat: shared transcript library with index and safe replace"
```

---

### Task 6: Projects

**Files:**
- Create: `ytt/projects.py`, `tests/test_projects.py`

**Interfaces:**
- Consumes: storage, `Paths`, `VideoInfo`, `sanitize`
- Produces:
  - `@dataclass class Source: id: str` (uuid4 hex[:8])`; kind: Literal["video","playlist","channel"]; url: str; title: str; language: str | None = None; date_from: str | None = None; date_to: str | None = None; videos: list[VideoInfo]`
  - `@dataclass class DocumentMeta: name: str; file: str; doc_type: str; generated_at: str; edited_at: str | None = None`
  - `@dataclass class Project: slug: str; name: str; language: str | None; sources: list[Source]; cancelled: set[str]; documents: list[DocumentMeta]`, with `videos() -> list[VideoInfo]` (deduped by id, first occurrence wins) and `forced_language_for(video_id) -> str | None` (the first source containing it with a language, else `project.language`)
  - `class ProjectStore(paths)`: `list() -> list[Project]` (sorted by name); `get(slug) -> Project`; `create(name) -> Project`; `rename(slug, new_name) -> Project`; `delete(slug) -> None`; `save(project) -> None`; `add_source(slug, source) -> Project`; `remove_source(slug, source_id) -> Project`; `merge_new_videos(slug, source_id, infos: list[VideoInfo]) -> list[VideoInfo]` (appends only unseen ids, returns them); `project_dir(slug) -> Path`
  - `project.json` holds `{"version": 1, "name", "language", "sources", "cancelled", "documents"}`
- Each `get` remembers the file's `Fingerprint`, and `save` passes it as `expect`, so an outside edit raises `ConflictError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_rejects_bad_names(store):
    store.create("Kubernetes")
    for bad in ["", "   ", "kubernetes"]:
        with pytest.raises(ValueError):
            store.create(bad)
    p = store.create("a/b: c")
    assert p.name == "a/b: c" and "/" not in p.slug and store.project_dir(p.slug).is_dir()

def test_rename_moves_folder_and_keeps_contents(store):
    p = store.create("Old"); (store.project_dir(p.slug) / "documents" / "x.md").write_text("hi")
    q = store.rename(p.slug, "New")
    assert (store.project_dir(q.slug) / "documents" / "x.md").read_text() == "hi"
    assert not store.project_dir(p.slug).exists()

def test_videos_dedup_and_forced_language(store):
    p = store.create("P")
    store.add_source(p.slug, src("s1", [vi("a"), vi("b")], language="de"))
    p = store.add_source(p.slug, src("s2", [vi("b"), vi("c")]))
    assert [v.id for v in p.videos()] == ["a", "b", "c"]
    assert p.forced_language_for("b") == "de" and p.forced_language_for("c") is None

def test_merge_new_videos_only_returns_unseen(store): ...

def test_outside_edit_conflicts(store):
    p = store.get(store.create("P").slug)
    (store.project_dir(p.slug) / "project.json").write_text('{"version":1,"name":"P","language":null,"sources":[],"cancelled":[],"documents":[]} ')
    p.language = "en"
    with pytest.raises(ConflictError):
        store.save(p)

def test_delete_moves_to_trash(store, tmp_path, monkeypatch): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_projects.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/projects.py`**

Slug = `sanitize(name, 60)`; case-insensitive uniqueness is checked against both existing names and slugs. Folder layout per project: `project.json`, `documents/`, `exports/`, `.history/`. `delete` moves the folder to `~/.Trash/<slug> <timestamp>` (my call: deletion stays recoverable; tests override the trash dir with env `YTT_TRASH_DIR`). `rename` uses `os.rename` of the folder, then saves the new name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_projects.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/projects.py tests/test_projects.py
git commit -m "feat: project store with sources and conflict-safe saves"
```

---

### Task 7: Global transcription queue

**Files:**
- Create: `ytt/tq.py`, `tests/test_tq.py`

**Interfaces:**
- Consumes: `Library` (Task 5), `download_audio`, `friendly_error`, `Aborted` (Task 3), `Transcriber` (Task 4)
- Produces:
  - `@dataclass class Job: info: VideoInfo; language: str | None; model: str; projects: set[str]; force: bool = False`
  - `@dataclass class QueueSnapshot: paused: bool; current: str | None` (video id)`; current_title: str | None; stage: Literal["idle","downloading","loading_model","transcribing"]; done: int; total: int; queued: list[str]; log: list[str]`
  - `class TranscriptionQueue(library: Library, audio_dir: Path, download=download_audio, transcriber_factory=Transcriber)`: `start() -> None` (daemon thread); `enqueue(job: Job) -> Literal["queued","merged","reused"]`; `stop() -> None`; `resume() -> None`; `cancel_project(slug: str) -> list[str]`; `snapshot() -> QueueSnapshot`; `stage_of(video_id) -> Literal["queued","downloading","transcribing"] | None`; `wait_idle(timeout: float) -> bool` (for tests)
- Semantics:
  - `enqueue`: if the video is already done in the library and `force` is False → `"reused"`. If the same id is already queued or running → add the job's projects to that job → `"merged"`. Otherwise append to the FIFO → `"queued"`.
  - `stop`: pause after the current video. A download in progress is aborted (`should_abort`), and the video goes back to the **front** of the queue, not marked failed. A transcription in progress finishes.
  - `cancel_project`: removes the slug from every queued (not running) job's `projects`, drops jobs left with no projects, and returns the dropped ids.
  - Unavailable `VideoInfo` (`available=False`) → `library.mark_failed(info, info.error)` without downloading.
  - Every exception → `library.mark_failed(info, friendly_error(e))`; the audio file is always deleted; the loop continues.
  - `done` / `total` count since the queue was last empty. `log` keeps the last 200 lines.

- [ ] **Step 1: Write the failing tests** (fakes: `download` writes a dummy file into `audio_dir`; the fake transcriber returns `TranscriptResult("t", "en")` and counts calls)

```python
def test_enqueue_dedups_across_projects(q, lib, fake_tx, info):
    assert q.enqueue(Job(info, None, "large-v3-turbo", {"A"})) == "queued"
    assert q.enqueue(Job(info, None, "large-v3-turbo", {"B"})) == "merged"
    q.start(); assert q.wait_idle(5)
    assert fake_tx.calls == 1 and lib.is_done(info.id)
    assert q.enqueue(Job(info, None, "large-v3-turbo", {"C"})) == "reused"

def test_force_retranscribes(q, lib, fake_tx, info): ...  # done first, then force=True → calls == 2

def test_failure_does_not_stop_batch(q, lib, info, info2, failing_download_for):
    failing_download_for(info.id, DownloadError("Private video"))
    q.enqueue(Job(info, None, "large-v3-turbo", {"A"})); q.enqueue(Job(info2, None, "large-v3-turbo", {"A"}))
    q.start(); q.wait_idle(5)
    assert lib.get(info.id).error == "Private video" and lib.is_done(info2.id)
    assert not any(q_audio_dir.iterdir())

def test_unavailable_marked_without_download(q, lib, download_spy, info): ...

def test_stop_during_download_requeues(q, blocking_download, info, info2):
    ...  # start, stop while the first download blocks → snapshot().paused, queued == [info.id, info2.id], nothing failed
    q.resume(); assert q.wait_idle(5)

def test_cancel_project_keeps_shared_jobs(q, info, info2):
    q.enqueue(Job(info, None, "m", {"A"})); q.enqueue(Job(info2, None, "m", {"A", "B"}))
    assert q.cancel_project("A") == [info.id]
    assert q.snapshot().queued == [info2.id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tq.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/tq.py`**

One `threading.Condition` guards the deque, the current job, and the flags. Before the first `transcribe` call for a model key whose repo is not yet in the Hugging Face cache (`huggingface_hub.try_to_load_from_cache(repo, "config.json")` is None), set stage `loading_model` and log "Downloading Whisper model <key> (first use, ~1.5–3 GB)…"; the queue bar shows it. The worker waits on the condition while paused or empty. Cache one `Transcriber` per model key. Language passed to `transcribe` = `job.language` (None means auto-detect).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tq.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/tq.py tests/test_tq.py
git commit -m "feat: global FIFO transcription queue with dedup, stop/resume, cancel"
```

---

### Task 8: Document types

**Files:**
- Create: `ytt/doctypes.py`, `tests/test_doctypes.py`

**Interfaces:**
- Consumes: storage, `Paths`, `sanitize`
- Produces:
  - `@dataclass class DocType: slug: str; name: str; description: str; body: str`
  - `parse_doctype(slug: str, text: str) -> DocType`; `render_doctype(dt: DocType) -> str`
  - `class DocTypeStore(paths)`: `list() -> list[DocType]`; `get(slug) -> tuple[DocType, Fingerprint]`; `save(dt, expect: Fingerprint) -> Fingerprint`; `create(name) -> DocType`; `duplicate(slug) -> DocType` (name `"<name> (copy)"`); `rename(slug, new_name) -> DocType`; `delete(slug) -> None` (moves the file to `doc_types/.trash/`); `seed_defaults() -> None` (writes the two defaults **only** if `doc_types/` has no `.md` files)
- File format (`document-types/<slug>.md`):

```
---
name: Step-by-step guide
description: A practical guide someone can follow from start to finish.
---
## Structure
...
## Instructions
...
```

  A file without front matter parses with `name` = slug and `body` = the whole text, so a hand-edited file never fails to load.

- [ ] **Step 1: Write the failing tests**

```python
def test_roundtrip():
    dt = DocType("summary", "Summary", "Short overview", "## Structure\n- TL;DR\n")
    assert parse_doctype("summary", render_doctype(dt)) == dt

def test_tolerates_missing_front_matter():
    dt = parse_doctype("notes", "Just write notes.")
    assert (dt.name, dt.body) == ("notes", "Just write notes.")

def test_seed_only_when_empty(store):
    store.seed_defaults()
    assert {d.slug for d in store.list()} == {"summary", "step-by-step-guide"}
    store.delete("summary"); store.seed_defaults()
    assert {d.slug for d in store.list()} == {"step-by-step-guide"}

def test_duplicate_rename_delete(store): ...
def test_save_conflict_on_outside_edit(store): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctypes.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/doctypes.py` with the default bodies**

Write the two defaults as module constants. **Summary**: Structure = Title, TL;DR (3–5 bullets), Key ideas (one `###` per idea), Notable details/numbers, Takeaways. **Step-by-step guide**: Structure = Title, Who this is for & prerequisites, Numbered steps (`##` per phase, numbered actions inside, commands in code blocks), Common mistakes, Next steps. Instructions for both: merge overlapping content across videos and don't repeat it per video; write as a standalone document, not "in this video…"; use only what the transcripts say; no timestamps; no Sources section (the app adds it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctypes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/doctypes.py tests/test_doctypes.py
git commit -m "feat: editable document types with Summary and Step-by-step defaults"
```

---

### Task 9: Claude backends and document generation

**Files:**
- Create: `ytt/claude_client.py`, `ytt/generate.py`, `tests/test_claude_client.py`, `tests/test_generate.py`

**Interfaces:**
- Consumes: `Settings` (Task 2), `VideoInfo` (Task 3), `DocType` (Task 8)
- Produces (`ytt/claude_client.py`):
  - `class ClaudeError(Exception)` (its message is shown to the user as is)
  - `class Backend(Protocol): def generate(self, prompt: str, model: str) -> str`
  - `class SubscriptionBackend`: if `shutil.which("claude")` is None → `ClaudeError("Claude Code isn't installed or isn't on PATH. Install it, or switch to an API key in Settings.")`; auth/CLI failures → `ClaudeError("Claude Code couldn't run (are you logged in? run `claude` once in Terminal). Details: …")`
  - `class ApiKeyBackend(api_key: str)`: empty key → `ClaudeError("No API key set. Add one in Settings.")`; API errors → `ClaudeError` with the API's message
  - `backend_for(settings: Settings) -> Backend`
- Produces (`ytt/generate.py`):
  - `CHAR_BUDGET = 400_000` (my call, roughly 100k tokens of transcript per request)
  - `build_prompt(dt: DocType, items: list[tuple[VideoInfo, str]]) -> str`
  - `sources_section(infos: list[VideoInfo]) -> str` → `"\n\n---\n\n## Sources\n\n" + "".join(f"- [{title}]({url})\n")`
  - `generate_document(backend: Backend, model: str, dt: DocType, items: list[tuple[VideoInfo, str]]) -> str` (returns the document body + `sources_section`)

- [ ] **Step 1: Write the failing tests**

```python
class FakeBackend:
    def __init__(self, reply="# Doc\nBody"): self.prompts = []; self.reply = reply
    def generate(self, prompt, model): self.prompts.append(prompt); return self.reply

def test_single_call_includes_template_and_all_transcripts():
    b = FakeBackend()
    out = generate_document(b, "claude-sonnet-5-5", DT, [(vi("a", "Intro"), "alpha text"), (vi("b", "Helm"), "beta text")])
    assert len(b.prompts) == 1
    p = b.prompts[0]
    assert DT.body in p and "alpha text" in p and "beta text" in p and "Intro" in p
    assert out.startswith("# Doc\nBody")
    assert out.endswith("## Sources\n\n- [Intro](https://www.youtube.com/watch?v=a)\n- [Helm](https://www.youtube.com/watch?v=b)\n")

def test_over_budget_uses_per_video_notes_then_combines(monkeypatch):
    monkeypatch.setattr(generate, "CHAR_BUDGET", 100)
    b = FakeBackend()
    generate_document(b, "m", DT, [(vi("a"), "x" * 80), (vi("b"), "y" * 80)])
    assert len(b.prompts) == 3 and "x" * 80 not in b.prompts[2]

def test_sources_escape_brackets_in_titles():
    assert "- [A \\[live\\]](https://www.youtube.com/watch?v=a)" in sources_section([vi("a", "A [live]")])

def test_api_backend_requires_key():
    with pytest.raises(ClaudeError, match="No API key"):
        ApiKeyBackend("").generate("p", "m")

def test_subscription_backend_missing_cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ClaudeError, match="isn't installed"):
        SubscriptionBackend().generate("p", "m")
```

Add `@pytest.mark.live` tests that call each backend for real with a tiny prompt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_client.py tests/test_generate.py -v`
Expected: FAIL

- [ ] **Step 3: Implement both modules**

Subscription: `claude_agent_sdk.query(prompt=..., options=ClaudeAgentOptions(model=model, allowed_tools=[], max_turns=1, system_prompt=SYSTEM))`, run with `anyio.run` from the calling thread, concatenating the `TextBlock`s of each `AssistantMessage`. API: `anthropic.Anthropic(api_key=...).messages.stream(model=model, max_tokens=16000, system=SYSTEM, messages=[...])` → `get_final_text()`. `SYSTEM` = "You write one combined Markdown document from YouTube transcripts, following the document type's structure and instructions. Output only the document." Prompt layout: the document type body, then `### Video N: <title>` + transcript per video. Over budget: one call per video ("Write detailed notes from this transcript that are relevant to the document type below"), then the normal prompt with the notes standing in for the transcripts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_client.py tests/test_generate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/claude_client.py ytt/generate.py tests/test_claude_client.py tests/test_generate.py
git commit -m "feat: Claude subscription/API backends and combined document generation"
```

---

### Task 10: Documents and export

**Files:**
- Create: `ytt/documents.py`, `ytt/pdf.py`, `tests/test_documents.py`

**Interfaces:**
- Consumes: storage, `ProjectStore`, `DocumentMeta` (Task 6)
- Produces (`ytt/pdf.py`): `class PdfUnavailable(Exception)`; `markdown_to_pdf(md: str, out: Path) -> None` (Markdown → HTML with the `tables`, `fenced_code`, `sane_lists` extensions, an embedded print CSS (A4, 2 cm margins, system sans-serif 11pt, code in monospace on light grey), then WeasyPrint; an import or OS error from WeasyPrint → `PdfUnavailable("PDF export needs pango: run `brew install pango`")`)
- Produces (`ytt/documents.py`) `class DocumentStore(projects: ProjectStore)`:
  - `create(slug, name, doc_type, markdown) -> DocumentMeta` (unique file `sanitize(name).md`, suffix ` 2`, ` 3`… if taken; adds meta to project.json)
  - `read(slug, file) -> tuple[str, Fingerprint]`
  - `save(slug, file, text, expect: Fingerprint) -> Fingerprint` (history snapshot first, then a conflict-checked write; sets `edited_at`)
  - `replace_generated(slug, file, text) -> Fingerprint` (history snapshot, unconditional write, sets `generated_at`, clears `edited_at`)
  - `delete(slug, file) -> None` (history snapshot, then remove the file and its meta)
  - `export_md(slug, file) -> Path`, `export_pdf(slug, file) -> Path` (into `exports/<stem>.md|.pdf`, atomic)

- [ ] **Step 1: Write the failing tests**

```python
def test_save_detects_outside_edit(docs, slug):
    meta = docs.create(slug, "Guide", "step-by-step-guide", "# G")
    text, fp = docs.read(slug, meta.file)
    (docs.projects.project_dir(slug) / "documents" / meta.file).write_text("# edited in VS Code")
    with pytest.raises(ConflictError):
        docs.save(slug, meta.file, "# mine", expect=fp)

def test_save_keeps_history(docs, slug):
    meta = docs.create(slug, "Guide", "t", "v1")
    _, fp = docs.read(slug, meta.file); fp = docs.save(slug, meta.file, "v2", expect=fp)
    docs.replace_generated(slug, meta.file, "v3")
    hist = sorted((docs.projects.project_dir(slug) / ".history" / "Guide").iterdir())
    assert [h.read_text() for h in hist] == ["v1", "v2"]

def test_unique_names(docs, slug):
    assert docs.create(slug, "Guide", "t", "a").file == "Guide.md"
    assert docs.create(slug, "Guide", "t", "b").file == "Guide 2.md"

def test_export_md_and_pdf(docs, slug):
    meta = docs.create(slug, "Guide", "t", "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert docs.export_md(slug, meta.file).read_text().startswith("# Title")
    pdf = docs.export_pdf(slug, meta.file)
    assert pdf.read_bytes()[:4] == b"%PDF"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/test_documents.py -v`
Expected: FAIL

- [ ] **Step 3: Install pango and implement both modules**

Run: `brew list pango >/dev/null 2>&1 || brew install pango`. Then implement as specified.

- [ ] **Step 4: Run tests to verify they pass**

Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest tests/test_documents.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/documents.py ytt/pdf.py tests/test_documents.py
git commit -m "feat: document store with history, conflict checks, md/pdf export"
```

---

### Task 11: AppState wiring and startup recovery

**Files:**
- Create: `ytt/app_state.py`, `tests/test_app_state.py`

**Interfaces:**
- Consumes: everything from Tasks 2–10
- Produces `class AppState(paths: Paths, *, queue_factory=TranscriptionQueue, expand=youtube.expand)` with attributes `paths, settings, library, projects, queue, doctypes, documents` and:
  - `startup() -> None`: `paths.ensure_layout()`, clear `audio_cache`, `library.cleanup_partials()`, `doctypes.seed_defaults()`, `requeue_pending()`, `queue.start()`
  - `requeue_pending() -> None`: for every project and every video not done in the library, not failed, and not in `project.cancelled` → enqueue
  - `preview_source(text: str, date_from: date | None, date_to: date | None) -> list[tuple[ParsedUrl, list[VideoInfo]]]` (invalid URLs come back with an empty list so the UI can show them)
  - `add_sources(slug, previews, language: str | None) -> None` (creates `Source`s and enqueues their videos)
  - `refresh_source(slug, source_id) -> list[VideoInfo]` (re-expand, `merge_new_videos`, enqueue the new ones)
  - `video_status(slug) -> dict[str, str]` → each video id maps to one of `"queued" | "downloading" | "transcribing" | "done" | "failed" | "cancelled"`
  - `retry_failed(slug) -> int` (re-enqueues the project's failed **and** cancelled videos with `force=True`, removes them from `cancelled`)
  - `cancel_queued(slug) -> list[str]` (`queue.cancel_project` + record the ids in `project.cancelled`)
  - `retranscribe(video_id, model: str, language: str | None) -> None` (`force=True`, projects = every project containing the video)
  - `ready_to_generate(slug) -> tuple[bool, list[VideoInfo]]` (True when nothing is queued or running for the project; the list = videos that will be left out because they failed or were cancelled)
  - `generate(slug, doc_type_slug, name: str, replace_file: str | None = None) -> DocumentMeta` (reads transcripts of done videos in project order, `backend_for(settings)`, `generate_document`, then `documents.create` or `replace_generated`)
  - `update_settings(new: Settings) -> None`; `change_data_root(path: Path) -> None` (`set_data_root` + tells the user a restart is required; my call: no hot-swap)
- Enqueued jobs use `language = project.forced_language_for(id)` (None → auto-detect, also when `settings.default_language == "auto"`; otherwise the default language) and `model = settings.whisper_model`.

- [ ] **Step 1: Write the failing tests** (with a fake queue that records jobs, a fake `expand`, and a `FakeBackend`)

```python
def test_startup_requeues_only_pending(state, make_project):
    slug = make_project(videos=["a", "b", "c"], done=["a"], failed=["b"])
    state.startup()
    assert [j.info.id for j in state.queue.jobs] == ["c"]

def test_status_mapping(state, make_project): ...
def test_retry_failed_forces_and_clears_cancelled(state, make_project): ...

def test_generate_not_ready_while_queued(state, make_project):
    slug = make_project(videos=["a", "b"], done=["a"]); state.queue.jobs_running = {"b"}
    assert state.ready_to_generate(slug)[0] is False

def test_generate_excludes_failed_and_appends_sources(state, make_project, fake_backend):
    slug = make_project(videos=["a", "b"], done=["a"], failed=["b"])
    ok, left_out = state.ready_to_generate(slug)
    assert ok and [v.id for v in left_out] == ["b"]
    meta = state.generate(slug, "summary", "Summary")
    text, _ = state.documents.read(slug, meta.file)
    assert "watch?v=a" in text and "watch?v=b" not in text

def test_claude_error_leaves_existing_document_untouched(state, make_project, failing_backend): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_state.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/app_state.py`**

- [ ] **Step 4: Run the whole suite**

Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add ytt/app_state.py tests/test_app_state.py
git commit -m "feat: AppState wiring, startup recovery, generation readiness"
```

---

### Task 12: App shell, settings page, entry point, launchers

**Files:**
- Create: `ytt/__main__.py`, `ytt/ui/__init__.py`, `ytt/ui/shell.py`, `ytt/ui/settings_page.py`, `run.sh`, `YouTube Transcriber.command`, `tests/test_ui_shell.py`

**Interfaces:**
- Consumes: `AppState` (Task 11), `InstanceLock`, `running_port` (Task 2)
- Produces:
  - `register_pages(state: AppState) -> None` (defines every `@ui.page`; later tasks add their pages through it)
  - `frame(state: AppState, active: str | None)`: a context manager that renders the left drawer (projects with "N videos · M docs", **+ New project** dialog with name validation errors shown inline, **Document types** and **Settings** links) and a top queue bar (current title, stage, `done/total`, **Stop**/**Resume**), refreshed by `ui.timer(1.0, …)` from `state.queue.snapshot()`
  - Routes: `/` (redirects to the first project, or shows "Create your first project"), `/settings`
  - `main() -> None` in `ytt/__main__.py`: port `8765`; if `InstanceLock().acquire(port)` fails → `webbrowser.open(f"http://localhost:{running_port()}")` and exit 0; else `AppState(Paths(load_data_root())).startup()`, `register_pages`, `ui.run(host="127.0.0.1", port=port, title="YouTube Transcriber", reload=False, show=True)`
- Settings page fields: data folder (text + **Change…**, with a "restart required" notice), Whisper model select (`WHISPER_MODELS` keys), default language select (`LANGUAGES`), Claude mode radio ("Use my Claude subscription (via Claude Code)" / "Use an API key"), API key password input (shown only in API-key mode), Claude model select (`CLAUDE_MODELS`), **Save**.
- `run.sh`: `cd "$(dirname "$0")"`; check `command -v ffmpeg` and `brew list pango`, printing the `brew install …` hint if missing; `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`; `uv sync --quiet`; `exec uv run python -m ytt`. Accept `--update-ytdlp` to run `uv lock --upgrade-package yt-dlp && uv sync` first. `.command` = `#!/bin/zsh` + `exec "$(dirname "$0")/run.sh"`. Both are `chmod +x`.

- [ ] **Step 1: Write failing UI tests** with NiceGUI's `user` fixture (set it up as the installed NiceGUI version's testing docs describe, pointing at a test entry that builds `AppState` on tmp dirs with a fake queue)

```python
async def test_create_project_from_sidebar(user):
    await user.open("/")
    await user.should_see("Create your first project")
    user.find("+ New project").click()
    user.find(marker="new-project-name").type("Kubernetes")
    user.find("Create").click()
    await user.should_see("Kubernetes")

async def test_duplicate_project_name_shows_error(user): ...
async def test_settings_save_persists(user, state): ...  # switch to API key mode, enter key, Save → load_settings(...).claude_mode == "api_key"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_shell.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the shell, settings page, `__main__`, and launchers**

- [ ] **Step 4: Run tests, then launch for real**

Run: `uv run pytest tests/test_ui_shell.py -v` → PASS. Then `./run.sh`: the browser opens on `http://localhost:8765` with the sidebar. Run `./run.sh` again in a second terminal: it just opens the browser and exits. Double-click `YouTube Transcriber.command` in Finder: the same behavior.

- [ ] **Step 5: Commit**

```bash
git add ytt/__main__.py ytt/ui run.sh "YouTube Transcriber.command" tests/test_ui_shell.py
git commit -m "feat: app shell, settings page, single-command launchers"
```

---

### Task 13: Project page: sources and videos

**Files:**
- Create: `ytt/ui/project_page.py`, `tests/test_ui_project.py`
- Modify: `ytt/ui/__init__.py` (register `/project/{slug}`)

**Interfaces:**
- Consumes: `AppState.preview_source/add_sources/refresh_source/video_status/retry_failed/cancel_queued/retranscribe`, `store.rename/delete/remove_source`, `library.get`
- Produces: route `/project/{slug}`; marker names used by tests: `add-urls`, `add-file`, `date-from`, `date-to`, `source-language`, `preview-confirm`
- Page contents (top to bottom):
  - Header: name, **Rename**, **Delete** (confirm dialog: "moves the project folder to the Trash; transcripts stay in the library"), project language select (Auto-detect + `LANGUAGES`)
  - Add-sources box: a textarea for URLs, an upload for a `.txt` URL list, a date-from/date-to pair shown when any pasted URL is a channel (date-from required; date-to empty = today), a source language select, **Preview** → dialog listing title/date/duration per video with the total count and total duration, invalid URLs listed in red, **Add N videos**
  - Sources list: an expandable row per source (title, kind, date range, "x of y transcribed") with **Refresh** (playlist/channel), **Remove**; inside, one row per video: title, status chip (`queued` / `downloading` / `transcribing` / `done` / `failed: <reason>` / `cancelled`), language badge (flag emoji + code, ⚠ with tooltip "Whisper heard <x>, YouTube says <y>" when `language_mismatch`), and a ⋯ menu → **Re-transcribe…** (dialog: model + language, warning "This replaces the transcript in every project that uses this video", list of those projects)
  - Footer actions: **Retry failed**, **Cancel queued**
  - Status refreshes through `ui.timer(1.0)` (update only changed chips)

- [ ] **Step 1: Write failing UI tests** (fake `expand` returns canned infos)

```python
async def test_paste_preview_add(user, state, slug):
    await user.open(f"/project/{slug}")
    user.find(marker="add-urls").type("https://www.youtube.com/playlist?list=PLx")
    user.find("Preview").click()
    await user.should_see("3 videos")
    user.find("Add 3 videos").click()
    await user.should_see("queued")

async def test_channel_requires_start_date(user, slug): ...
async def test_mismatch_badge_shown(user, state, slug): ...
async def test_retranscribe_dialog_lists_affected_projects(user, state, slug): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_project.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `ytt/ui/project_page.py`**

Run `preview_source` through `run.io_bound` so the page stays responsive while yt-dlp lists a channel; show a spinner in the dialog meanwhile.

- [ ] **Step 4: Run tests, then check by hand**

Run: `uv run pytest tests/test_ui_project.py -v` → PASS. Then `./run.sh`, create a project, and add a real short public playlist and a single video. Expect to see download → transcribing → done, with an `en` badge, and `.txt` files under `~/YouTube Transcriber/transcripts/<channel>/`.

- [ ] **Step 5: Commit**

```bash
git add ytt/ui/project_page.py ytt/ui/__init__.py tests/test_ui_project.py
git commit -m "feat: project page with source preview, statuses, languages, re-transcribe"
```

---

### Task 14: Documents panel and document-types page

**Files:**
- Create: `ytt/ui/documents_panel.py`, `ytt/ui/doctypes_page.py`, `tests/test_ui_documents.py`
- Modify: `ytt/ui/project_page.py` (mount the panel under the sources), `ytt/ui/__init__.py` (register `/doc-types`)

**Interfaces:**
- Consumes: `AppState.ready_to_generate/generate`, `documents.read/save/delete/export_md/export_pdf`, `doctypes.*`, `ConflictError`, `ClaudeError`, `PdfUnavailable`
- Produces: `documents_panel(state, slug)`; route `/doc-types`; markers `doc-editor`, `doctype-editor`
- Documents panel:
  - Left: the project's documents (name, type, "generated <date> · edited"), and **+ Generate new…** → dialog: document type select, name (defaults to the type's name), the left-out list from `ready_to_generate`, and a note that "The transcripts will be sent to Claude". **Generate** is disabled with "Waiting for N videos to finish" while not ready.
  - Generation runs through `run.io_bound` with a spinner. A `ClaudeError` shows its message in a dialog with a link to Settings.
  - Right: a Markdown editor (`ui.codemirror(language="Markdown")`) with **Save**, **Regenerate** (confirm: "the current version goes to history"), **Export .md**, **Export .pdf** (writes into `exports/` and then `ui.download`), **Delete**. Unsaved changes are flagged with a dot.
  - Save handles `ConflictError` with a dialog: **Overwrite** (save with `expect=ANY`), **Reload from disk**, **Cancel**.
- Document types page: a list on the left (**+ New type**, plus per type **Duplicate**, **Rename**, **Delete**); on the right, name, description, and a body editor (`doctype-editor`) with **Save**, using the same conflict dialog.

- [ ] **Step 1: Write failing UI tests**

```python
async def test_generate_disabled_until_ready(user, state, slug_with_queued_video): ...
async def test_generate_edit_save_export(user, state, ready_slug, fake_backend):
    await user.open(f"/project/{ready_slug}")
    user.find("+ Generate new…").click(); user.find("Generate").click()
    await user.should_see("## Sources")
    ...  # edit, Save → file on disk updated; Export .md → exports/<name>.md exists
async def test_save_conflict_dialog(user, state, ready_slug): ...
async def test_claude_error_shown(user, state, ready_slug, failing_backend): ...
async def test_new_doctype_appears_in_generate_dialog(user, state, ready_slug): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ui_documents.py -v`
Expected: FAIL

- [ ] **Step 3: Implement both modules**

- [ ] **Step 4: Run the whole suite, then check by hand**

Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest -v` → all PASS. Then `./run.sh`, generate a Summary for the project from Task 13 in subscription mode, edit a line, Save, and Export .pdf: the PDF opens in Preview with a Sources section at the end. Edit the Summary type's instructions, Regenerate, and check that the change is reflected. Switch Settings to API key mode with a real key and generate once.

- [ ] **Step 5: Commit**

```bash
git add ytt/ui tests/test_ui_documents.py
git commit -m "feat: document generation, editor, export, and document-types page"
```

---

### Task 15: README and end-to-end check

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

Sections: what it does (one paragraph, including "audio and transcription stay on your Mac; transcript text is sent to Claude only when you click Generate"), prerequisites (`brew install ffmpeg pango uv`; Claude Code logged in **or** an API key), start (`./run.sh` or double-click `YouTube Transcriber.command`), where files live (the data-root tree from spec §6), updating yt-dlp (`./run.sh --update-ytdlp`), troubleshooting (YouTube errors → update yt-dlp; PDF errors → pango; Claude errors → `claude` login or API key; "already running" → the browser opens the existing instance), and running tests (`uv run pytest`, `uv run pytest -m live`).

- [ ] **Step 2: Fresh-clone check**

Run: `git clone . "$TMPDIR/ytt-check" && cd "$TMPDIR/ytt-check" && ./run.sh`
Expected: dependencies install and the app opens in the browser using only the README steps.

- [ ] **Step 3: Live tests**

Run: `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest -m live -v`
Expected: PASS (needs network, GPU, and Claude login).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README with setup, usage, data layout, troubleshooting"
```
