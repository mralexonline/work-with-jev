"""Model registry: loads config/models.yaml and tracks install state."""
from __future__ import annotations

import importlib
from functools import lru_cache
from typing import Any

import yaml

from . import db
from .config import ROOT, get_settings

KINDS = ("llm", "tts", "image", "video", "lipsync")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def verified_on() -> str:
    return str(_load()["verified_on"])


def all_models() -> list[dict[str, Any]]:
    states = {r["model_id"]: r for r in db.q("SELECT * FROM models_state")}
    out = []
    for m in _load()["models"]:
        m = dict(m)
        st = states.get(m["id"])
        if st:
            m["install"] = {"status": st["status"], "detail": st["detail"]}
        elif m["runs_on"] == "local" and not m.get("files"):
            m["install"] = {"status": "built-in", "detail": "ships with this repo"}
        elif m["runs_on"] == "local" and all((get_settings().models_dir / f["path"]).exists() for f in m["files"]):
            m["install"] = {"status": "files present", "detail": "run Install to re-verify sha256"}
        else:
            m["install"] = {"status": "not_installed", "detail": ""}
        m["commercial_ok"] = (m.get("license") or {}).get("class") == "permissive"
        out.append(m)
    return out


def get_model(model_id: str) -> dict[str, Any]:
    for m in all_models():
        if m["id"] == model_id:
            return m
    raise KeyError(f"unknown model id: {model_id}")


def set_state(model_id: str, status: str, detail: str = "") -> None:
    db.x("INSERT INTO models_state(model_id,status,detail,updated_at) VALUES(?,?,?,?) "
         "ON CONFLICT(model_id) DO UPDATE SET status=excluded.status,detail=excluded.detail,updated_at=excluded.updated_at",
         (model_id, status, detail, db.now()))


def adapter_class(model_id: str):
    m = get_model(model_id)
    ref = m.get("adapter")
    if not ref:
        raise NotImplementedError(f"{model_id} has no adapter yet (status={m['status']})")
    mod, _, cls = ref.partition(":")
    return getattr(importlib.import_module(mod), cls)
