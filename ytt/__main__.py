"""Entry point: `python -m ytt`."""
from __future__ import annotations

import os
import sys
import webbrowser

from nicegui import ui

from ytt.app_state import AppState
from ytt.config import Paths, load_data_root
from ytt.instance import InstanceLock, running_port
from ytt.ui.shell import register_pages

PORT = 8765


def main() -> None:
    no_browser = bool(os.environ.get("YTT_NO_BROWSER"))
    lock = InstanceLock()
    if not lock.acquire(PORT):
        if not no_browser:
            webbrowser.open(f"http://localhost:{running_port() or PORT}")
        sys.exit(0)
    state = AppState(Paths(load_data_root()))
    state.startup()
    register_pages(state)
    ui.run(
        host="127.0.0.1",
        port=PORT,
        title="YouTube Transcriber",
        reload=False,
        show=not no_browser,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
