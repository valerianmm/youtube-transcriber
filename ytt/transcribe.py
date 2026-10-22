"""mlx-whisper wrapper: transcription with paragraphing and language detection."""
from dataclasses import dataclass
from pathlib import Path

import mlx_whisper

from ytt.config import WHISPER_MODELS

PARAGRAPH_GAP_SECONDS = 2.0
PARAGRAPH_MIN_CHARS = 600


@dataclass
class TranscriptResult:
    text: str
    language: str


def segments_to_paragraphs(segments: list[dict]) -> str:
    paragraphs: list[str] = []
    current = ""
    prev_end: float | None = None
    for s in segments:
        text = s["text"].strip()
        if not text:
            continue
        if current:
            gap = s["start"] - prev_end > PARAGRAPH_GAP_SECONDS
            long_enough = len(current) >= PARAGRAPH_MIN_CHARS and current[-1] in ".?!"
            if gap or long_enough:
                paragraphs.append(current)
                current = text
            else:
                current += " " + text
        else:
            current = text
        prev_end = s["end"]
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


class Transcriber:
    def __init__(self, model_key: str):
        self.repo = WHISPER_MODELS[model_key]

    def transcribe(self, audio: Path, language: str | None) -> TranscriptResult:
        result = mlx_whisper.transcribe(
            str(audio), path_or_hf_repo=self.repo, language=language, verbose=None
        )
        return TranscriptResult(
            segments_to_paragraphs(result["segments"]), result["language"]
        )
