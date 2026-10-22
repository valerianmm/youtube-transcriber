import pytest

from ytt import generate
from ytt.claude_client import ClaudeError
from ytt.doctypes import DocType
from ytt.generate import generate_document, sources_section
from ytt.youtube import VideoInfo

DT = DocType(slug="summary", name="Summary", description="d", body="## Template body")


def vi(i, title=None):
    return VideoInfo(id=i, title=title or f"T{i}", url=f"https://www.youtube.com/watch?v={i}")


class FakeBackend:
    def __init__(self, reply="# Doc\nBody"): self.prompts = []; self.reply = reply
    def generate(self, prompt, model): self.prompts.append(prompt); return self.reply


def test_single_call_includes_template_and_all_transcripts():
    b = FakeBackend()
    out = generate_document(b, "claude-sonnet-5-5", DT, [(vi("a", "Intro"), "alpha text"), (vi("b", "Helm"), "beta text")])
    assert len(b.prompts) == 1
    p = b.prompts[0]
    assert DT.body in p and "alpha text" in p and "beta text" in p and "Intro" in p
    assert out.startswith("# Doc\nBody")
    assert out.endswith("## Sources\n\n- [Intro](https://www.youtube.com/watch?v=a)\n- [Helm](https://www.youtube.com/watch?v=b)\n")


def test_over_budget_uses_per_video_notes_then_combines(monkeypatch):
    monkeypatch.setattr(generate, "CHAR_BUDGET", 100)
    b = FakeBackend()
    generate_document(b, "m", DT, [(vi("a"), "x" * 80), (vi("b"), "y" * 80)])
    assert len(b.prompts) == 3 and "x" * 80 not in b.prompts[2]


def test_sources_escape_brackets_in_titles():
    assert "- [A \\[live\\]](https://www.youtube.com/watch?v=a)" in sources_section([vi("a", "A [live]")])


def test_sources_fall_back_to_video_url_when_url_empty():
    info = VideoInfo(id="z", title="Z", url="")
    assert "(https://www.youtube.com/watch?v=z)" in sources_section([info])


def test_unexpected_backend_exception_becomes_claude_error():
    class Boom:
        def generate(self, prompt, model): raise RuntimeError("kaput")
    with pytest.raises(ClaudeError, match="kaput"):
        generate_document(Boom(), "m", DT, [(vi("a"), "t")])


def test_prompt_forbids_sources_section():
    b = FakeBackend()
    generate_document(b, "m", DT, [(vi("a"), "t")])
    assert "Do not include a Sources or References section; it is added automatically." in b.prompts[0]


def test_empty_items_raises_without_calling_claude():
    b = FakeBackend()
    with pytest.raises(ClaudeError, match="No transcripts"):
        generate_document(b, "m", DT, [])
    assert b.prompts == []


def test_single_oversize_transcript_is_chunked(monkeypatch):
    monkeypatch.setattr(generate, "CHAR_BUDGET", 2000)
    b = FakeBackend(reply="n")
    text = "\n\n".join("p" * 300 for _ in range(20))  # ~6000 chars
    generate_document(b, "m", DT, [(vi("a"), text)])
    assert len(b.prompts) > 2  # several note calls + final combine
    assert all(len(p) <= 2000 + 500 for p in b.prompts[:-1])


def test_notes_still_too_large_raises(monkeypatch):
    monkeypatch.setattr(generate, "CHAR_BUDGET", 2000)
    b = FakeBackend(reply="z" * 1500)
    with pytest.raises(ClaudeError, match="too large"):
        generate_document(b, "m", DT, [(vi("a"), "x" * 3000), (vi("b"), "y" * 3000)])
