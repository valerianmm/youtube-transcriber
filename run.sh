#!/usr/bin/env bash
set -euo pipefail
src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ "$src" != /* ]] && src="$dir/$src"
done
cd -P "$(dirname "$src")"

command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is missing. Install it with: brew install ffmpeg" >&2; exit 1; }
brew list pango >/dev/null 2>&1 || { echo "pango is missing. Install it with: brew install pango" >&2; exit 1; }
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib

if [ "${1:-}" = "--update-ytdlp" ]; then
  uv lock --upgrade-package yt-dlp
  uv sync
fi

uv sync --quiet
exec uv run python -m ytt
