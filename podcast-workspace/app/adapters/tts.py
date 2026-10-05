"""Text-to-speech adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from ..config import get_settings
from .base import NotConfigured, TTSAdapter

# Kokoro voice packs mapped to the two fictional hosts. Chosen by ear-neutral labels only;
# Matt: adult male American voice. Chloe: adult female American voice.
KOKORO_DEFAULTS = {"Matt": "am_michael", "Chloe": "af_heart"}


class KokoroLocalTTS(TTSAdapter):
    id = "kokoro-onnx-local"
    paid = False

    def __init__(self):
        d = get_settings().models_dir
        onnx, voices = d / "kokoro-v1.0.int8.onnx", d / "voices-v1.0.bin"
        if not (onnx.exists() and voices.exists()):
            raise NotConfigured("Kokoro files missing: run `python scripts/fetch_models.py`")
        from kokoro_onnx import Kokoro  # imported lazily: heavy
        self._k = Kokoro(str(onnx), str(voices))

    def voices(self) -> list[str]:
        return sorted(self._k.get_voices())

    def synthesize(self, text: str, voice: str, out_wav: Path, *, speed: float = 1.0) -> dict[str, Any]:
        samples, sr = self._k.create(text, voice=voice, speed=speed, lang="en-us")
        samples = np.asarray(samples, dtype=np.float32)
        sf.write(out_wav, samples, sr, subtype="PCM_16")
        return {"seconds": len(samples) / sr, "sample_rate": sr}


def make_tts(model_id: str) -> TTSAdapter:
    if model_id == "kokoro-onnx-local":
        return KokoroLocalTTS()
    from .cloud import CloudTTS
    return CloudTTS(model_id)
