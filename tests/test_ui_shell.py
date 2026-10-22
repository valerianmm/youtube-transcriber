import pytest

from tests import ui_support
from ytt.storage import read_json
from ytt.config import app_support_dir, load_settings


@pytest.fixture
def state(user):
    return ui_support.STATE


async def test_create_project_from_sidebar(user):
    await user.open("/")
    await user.should_see("Create your first project")
    user.find("+ New project").click()
    user.find(marker="new-project-name").type("Kubernetes")
    user.find("Create").click()
    await user.should_see("Kubernetes")
    await user.open("/settings")
    await user.should_see("0 videos · 0 docs")


async def test_duplicate_project_name_shows_error(user, state):
    state.projects.create("Kubernetes")
    await user.open("/settings")
    user.find("+ New project").click()
    user.find(marker="new-project-name").type("Kubernetes")
    user.find("Create").click()
    assert [p.name for p in state.projects.list()] == ["Kubernetes"]
    (err,) = user.find(marker="new-project-error").elements
    assert err.visible and err.text


async def test_blank_project_name_shows_error(user, state):
    await user.open("/settings")
    user.find("+ New project").click()
    user.find("Create").click()
    assert state.projects.list() == []
    (err,) = user.find(marker="new-project-error").elements
    assert err.visible and err.text


async def test_settings_save_persists(user, state):
    await user.open("/settings")
    await user.should_not_see(marker="api-key")
    user.find(marker="claude-mode").elements.pop().set_value("api_key")
    await user.should_see(marker="api-key")
    user.find(marker="api-key").type("sk-test")
    user.find("Save").click()
    await user.should_see("Settings saved")
    saved = load_settings(state.paths)
    assert saved.claude_mode == "api_key"
    assert saved.api_key == "sk-test"


async def test_change_data_folder_shows_restart_notice(user, state, tmp_path):
    await user.open("/settings")
    await user.should_not_see("Restart the app")
    user.find(marker="data-folder").elements.pop().set_value(str(tmp_path / "elsewhere"))
    user.find("Change…").click()
    await user.should_see("Restart the app to use the new data folder.")
    data, _ = read_json(app_support_dir() / "config.json")
    assert data["data_root"] == str(tmp_path / "elsewhere")


async def test_queue_bar_without_current_job_and_toggle(user, state):
    await user.open("/settings")
    await user.should_see("0/0")
    user.find("Stop").click()
    await user.should_see("Resume")
    assert state.queue.paused
    user.find("Resume").click()
    await user.should_see("Stop")


async def test_log_shows_recent_lines(user, state):
    state.queue.log = ["10:00:00 Downloading Whisper model large-v3-turbo (first use, ~1.5–3 GB)…"]
    await user.open("/settings")
    user.find("Log").click()
    await user.should_see("Downloading Whisper model large-v3-turbo")
    state.queue.log = [*state.queue.log, "10:00:05 Transcribing Talk"]  # picked up by the timer
    await user.should_see("Transcribing Talk")
