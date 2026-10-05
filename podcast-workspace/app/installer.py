"""Install planning and execution.

Rules:
  * plan() is pure: it explains what would happen, where, and what it may cost. No side effects.
  * Local files are downloaded to models/ and sha256-verified; they are data, never executed.
  * Cloud models are installed INTO Modal (weights volume + pinned image). The app server never
    clones or runs third-party repository code.
  * 'blocked' (non-commercial) models need an explicit owner override; 'candidate' models have
    no adapter and cannot be installed; arbitrary repositories go through the custom adapter flow.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import budget, jobq, registry
from .adapters.base import Ctx, NotConfigured
from .config import ROOT, get_settings
from .localmodels import ensure_files


def plan(model_id: str) -> dict[str, Any]:
    m = registry.get_model(model_id)
    s = get_settings()
    hw = m.get("hardware") or {}
    gb = float(hw.get("weights_gb") or 0)
    where = "this machine (CPU, free)" if m["runs_on"] == "local" else "a Modal weights volume (CPU download job, no GPU billed)"
    vol_month = 0.0 if m["runs_on"] == "local" else round(gb * budget.pricing()["modal"]["volume_usd_per_gib_month"], 2)
    blockers = []
    if m["status"] == "blocked":
        blockers.append(f"licence class '{m['license']['class']}' does not allow commercial use; install needs an explicit override")
    if m["status"] in ("candidate",) and not m.get("adapter"):
        blockers.append("no adapter code exists yet (status: candidate)")
    if m["runs_on"] == "modal" and not s.cloud_enabled:
        blockers.append("cloud is disabled (CLOUD_ENABLED=0) and needs Modal credentials")
    if (m.get("source") or {}).get("repo", "").startswith("black-forest-labs") and not s.hf_token:
        blockers.append("gated Hugging Face repo: accept its terms and set HF_TOKEN")
    return {
        "model_id": model_id, "name": m["name"], "status": m["status"], "installs_to": where,
        "weights_gb": gb, "pinned_revision": (m.get("source") or {}).get("revision"),
        "storage_usd_per_month_if_billed": vol_month, "needs_owner_approval": m["runs_on"] != "local",
        "executes_third_party_code_on_app_server": False, "blockers": blockers,
        "note": "Volume price is the list price; Modal's page also states an included allowance - see docs/COSTS.md.",
    }


def run_install(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    import json
    spec = json.loads(job["spec_json"])
    mid = spec["model_id"]
    m = registry.get_model(mid)
    ctx = Ctx(job["id"], ROOT, lambda t: jobq.log(job["id"], t), lambda: None)
    p = plan(mid)
    if m["status"] == "blocked" and not spec.get("override_noncommercial"):
        raise ValueError(f"{mid} is licensed {m['license']['name']} (non-commercial); refusing without override")
    if m["runs_on"] == "local":
        registry.set_state(mid, "installing")
        try:
            done = ensure_files(m, ctx.log)
        except Exception as e:
            registry.set_state(mid, "failed", str(e))
            raise
        if m["id"] == "kokoro-onnx-local":  # real load test: refuse to call it installed if it cannot synthesize
            from .adapters.tts import KokoroLocalTTS
            KokoroLocalTTS().voices()
        registry.set_state(mid, "ready", "; ".join(done))
        return {"model_id": mid, "status": "ready", "files": done}
    if p["blockers"]:
        raise NotConfigured("; ".join(p["blockers"]))
    raise NotConfigured("Modal weight download (cloud/modal_app.py::download_weights) is implemented but untested; "
                        "run it by hand once and review docs/STATUS.md before wiring it to the UI")


# --------------------------------------------------------------------------- custom adapters
CUSTOM_DIR = ROOT / "adapters_custom"
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def scaffold_manifest(inspection: dict[str, Any]) -> Path:
    """Write a DRAFT adapter manifest from an inspection. It cannot run until the owner reviews it."""
    slug = re.sub(r"[^a-z0-9]+", "-", inspection["repo"].lower()).strip("-")
    d = CUSTOM_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": slug, "source": {"type": inspection["type"], "repo": inspection["repo"], "revision": inspection.get("revision") or "FILL-IN-40-CHAR-SHA"},
        "license": inspection["license"], "kind": "TODO: tts|image|video|lipsync",
        "runtime": {"python": "3.11", "lockfile": "requirements.lock (you must produce and review this)", "gpu": "TODO", "timeout_s": 1800},
        "entrypoint": {"command": "TODO: exact command from the project README, verified by hand", "input": "job.json", "output": "result.json"},
        "io_contract": "see docs/CUSTOM_ADAPTERS.md",
        "review": {"reviewed_by_owner": False, "reviewed_files": [], "notes": "set reviewed_by_owner: true only after reading the code you will run"},
        "warnings_at_inspection": inspection.get("warnings", []),
    }
    (d / "adapter.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return d / "adapter.yaml"


def validate_manifest(path: Path) -> list[str]:
    m = yaml.safe_load(path.read_text())
    errs = []
    if not SHA40.match(str(m["source"].get("revision", ""))):
        errs.append("source.revision must be a full 40-character commit SHA (no branches or tags)")
    if not m["review"].get("reviewed_by_owner"):
        errs.append("review.reviewed_by_owner is false: the owner has not reviewed this code")
    if m["license"].get("class") in ("non-commercial", "unknown"):
        errs.append(f"licence class {m['license'].get('class')} blocks commercial use")
    for k in ("kind", "entrypoint"):
        if "TODO" in str(m.get(k)):
            errs.append(f"{k} still contains TODO")
    return errs
