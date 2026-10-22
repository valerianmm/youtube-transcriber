# YouTube Transcriber

A local app for turning YouTube videos and playlists into transcripts and then into documents. You add videos to a project, the app downloads the audio and transcribes it with Whisper (mlx-whisper, on the Apple GPU), and you generate editable documents (summaries, step-by-step guides, your own types) with Claude, then export them as PDF or Markdown.

Audio and transcription stay on your Mac. Transcript text is sent to Claude only when you click Generate.

## Prerequisites

- An Apple Silicon Mac with [Homebrew](https://brew.sh).
- `brew install ffmpeg pango uv`
- Claude access, either:
  - Claude Code installed and logged in (the default, "subscription" mode), or
  - an Anthropic API key, entered in Settings.

## Start

```
./run.sh
```

or double-click `YouTube Transcriber.command` in Finder. The first run installs the Python dependencies (this takes a while); after that it starts quickly. The app serves on http://127.0.0.1:8765 and opens in your browser. The launcher checks that ffmpeg and pango are installed and tells you what to `brew install` if not.

Only one instance runs at a time. If it is already running, starting it again just opens the existing instance in your browser.

The first transcription downloads the selected Whisper model (about 1.5 GB for the default `large-v3-turbo`) into `~/.cache/huggingface`.

## Where files live

The data root defaults to `~/YouTube Transcriber/`. Change it in Settings; the new location is remembered in `~/Library/Application Support/YouTube Transcriber/config.json`. Changing it needs an app restart, and it does not move anything: the app switches to the new folder (using its contents if it already holds a library) and the old folder is left untouched for you to move or delete.

```
~/YouTube Transcriber/
  settings.json                      Whisper model, language, Claude mode/model, API key (mode 600)
  transcripts/                       shared library, one .txt per video
    index.json                       video metadata, language, status, errors
    <channel>/<YYYY-MM-DD>_<title> [<video_id>].txt
  doc_types/                         one .md per document type (summary, step-by-step guide, yours)
    .trash/                          deleted document types
  projects/
    <project name>/
      project.json                   sources, per-source settings, resolved video list
      documents/<name>.md            the editable generated documents
      exports/<name>.pdf / .md
      .history/                      previous versions of documents
  .cache/audio/                      temporary audio, cleared on startup
```

Notes:

- There is one transcript per video, shared by all projects. An existing transcript is always reused; to redo one, use Re-transcribe on that video.
- Deleting a project moves its folder to the macOS Trash (transcripts stay in the library). Deleting a document type moves its file to `doc_types/.trash/`.
- Everything is plain files, so you can read them in Finder or any editor.

## Updating yt-dlp

YouTube changes often and yt-dlp needs to keep up:

```
./run.sh --update-ytdlp
```

This upgrades yt-dlp in the lock file, syncs, and starts the app.

## Troubleshooting

- **YouTube errors (download fails, "Sign in", unavailable formats):** run `./run.sh --update-ytdlp`.
- **PDF export errors:** pango is missing or not found. Run `brew install pango`. `run.sh` sets `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` for you.
- **Claude errors:** in subscription mode, make sure the `claude` CLI is installed and logged in (run `claude` once in a terminal). Otherwise switch to API key mode in Settings and enter a key.
- **"Already running":** the app opens the existing instance in your browser instead of starting a second one. If nothing is actually running, check for a stale process on port 8765.
- **ffmpeg missing:** `brew install ffmpeg`.

## Tests

```
uv run pytest            # fast tests, no network or GPU
uv run pytest -m live    # network, GPU, and Claude; downloads the Whisper model on first run
```

PDF tests need the pango libraries on the loader path. `run.sh` sets this for the app, but when running pytest yourself use:

```
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run pytest
```

The API-key live test is skipped unless `ANTHROPIC_API_KEY` is set.
