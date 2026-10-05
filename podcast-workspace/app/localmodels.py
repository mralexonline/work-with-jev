"""Download + verify small local model files (never executed, only read by onnxruntime)."""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from typing import Any

from .config import get_settings


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_files(model: dict[str, Any], log=print) -> list[str]:
    """Fetch any missing file and verify its pinned sha256. Raises on mismatch (file is removed)."""
    d = get_settings().models_dir
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for f in model.get("files", []):
        dest = d / f["path"]
        if not dest.exists():
            log(f"downloading {f['path']}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(f["url"], tmp)
            tmp.replace(dest)
        got = sha256(dest)
        if f.get("sha256") and got != f["sha256"]:
            dest.unlink()
            raise ValueError(f"sha256 mismatch for {f['path']}: pinned {f['sha256'][:12]}.. got {got[:12]}..; file removed")
        done.append(f"{f['path']} sha256={got[:12]}...")
    return done
