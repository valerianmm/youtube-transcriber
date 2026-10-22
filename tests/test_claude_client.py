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


def test_subscription_options_blank_api_credentials_and_disable_tools():
    from ytt.claude_client import subscription_options
    o = subscription_options("m")
    assert o.env == {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}
    assert o.tools == [] and o.allowed_tools == [] and o.max_turns == 1


def _stub_query(monkeypatch, messages):
    import claude_agent_sdk

    async def fake(prompt, options):
        for m in messages:
            yield m
    monkeypatch.setattr(claude_agent_sdk, "query", fake)
    monkeypatch.setattr(shutil, "which", lambda _: "/bin/claude")


def _result(**kw):
    from claude_agent_sdk import ResultMessage
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=1, session_id="s")
    return ResultMessage(**{**base, **kw})


def test_subscription_concatenates_text_blocks(monkeypatch):
    from claude_agent_sdk import AssistantMessage, TextBlock
    _stub_query(monkeypatch, [
        AssistantMessage(content=[TextBlock(text="Hello "), TextBlock(text="world")], model="m"),
        _result(),
    ])
    assert SubscriptionBackend().generate("p", "m") == "Hello world"


def test_subscription_error_result_raises(monkeypatch):
    _stub_query(monkeypatch, [_result(is_error=True, subtype="error", result="Not logged in")])
    with pytest.raises(ClaudeError, match="Not logged in"):
        SubscriptionBackend().generate("p", "m")
