# YouTube Transcriber: Design

*Date: 2026-10-22. Supersedes the earlier `SPEC.md` (deleted from the working tree). Where this document is silent, the earlier SPEC's decisions still hold. They are restated below where they matter.*

## 1. What this is and why

A local, single-user app on an M1 Max MacBook. You gather YouTube videos into **projects**. The app downloads each video's audio and transcribes it **on the Apple GPU with Whisper**. Then **Claude** writes **one combined document** for the project, such as a summary or a step-by-step guide. You can edit the document in the app and export it as **Markdown and/or PDF**.

The transcripts are raw material. The payoff is the documents. The owner wants to turn many videos, whole playlists, or a channel's recent uploads into readable, editable guides and summaries without copy-pasting transcripts into a chatbot.

It runs in the browser on `localhost` and starts with one command or a double-click. There are no accounts. Audio and transcription never leave the Mac. **The only thing that goes to the cloud is transcript text sent to Claude, and only when the user clicks Generate.**

## 2. Goals

- Projects: named collections of sources (videos, playlists, channels with a date range).
- Transcribe locally with Whisper on the GPU, **once per video**, into a **shared transcript library** reused by every project.
- Generate one combined document per project from a **document type**, then edit, save, and export it as `.md` and/or `.pdf`.
- Document types are **user-editable templates**, changeable while the app runs. The app ships with **Summary** and **Step-by-step guide**, and the user can add more.
- Claude access by **either** the user's Claude subscription (through the installed Claude Code) **or** an Anthropic API key, chosen in Settings.
- Plain files on disk, **saved safely** (see §7).

## 3. Non-goals

- No YouTube captions/subtitles as a source: always Whisper (from the earlier SPEC).
- No timestamps in transcripts or documents.
- No links between documents.
- No accounts, multi-user, sync, or remote access.
- No database (owner's decision: plain files).
- No signed `.app` packaging, scheduling, or auto-watching channels.
- No members-only / age-restricted video support in v1.

## 4. Anti-goals (would count as failure even if it "works")

- Audio or transcription leaving the Mac, or transcript text being sent to Claude without the user clicking Generate.
- Losing or corrupting a user's edited document or template (crash, concurrent write, or silent overwrite of an outside edit).
- Re-transcribing a video that's already in the library just because a second project uses it.
- One bad video (private, removed, blocked) stopping the batch.

## 5. Stack (owner's decisions)

| Layer | Choice |
|---|---|
| Language | Python 3.11+, managed by **uv** (`pyproject.toml` + `uv.lock`). The system Python is 3.9, so uv must provision 3.11+. |
| UI | **NiceGUI** (browser UI written in Python, live updates over websocket) |
| Web server | **FastAPI** (NiceGUI runs on it, and extra endpoints like file downloads live there) |
| Transcription | **mlx-whisper** on the Apple GPU |
| Download/listing | **yt-dlp** Python API |
| Audio decoding | **ffmpeg** (already installed via Homebrew) |
| Claude via subscription | Claude Agent SDK for Python, using the already-installed and logged-in `claude` CLI |
| Claude via API key | `anthropic` Python SDK |
| PDF | Builder's choice. WeasyPrint (needs `brew install pango`) is the suggested route. |
| Storage | Plain files (§6), no database |

Already present on the Mac: Homebrew, uv, ffmpeg, yt-dlp (Homebrew copy; the app should use its own pinned Python package), `claude` CLI.

## 6. Data on disk

A data root, default `~/YouTube Transcriber/`, that can be changed in Settings:

```
~/YouTube Transcriber/
  settings.json
  transcripts/                       ← shared library, one .txt per video
    index.json                       ← video_id → metadata (see below)
    <channel>/<YYYY-MM-DD>_<title> [<video_id>].txt
  document-types/
    summary.md
    step-by-step-guide.md
  projects/
    <project name>/
      project.json                   ← name, sources, per-source settings, resolved video list
      documents/<document name>.md   ← the editable generated documents
      exports/<document name>.pdf / .md
      .history/                      ← rolling previous versions of documents
```

- **Transcript files:** plain text, Whisper segments joined into paragraphs, with no header and no timestamps (from the earlier SPEC). The name includes the video ID so it's both readable and unique. *(My call: keep the earlier SPEC's readable naming instead of bare `<videoId>.txt`.)*
- **`transcripts/index.json`:** per video: title, channel, upload date, URL, duration, Whisper model, **detected language**, **YouTube-reported language**, **language-mismatch flag**, path, transcribed_at, status, error. This drives reuse, skipping finished videos, retries, and language display.
- **One transcript per video.** If the user corrects a video's language, it's re-transcribed and the transcript **replaces** the old one, so every project using that video sees the corrected text. *(My call.)*
- **The existing transcript always wins.** Changing the Whisper model in Settings, or a project's forced language, applies only to videos **not yet in the library**. A video already in the library is reused as is. To redo one, the user clicks **Re-transcribe** on that video and picks a model and language. The new transcript replaces the old one for every project, and the UI says so before it starts. The earlier SPEC's "Re-transcribe even if done" toggle is replaced by this per-video action. *(My call.)*
- **Where the data root is recorded:** a small pointer file outside the data root, `~/Library/Application Support/YouTube Transcriber/config.json`, holds the data-root path plus the running instance's port and lock. Changing the data root in Settings **does not move anything**: the app switches to the new folder (adopting its contents if it already holds a library, or starting empty), and the old folder stays untouched for the user to move or delete in Finder. *(My call.)*
- **Audio** is deleted after a successful transcription, and the temp audio folder is cleared on startup (from the earlier SPEC).

## 7. Safe file handling (owner asked for "plain files, but safe")

- **Atomic writes:** write to a temp file in the same folder, flush/fsync, then `os.replace` it onto the real file. A crash leaves either the old version or the new one, never a half-written file.
- **One writer:** every write goes through one storage module that holds a lock per file, so the worker and the UI can't interleave writes.
- **Single instance:** a lock file (next to the pointer file, see §6) stops a second app copy from using the same folder. If the lock is stale (its process is dead), it's taken over.
- **Transcripts finish before they count:** Whisper output goes to `…txt.part` and is renamed only when complete. Leftover `.part` files are deleted on startup and those videos are queued again.
- **Outside-edit protection:** before saving a document, template, or `project.json`, the app checks whether the file's modification time or hash changed since it was loaded. If it did, the app asks the user (overwrite / reload / cancel) and never overwrites silently.
- **History:** each save of a document keeps the previous version in `.history/` (rolling, last N, *N = 20 my call*). Regenerating a document counts as a save, so the old version can be recovered.
- **Recovery on startup:** the app rebuilds the job queue by comparing every project's video list with `index.json` and the transcript files.

## 8. UI

Layout approved by the owner from a mockup: a **left sidebar** and a **main area**.

**Sidebar:** project list (name + video/doc counts), **+ New project**, then **Document types** and **Settings** links.

**Project page (main area):**
1. **Header:** project name, rename/delete.
2. **Sources:** add a source by pasting URLs (newlines/spaces/commas; a video URL, `youtu.be`, Shorts, playlist, or channel). *(Carried from the earlier SPEC: also accept an uploaded `.txt` file of URLs, one per line, with `#` comments allowed.)*
   - **Channel:** any URL form, normalized to the channel's Videos tab, **regular videos only** (no Shorts or streams), date mode "From date → today" or "Between two dates", inclusive (earlier SPEC).
   - **Playlist:** everything in it, including Shorts and past streams (earlier SPEC).
   - Before queueing a channel or playlist: a **preview list** (title, date, duration) with total count and duration, and a confirm step (earlier SPEC).
   - Each source expands to show its videos with **status** (queued / downloading / transcribing / done / failed + reason / reused from library) and **language badge** (e.g. "🇬🇧 en"), plus a ⚠ when the detected and YouTube-reported languages disagree.
   - A video's language can be changed by the user, which re-transcribes it. A source or the whole project can **force a language**, which skips detection.
   - **Retry failed** re-queues this project's failed videos.
   - *(My call)* The queue is **one global first-in-first-out queue** shared by all projects. Its status (current video, n of N, **Stop**) appears in a small bar visible on every page. **Stop** pauses the whole queue after cleanly finishing or aborting the current video, and **Resume** continues it. A project page can also **cancel** its own still-queued videos.
   - *(My call)* Removing a source from a project doesn't delete transcripts from the library.
   - *(My call)* A channel source stores its date range. A **Refresh** action re-lists the channel/playlist and queues only new videos.
3. **Documents:** a list of this project's documents (name, type, generated/edited date), **+ Generate new…** (choose a document type), and an **editor** for the selected document. Claude's output lands in the editor, where the user edits and saves. **Export .md** and **Export .pdf** buttons write to `exports/` and also offer a browser download.
   - Every generated document ends with a **Sources** section listing each source video's title and YouTube URL. No timestamps.
   - *(My call)* Generate is disabled until every video in the project is transcribed or has failed. If some failed, the user is told which ones will be left out.
   - *(My call)* Regenerating an existing document asks before replacing it (the old version goes to `.history/`).

**Document types page:** list of types, plus edit, duplicate, rename, delete, and **+ New type**. Edits apply on the next generation with no restart.

**Settings page:** data folder; Whisper model (`large-v3-turbo` default, `large-v3`, `large-v2`; from the earlier SPEC); default language mode (auto-detect); Claude access mode (**subscription via Claude Code** default, or **API key**) plus the API key field; Claude model *(my call: default to the latest Sonnet, with Opus selectable)*.

## 9. Transcription pipeline

- One shared background worker, **one video at a time** (one GPU). The Whisper model is loaded once and reused.
- Per video: fetch metadata → **if already in the library, mark it "reused" and stop** → download best audio-only stream → detect language (unless forced) → transcribe → write `.part` → rename → update `index.json` → delete audio.
- **Language:** auto-detect per video by default (Whisper reads the first ~30 s). Also read the language YouTube reports for the video. If both exist and disagree, set the mismatch flag (the transcript still uses Whisper's detection) and show ⚠ in the UI.
- If several projects queue the same video, it's transcribed once.
- Failures (private, removed, members-only, age/region-blocked, network) mark that video failed with a reason, and the queue moves on.
- Live progress is pushed to the UI: current video, stage, n of N, and a running log.
- Channel listing stops early once it's past the start date instead of crawling the whole history (earlier SPEC).

## 10. Document types and generation

- **Template file:** one per type in `document-types/`, holding a name, a short description, the **structure** (headings and sections), and **instructions to Claude** (tone, depth, what to include or leave out). The exact file format is the builder's call, but it must be easy to read and hand-edit as text, and editable inside the app.
- Ships with **Summary** and **Step-by-step guide**. The builder writes good default instructions for both.
- **Generation:** builds a prompt from the template + every transcript in the project (with titles) and asks Claude for **one combined Markdown document**. The app (not Claude) appends the **Sources** section, so the links are always correct.
- **Claude access:** both modes sit behind one small interface (`generate(prompt) → markdown`), so the rest of the app doesn't care which is used. If the subscription mode fails because the CLI is missing or logged out, the app shows a clear message pointing to Settings.
- **Large projects:** if the combined transcripts are too large for one Claude request, the builder must handle it so generation still succeeds. The owner chose not to prescribe the method.
- Export: `.md` writes the document as is. `.pdf` renders the Markdown with a clean, readable print style.

## 11. Launch

- `./run.sh` in Terminal and a double-clickable `YouTube Transcriber.command` (earlier SPEC). Both run `uv sync` if needed, start the server on localhost, and open the browser.
- If the app is already running (lock held), the launcher just opens the browser to the running instance.
- Clear startup messages if ffmpeg/pango is missing or yt-dlp is out of date, plus a one-command yt-dlp upgrade.

## 12. Left to the builder

Internal module layout, template file format, PDF library and styling, how large projects are split for Claude, the exact NiceGUI components, polling vs. push details, how many history versions to keep, and the test strategy. Keep the logic testable without the UI, as the earlier SPEC required.

## 13. Risks

| Risk | Mitigation |
|---|---|
| YouTube changes break yt-dlp | Pin yt-dlp in uv and provide a one-command upgrade (README + launcher hint). |
| Throttling on big channel runs | Sequential downloads; failures are per video; Retry failed. |
| Subscription usage limits during big generations | Show Claude's error clearly; the user can switch to an API key in Settings. |
| First model download (1.5–3 GB) | Visible status on first use; cached afterwards. |
| Outside edits racing with app saves | Modification-time/hash check before every save (§7). |
