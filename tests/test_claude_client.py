import shutil

import pytest

from ytt.claude_client import ApiKeyBackend, ClaudeError, SubscriptionBackend, backend_for
from ytt.config import Settings


def test_api_backend_requires_key():
    with pytest.raises(ClaudeError, match="No API key"):
        ApiKeyBackend("").generate("p", "m")


def test_subscription_backend_missing_cli(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ClaudeError, match="isn't installed"):
        SubscriptionBackend().generate("p", "m")


def test_backend_for_picks_by_mode():
    assert isinstance(backend_for(Settings(claude_mode="api_key", api_key="k")), ApiKeyBackend)
    assert isinstance(backend_for(Settings(claude_mode="subscription")), SubscriptionBackend)


@pytest.mark.live
def test_live_subscription():
    if not shutil.which("claude"):
        pytest.skip("claude CLI not on PATH")
    assert SubscriptionBackend().generate("Reply with the single word: pong", "claude-sonnet-5-5").strip()


@pytest.mark.live
def test_live_api_key():
    import os
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    assert ApiKeyBackend(key).generate("Reply with the single word: pong", "claude-sonnet-5-5").strip()
