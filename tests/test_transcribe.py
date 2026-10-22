import shutil
import subprocess
from pathlib import Path

import pytest

from ytt.transcribe import Transcriber, TranscriptResult, segments_to_paragraphs


def seg(s, e, t):
    return {"start": s, "end": e, "text": t}


def test_paragraph_on_gap():
    assert segments_to_paragraphs(
        [seg(0, 2, " Hi."), seg(2, 4, " There."), seg(7, 9, " Next.")]
    ) == "Hi. There.\n\nNext."


def test_paragraph_on_length_at_sentence_end():
    long = "x" * 600 + "."
    assert segments_to_paragraphs(
        [seg(0, 1, long), seg(1, 2, "Then"), seg(2, 3, "more.")]
    ) == long + "\n\nThen more."


def test_transcriber_passes_repo_and_language(monkeypatch):
    calls = {}

    def fake(path, **kw):
        calls.update(kw)
        return {"language": "de", "segments": [seg(0, 1, "Hallo.")]}

    monkeypatch.setattr("mlx_whisper.transcribe", fake)
    r = Transcriber("large-v3").transcribe(Path("a.m4a"), None)
    assert r == TranscriptResult("Hallo.", "de")
    assert calls["path_or_hf_repo"] == "mlx-community/whisper-large-v3-mlx"
    assert calls["language"] is None


@pytest.mark.live
def test_live_transcribe_speech(tmp_path):
    if not (shutil.which("say") and shutil.which("ffmpeg")):
        pytest.skip("say/ffmpeg unavailable")
    aiff, wav = tmp_path / "a.aiff", tmp_path / "a.wav"
    subprocess.run(["say", "-o", str(aiff), "Hello world, this is a test."], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(aiff), str(wav)], check=True, capture_output=True)
    r = Transcriber("large-v3-turbo").transcribe(wav, None)
    assert r.language == "en"
    assert "hello" in r.text.lower()
