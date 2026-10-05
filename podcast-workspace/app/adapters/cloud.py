"""Cloud GPU adapters. STATUS: implemented-untested - never run against a real Modal account.

The app server only SENDS small JSON jobs to functions deployed from cloud/modal_app.py and
fetches the resulting files. No downloaded model/repository code runs here; it runs inside
Modal containers built from pinned images.

Every call is guarded: provider enabled -> budget check -> spawn -> poll (with cancel) -> usage
recorded from the *measured* GPU seconds the function reports.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .. import budget
from ..config import get_settings
from .base import Cancelled, Ctx, ImageAdapter, LipSyncAdapter, NotConfigured, TTSAdapter


class ModalClient:
    """Thin wrapper over the Modal SDK so tests can substitute a fake."""

    def __init__(self, ctx: Ctx):
        s = get_settings()
        if not s.cloud_enabled:
            raise NotConfigured("cloud disabled: set CLOUD_ENABLED=1 after reading docs/COSTS.md")
        try:
            import modal  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise NotConfigured("pip install -r requirements-cloud.txt to enable Modal") from e
        self.modal, self.app, self.ctx = modal, s.modal_app_name, ctx

    def call(self, function: str, payload: dict[str, Any], *, est_usd: float, stage: str,
             output_seconds: float | None = None, poll_s: float = 3.0, timeout_s: float = 3600) -> dict[str, Any]:
        budget.enforce_job_cap(self.ctx.job_id, est_usd)       # refuse before spending
        fn = self.modal.Function.from_name(self.app, function)
        fc = fn.spawn(payload)
        self.ctx.log(f"cloud call {function} started")
        t0 = time.time()
        try:
            while True:
                try:
                    res = fc.get(timeout=0)
                    break
                except TimeoutError:
                    pass
                self.ctx.check_cancel()                            # raises Cancelled
                if time.time() - t0 > timeout_s:
                    raise TimeoutError(f"{function} exceeded {timeout_s}s")
                time.sleep(poll_s)
        except BaseException:
            try:
                fc.cancel()                                        # worker cleanup: stop billing now
                self.ctx.log(f"cloud call {function} cancelled")
            except Exception:
                pass
            raise
        gpu = res.get("gpu"); gpu_s = float(res.get("gpu_seconds", 0))
        cost = gpu_s * budget.gpu_rate(gpu) if gpu else 0.0
        budget.record(self.ctx.job_id, "modal", "gpu_seconds", gpu_s, "s", cost, stage=stage,
                      output_seconds=output_seconds)
        return res


class _CloudBase:
    paid = True
    function = ""

    def __init__(self, model_id: str, ctx: Ctx | None = None):
        self.id, self.ctx = model_id, ctx
        if not get_settings().cloud_enabled:
            raise NotConfigured(f"{model_id} runs on a cloud GPU; cloud is disabled or not installed (see docs/STATUS.md)")

    def _client(self) -> ModalClient:
        if not self.ctx:
            raise NotConfigured("no job context")
        return ModalClient(self.ctx)


class CloudTTS(_CloudBase, TTSAdapter):
    function = "tts"

    def voices(self) -> list[str]:
        raise NotConfigured("voice list comes from the deployed function; not implemented")

    def synthesize(self, text, voice, out_wav: Path, *, speed=1.0):
        raise NotConfigured("cloud TTS function (cloud/modal_app.py::tts) is a placeholder")


class CloudImage(_CloudBase, ImageAdapter):
    function = "image"

    def generate(self, prompt, out_png, *, seed, reference=None, size=(1280, 720)):
        raise NotConfigured("cloud image function (cloud/modal_app.py::image) is a placeholder")


class CloudLipSync(_CloudBase, LipSyncAdapter):
    function = "lipsync"

    def render(self, image, audio, prompt, out_mp4, *, seed):
        raise NotConfigured("cloud lip-sync function (cloud/modal_app.py::lipsync) is a placeholder; "
                            "its model command must first be verified in the proof run")
