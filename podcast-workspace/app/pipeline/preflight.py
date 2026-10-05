"""Fail fast, before anything is queued or billed."""
from __future__ import annotations

from .. import registry
from ..adapters.llm import TemplateLLM
from ..config import get_settings
from .spec import PodcastSpec


def check(spec: PodcastSpec) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    s = get_settings()
    for kind, mid in (("llm", spec.llm), ("tts", spec.tts), ("visuals", spec.visuals)):
        try:
            m = registry.get_model(mid)
        except KeyError:
            errors.append(f"unknown {kind} model '{mid}'")
            continue
        if m["status"] == "blocked":
            errors.append(f"{mid}: licence ({m['license']['name']}) does not permit commercial use")
        elif m["status"] in ("placeholder", "candidate"):
            errors.append(f"{mid}: status '{m['status']}' - no working adapter yet (see docs/STATUS.md)")
        elif m["status"] == "implemented-untested":
            warnings.append(f"{mid}: implemented but never run against the real service")
        if m["runs_on"] == "modal" and not s.cloud_enabled:
            errors.append(f"{mid}: needs the cloud GPU worker, which is disabled")
    if spec.llm == "template-local" and spec.duration_s > TemplateLLM.MAX_SECONDS:
        errors.append(f"the built-in template writer only supports episodes up to {TemplateLLM.MAX_SECONDS}s; "
                      "configure a real LLM (LLM_BASE_URL/LLM_MODEL or ANTHROPIC_API_KEY) for longer shows")
    if spec.tts == "kokoro-onnx-local" and not (s.models_dir / "kokoro-v1.0.int8.onnx").exists():
        errors.append("Kokoro files missing: run `python scripts/fetch_models.py` or press Install on the model")
    if spec.visuals == "procedural-draft":
        warnings.append("Visuals use the free stylised draft renderer: not realistic, no generative lip-sync.")
        if spec.duration_s > 120:
            warnings.append(f"CPU draft rendering takes roughly 1-2x the episode length (~{spec.duration_s // 30 * 1} min for this one).")
    return {"errors": errors, "warnings": warnings}
