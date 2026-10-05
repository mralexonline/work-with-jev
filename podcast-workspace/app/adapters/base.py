"""Adapter contracts. A new model plugs in by implementing one of these and being listed in
config/models.yaml. Heavy adapters never import their model code here: they call a cloud
worker (see cloud/) and exchange small JSON + files."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class NotConfigured(RuntimeError):
    """Adapter exists but credentials/infrastructure are missing. Never retried."""


class Cancelled(RuntimeError):
    """Cooperative cancellation requested by the owner."""


class Ctx:
    """Per-job context handed to adapters: logging, cancellation, spend accounting."""

    def __init__(self, job_id: str, project_dir: Path, log: Callable[..., None],  # log(message, level='info')
                 check_cancel: Callable[[], None]):
        self.job_id, self.project_dir, self.log, self.check_cancel = job_id, project_dir, log, check_cancel


class LLMAdapter:
    id = "llm"
    paid = False

    def complete(self, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 4096) -> str:
        raise NotImplementedError


class TTSAdapter:
    id = "tts"
    paid = False

    def voices(self) -> list[str]:
        raise NotImplementedError

    def synthesize(self, text: str, voice: str, out_wav: Path, *, speed: float = 1.0) -> dict[str, Any]:
        """Write a mono wav, return {"seconds": float, "sample_rate": int}."""
        raise NotImplementedError


class ImageAdapter:
    id = "image"
    paid = False

    def generate(self, prompt: str, out_png: Path, *, seed: int, reference: Path | None = None,
                 size: tuple[int, int] = (1280, 720)) -> None:
        raise NotImplementedError


class LipSyncAdapter:
    """Audio-driven talking video. Input: still or short video + speech wav; output: mp4 clip."""
    id = "lipsync"
    paid = False

    def render(self, image: Path, audio: Path, prompt: str, out_mp4: Path, *, seed: int) -> dict[str, Any]:
        raise NotImplementedError
