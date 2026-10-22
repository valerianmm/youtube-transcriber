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


NO_SOURCES_LINE = "Do not include a Sources or References section; it is added automatically."
TOO_LARGE = "This project is too large to combine into one document. Try splitting it into smaller projects."


def build_prompt(dt: DocType, items: list[tuple[VideoInfo, str]]) -> str:
    header = f"Document type: {dt.name}" + (f" — {dt.description}" if dt.description else "")
    parts = [header, dt.body.strip(), NO_SOURCES_LINE]
    for n, (info, text) in enumerate(items, 1):
        parts.append(f"### Video {n}: {info.title}\n\n{text}")
    return "\n\n".join(parts)


def _chunks(text: str, limit: int) -> list[str]:
    """Split text into pieces of at most `limit` chars, preferring paragraph boundaries."""
    out: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        while len(para) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + 2 + len(para) > limit:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def _notes(backend: Backend, model: str, dt: DocType, info: VideoInfo, text: str) -> str:
    notes = [
        backend.generate(f"{NOTES_INSTRUCTION}\n\n{build_prompt(dt, [(info, c)])}", model)
        for c in _chunks(text, CHAR_BUDGET)
    ]
    return "\n\n".join(notes)


def generate_document(
    backend: Backend, model: str, dt: DocType, items: list[tuple[VideoInfo, str]]
) -> str:
    if not items:
        raise ClaudeError("No transcripts to work from yet.")
    try:
        if sum(len(t) for _, t in items) > CHAR_BUDGET:
            items = [(info, _notes(backend, model, dt, info, text)) for info, text in items]
            if sum(len(t) for _, t in items) > CHAR_BUDGET:
                raise ClaudeError(TOO_LARGE)
        body = backend.generate(build_prompt(dt, items), model).rstrip()
    except ClaudeError:
        raise
    except Exception as e:
        raise ClaudeError(f"Document generation failed: {e}") from e
    return body + sources_section([info for info, _ in items])
