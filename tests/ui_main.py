"""Test entry for NiceGUI's `user` fixture: AppState on tmp dirs with a fake queue."""
from nicegui import ui

from tests import ui_support
from tests.fakes import FakeQueue
from ytt.app_state import AppState
from ytt.config import Paths, load_data_root
from ytt.ui.shell import register_pages

state = AppState(Paths(load_data_root()), queue_factory=lambda library, cache: FakeQueue())
state.paths.ensure_layout()
ui_support.STATE = state
register_pages(state)


ui.run(storage_secret="test")
