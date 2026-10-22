"""Build prompts and generate one combined document from transcripts."""
from __future__ import annotations

from ytt.claude_client import Backend, ClaudeError
from ytt.doctypes import DocType
from ytt.urls import video_url
from ytt.youtube import VideoInfo

CHAR_BUDGET = 400_000  # roughly 100k tokens of transcript per request

NOTES_INSTRUCTION = (
    "Write detailed notes from this transcript that are relevant to the document type below."
)


def _escape(title: str) -> str:
    return title.replace("[", "\\[").replace("]", "\\]")


def sources_section(infos: list[VideoInfo]) -> str:
    return "\n\n---\n\n## Sources\n\n" + "".join(
        f"- [{_escape(i.title)}]({i.url or video_url(i.id)})\n" for i in infos
    )


def build_prompt(dt: DocType, items: list[tuple[VideoInfo, str]]) -> str:
    parts = [dt.body.strip()]
    for n, (info, text) in enumerate(items, 1):
        parts.append(f"### Video {n}: {info.title}\n\n{text}")
    return "\n\n".join(parts)


def generate_document(
    backend: Backend, model: str, dt: DocType, items: list[tuple[VideoInfo, str]]
) -> str:
    try:
        if sum(len(t) for _, t in items) > CHAR_BUDGET:
            items = [
                (info, backend.generate(f"{NOTES_INSTRUCTION}\n\n{build_prompt(dt, [(info, text)])}", model))
                for info, text in items
            ]
        body = backend.generate(build_prompt(dt, items), model).rstrip()
    except ClaudeError:
        raise
    except Exception as e:
        raise ClaudeError(f"Document generation failed: {e}") from e
    return body + sources_section([info for info, _ in items])
