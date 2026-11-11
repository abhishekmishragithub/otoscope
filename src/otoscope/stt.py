"""Optional local-only STT. Supply an existing model directory; never download."""

from __future__ import annotations

import importlib
import io
import wave
from pathlib import Path
from typing import Any


class LocalWhisper:
    def __init__(self, model_directory: str | Path, *, language: str | None = None):
        path = Path(model_directory)
        if not path.is_dir():
            raise ValueError("provide a pre-downloaded local faster-whisper model directory")
        # PyAV's BSD license alone says nothing about its linked FFmpeg build.
        # Refuse GPL/nonfree/unknown builds before importing faster-whisper.
        core = importlib.import_module("av._core")
        metadata = core.library_meta
        if not metadata:
            raise RuntimeError("FFmpeg license metadata unavailable")
        for item in metadata.values():
            license_name = item.get("license", b"")
            config = item.get("configuration", b"")
            if isinstance(license_name, bytes):
                license_name = license_name.decode("utf-8", errors="replace")
            if isinstance(config, bytes):
                config = config.decode("utf-8", errors="replace")
            if (
                "LGPL" not in license_name
                and "Lesser General Public License" not in license_name
                or "--enable-gpl" in config
                or "--enable-nonfree" in config
            ):
                raise RuntimeError("STT requires a verified LGPL-only FFmpeg/PyAV build")
        module = importlib.import_module("faster_whisper")
        self.model: Any = module.WhisperModel(
            str(path.resolve()), device="cpu", compute_type="int8", local_files_only=True
        )
        self.language = language

    def __call__(self, pcm: bytes, sample_rate: int) -> str:
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        stream.seek(0)
        segments, _ = self.model.transcribe(stream, language=self.language)
        return " ".join(segment.text.strip() for segment in segments)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Case-folded whitespace-tokenized WER, without hidden text normalization."""
    expected, actual = reference.casefold().split(), hypothesis.casefold().split()
    if not expected:
        raise ValueError("WER needs a nonempty reference")
    row = list(range(len(actual) + 1))
    for i, word in enumerate(expected, 1):
        next_row = [i]
        for j, token in enumerate(actual, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (word != token)))
        row = next_row
    return row[-1] / len(expected)
