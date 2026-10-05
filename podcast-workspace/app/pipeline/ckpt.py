"""Checkpoints: content-addressed artefacts so a job can resume after a crash or browser close."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import db


def key_of(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def get(project_id: str, key: str) -> dict[str, Any] | None:
    r = db.q1("SELECT * FROM checkpoints WHERE project_id=? AND key=?", (project_id, key))
    if r and r["path"] and not Path(r["path"]).exists():
        return None  # file vanished: treat as missing
    return r


def put(project_id: str, key: str, stage: str, path: Path | str | None, meta: dict | None = None, cost_usd: float = 0) -> None:
    db.x("INSERT OR REPLACE INTO checkpoints(project_id,key,stage,path,meta_json,cost_usd,created_at) VALUES(?,?,?,?,?,?,?)",
         (project_id, key, stage, str(path) if path else None, db.dumps(meta or {}), cost_usd, db.now()))


def list_for(project_id: str) -> list[dict[str, Any]]:
    return db.q("SELECT key,stage,path,cost_usd,created_at FROM checkpoints WHERE project_id=? ORDER BY created_at", (project_id,))
