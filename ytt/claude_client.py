"""Claude backends: the logged-in `claude` CLI (subscription) or the Anthropic API."""
from __future__ import annotations

import shutil
from typing import Protocol

import anyio

from ytt.config import Settings

SYSTEM = (
    "You write one combined Markdown document from YouTube transcripts, following the "
    "document type's structure and instructions. Output only the document."
)


class ClaudeError(Exception):
    """Message is shown to the user as is."""


class Backend(Protocol):
    def generate(self, prompt: str, model: str) -> str: ...


class SubscriptionBackend:
    def generate(self, prompt: str, model: str) -> str:
        if shutil.which("claude") is None:
            raise ClaudeError(
                "Claude Code isn't installed or isn't on PATH. "
                "Install it, or switch to an API key in Settings."
            )
        try:
            return anyio.run(self._run, prompt, model)
        except ClaudeError:
            raise
        except Exception as e:  # SDK/CLI/auth failures
            raise ClaudeError(
                "Claude Code couldn't run (are you logged in? run `claude` once in Terminal). "
                f"Details: {e}"
            ) from e

    async def _run(self, prompt: str, model: str) -> str:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=model, allowed_tools=[], max_turns=1, system_prompt=SYSTEM
        )
        parts: list[str] = []
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                parts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
            elif isinstance(msg, ResultMessage) and msg.is_error:
                detail = msg.result or "; ".join(msg.errors or []) or msg.subtype
                raise ClaudeError(
                    "Claude Code couldn't run (are you logged in? run `claude` once in "
                    f"Terminal). Details: {detail}"
                )
        text = "".join(parts).strip()
        if not text:
            raise ClaudeError("Claude returned an empty response.")
        return text


class ApiKeyBackend:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(self, prompt: str, model: str) -> str:
        if not self.api_key.strip():
            raise ClaudeError("No API key set. Add one in Settings.")
        import anthropic

        try:
            client = anthropic.Anthropic(api_key=self.api_key)
            with client.messages.stream(
                model=model,
                max_tokens=16000,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                text = stream.get_final_text()
        except anthropic.APIError as e:
            raise ClaudeError(getattr(e, "message", None) or str(e)) from e
        except Exception as e:
            raise ClaudeError(f"Claude API request failed: {e}") from e
        if not text.strip():
            raise ClaudeError("Claude returned an empty response.")
        return text


def backend_for(settings: Settings) -> Backend:
    if settings.claude_mode == "api_key":
        return ApiKeyBackend(settings.api_key)
    return SubscriptionBackend()
