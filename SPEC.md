# YouTube Transcriber — Spec

A local, single-user app for an M1 Max MacBook. It downloads audio from YouTube videos and transcribes it on the Apple GPU with Whisper (via MLX), saving one plain-text transcript per video. It has a simple browser UI on localhost and is started with one command or a double-click.

---

## 1. Decisions (locked)

| Topic | Decision |
|---|---|
| Transcription source | **Always Whisper**, never YouTube captions. Same quality for every video. |
| Output format | **Plain text `.txt`** only, one file per video. |
| Models | UI dropdown with the top 3 large models. Default **large-v3-turbo**; also **large-v3** and **large-v2**. |
| Language | **English by default.** A dropdown can override it (other languages + "Auto-detect"). |
| Channel input | **Regular videos only** (the channel's Videos tab), filtered by date range. No Shorts, no streams. |
| Playlist input | **Everything in the playlist**, including Shorts and past livestreams. |
| Storage | `output/<channel>/<YYYY-MM-DD>_<title> [<video_id>].txt` |
| Re-runs | **Skip videos already transcribed** (by video ID). Re-running a channel picks up only new uploads. |
| Audio | **Deleted** after a successful transcription. |
| Launch | **Both** `./run.sh` in Terminal and a double-clickable `YouTube Transcriber.command`. |
| Users | One person, local only. No accounts, no server, no cloud. |

---

## 2. Features

### 2.1 Inputs (four tabs in the UI)

1. **URL file**: upload a `.txt` file (one URL per line; blank lines and `#` comments are ignored). Duplicates are removed.
2. **Paste URLs**: a textbox. URLs can be separated by newlines, spaces or commas.
3. **Channel + date range**
   - Channel URL in any form (`/@handle`, `/channel/UC…`, `/c/name`); the app normalizes it to the channel's `/videos` tab.
   - Date mode: **"From date → today"** or **"Between two dates"** (both ends inclusive).
   - Before transcribing, it shows a **preview list** (title, date, duration) with a total count and total duration, so you can confirm.
4. **Playlist URL**: the app expands the playlist into all its entries (videos, Shorts, past streams), shows the same preview list, then queues them.

A plain video URL pasted into tab 1 or 2 also works if it's a `youtu.be` link or a Shorts link. If a URL pasted in tab 2 turns out to be a playlist, the app expands it the same way as tab 4.

### 2.2 Run settings (sidebar)

- **Model**: `large-v3-turbo` (default) / `large-v3` / `large-v2`
- **Language**: `English` (default) / `Auto-detect` / a short list of common languages
- **Output folder**: defaults to `./output`

### 2.3 Processing

- Videos are processed **one at a time** (one GPU job at a time; parallel Whisper on one GPU gives no speedup).
- For each video: fetch metadata → skip if already done → download the best audio-only stream → transcribe → write `.txt` → delete the audio → record it in the index.
- **Live progress**: overall bar (n of N), the current video's title and stage (downloading / transcribing), elapsed time, and a running log.
- **Stop** button: finishes or aborts the current video cleanly, then stops the queue. Nothing half-written stays in `output/`, because files are written to a temp name and then renamed.
- **One video failing never stops the batch.** Private, removed, members-only, age-restricted or region-blocked videos are marked *failed* with the reason, and the queue moves on.
- At the end, a summary: done / skipped / failed. Failed items have a **"Retry failed"** button.

### 2.4 Output

- Plain text: Whisper segments joined into paragraphs, with no timestamps and no header.
- Filename: `<YYYY-MM-DD>_<sanitized title> [<video_id>].txt`. The ID makes the name unique and lets you find the source video.
- Folder: `output/<sanitized channel name>/`. Playlist items go into their own uploader's channel folder.
- **Index** `output/index.json`: video_id → {title, channel, upload date, url, model, language, output path, transcribed_at, status, error}. This drives skip-done and retries, and keeps the metadata out of the `.txt` files.
- A UI toggle **"Re-transcribe even if done"** (off by default) for when you want to redo a video with a different model.

---

## 3. Tech choices

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Every component below is Python-native. |
| Env / deps | **uv** (`pyproject.toml` + `uv.lock`) | One command sets up Python and dependencies. Fast and reproducible. Replaces the existing `venv/`. |
| Transcription | **mlx-whisper** | Runs Whisper on the Apple GPU through MLX. The fastest Whisper option on Apple Silicon. |
| Model weights | `mlx-community/whisper-large-v3-turbo`, `mlx-community/whisper-large-v3-mlx`, `mlx-community/whisper-large-v2-mlx` | Downloaded from Hugging Face on first use, then cached in `~/.cache/huggingface`. |
| Download / listing | **yt-dlp** (Python API, not shell calls) | Handles channels, playlists and Shorts, filters by date, and downloads audio. Actively maintained against YouTube changes. |
| Audio decoding | **ffmpeg** (Homebrew) | Needed by both yt-dlp and mlx-whisper. |
| UI | **Streamlit** | A browser UI in pure Python with no frontend build. Has file upload, tabs, progress bars and date pickers built in. |
| Storage | Plain files + `index.json` | No database needed for one user. |

**Prerequisites on the Mac:** Homebrew, `brew install ffmpeg uv`. Everything else is installed by `uv`.

### Key implementation notes (no code yet, just the approach)

- **Channel date filtering:** list the `/videos` tab newest-first and fetch each entry's upload date. Stop listing as soon as a video is older than the start date, so you don't crawl a channel's whole history. Skip entries newer than the end date.
- **Background worker:** Streamlit reruns the whole script on every interaction. The queue runs in a **background thread**, and the UI polls its status about once a second. This keeps the Stop button and the page responsive during long transcriptions.
- **Model loading:** load the selected model once per run, not once per video.
- **Audio format:** download audio-only (m4a/webm) and let mlx-whisper decode it with ffmpeg. No separate conversion step.
- **Temp files:** audio goes to `./.cache/audio/`, which is cleared on start-up and after each video.
- **yt-dlp freshness:** YouTube breaks yt-dlp periodically. `run.sh` offers a quick `uv lock --upgrade-package yt-dlp`, and the README documents it.

---

## 4. Project layout

```
youtube-transcriber/
├── SPEC.md
├── README.md                     # setup + usage + troubleshooting
├── pyproject.toml                # deps: mlx-whisper, yt-dlp, streamlit
├── uv.lock
├── run.sh                        # terminal launcher
├── YouTube Transcriber.command   # double-click launcher (calls run.sh)
├── app.py                        # Streamlit UI only
├── transcriber/
│   ├── __init__.py
│   ├── inputs.py                 # parse URL file / textbox, classify URL type
│   ├── sources.py                # yt-dlp: expand channel/playlist, fetch metadata, date filter
│   ├── download.py               # yt-dlp: audio download
│   ├── transcribe.py             # mlx-whisper wrapper, model registry
│   ├── output.py                 # filename sanitizing, paragraph text, atomic write
│   ├── index.py                  # index.json read/write, skip-done logic
│   └── worker.py                 # background queue, progress state, stop/retry
├── tests/
└── output/                       # transcripts (gitignored)
```

The UI (`app.py`) only reads inputs and displays state. All the logic lives in `transcriber/` and can be tested without Streamlit.

---

## 5. Build steps (in order)

Each step ends with something you can check before moving on.

1. **Environment setup.** Install `ffmpeg` and `uv`. Create `pyproject.toml` with the three dependencies and run `uv sync`. Remove the old `venv/`.
   *Check:* `uv run python -c "import mlx_whisper, yt_dlp, streamlit"` succeeds.

2. **Transcription spike.** `transcribe.py`: transcribe one local audio file with each of the three models.
   *Check:* text comes out, the GPU is busy in Activity Monitor, and you've noted the speed of each model on a 10-minute clip.

3. **Single-video pipeline.** `download.py` + `output.py`: URL → audio → transcript → `.txt` in the right folder with the right name → audio deleted.
   *Check:* one regular video, one Shorts URL and one `youtu.be` link all produce correct files.

4. **Index and skip-done.** `index.py`: record results; skip IDs already done; "re-transcribe" override.
   *Check:* running the same URL twice skips it the second time.

5. **Input parsing.** `inputs.py`: URL file and textbox parsing, de-duplication, URL classification (video / playlist / channel / invalid).
   *Check:* unit tests on messy input (blank lines, comments, commas, junk).

6. **Playlist expansion.** `sources.py`: expand a playlist into video entries with metadata.
   *Check:* a playlist containing a Short and a past stream lists all of them.

7. **Channel + date range.** `sources.py`: normalize channel URLs to `/videos`, list newest-first, stop early at the start date, apply the end date.
   *Check:* both date modes return the expected videos for a known channel; no Shorts or streams appear; it stops early on a large channel.

8. **Background worker.** `worker.py`: sequential queue in a thread, per-video stages, progress state, stop flag, error capture, retry-failed.
   *Check:* a batch where one URL is private finishes with 1 failed and the rest done; Stop leaves no partial files.

9. **Streamlit UI.** `app.py`: four input tabs, sidebar settings, preview list with count and duration, Start/Stop, live progress, log, summary, Retry failed.
   *Check:* you can do every input type end to end from the browser.

10. **Launchers.** `run.sh` (runs `uv sync` if needed, then `streamlit run app.py`, which opens the browser) and `YouTube Transcriber.command` (`cd`s to the project and calls `run.sh`; `chmod +x` both).
    *Check:* double-clicking in Finder opens the app in the browser.

11. **Hardening and README.** Clear messages when ffmpeg is missing, the network is down, or yt-dlp is outdated; the first model download shows progress; README covers setup, usage, updating yt-dlp, and where the files go.
    *Check:* a fresh clone works by following only the README.

---

## 6. Out of scope (v1)

- YouTube captions, subtitles (`.srt`/`.vtt`), JSON or Markdown output
- Speaker diarization, summaries, or translation
- Channel Shorts/livestreams tabs (playlists can still include them)
- Members-only or age-restricted videos (a later option: yt-dlp `cookies-from-browser`)
- Parallel transcription, scheduling, or auto-watching channels
- Packaging as a signed `.app`

## 7. Risks

| Risk | Mitigation |
|---|---|
| YouTube changes break yt-dlp | Keep yt-dlp up to date; one-command upgrade in `run.sh` and the README. |
| Rate limiting or throttling on large channel runs | Sequential processing; optional small delay between downloads; per-video errors don't stop the batch. |
| Very long livestreams (hours) | Sequential processing, so memory use stays bounded. The preview shows total duration before you start. |
| First model download (~1.5–3 GB per model) | Download on first use with a visible status; it's cached afterwards. |
