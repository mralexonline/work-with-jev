#!/usr/bin/env python3
"""Download the free local model files declared in config/models.yaml and verify pinned sha256."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import registry  # noqa: E402
from app.localmodels import ensure_files  # noqa: E402

rc = 0
for m in registry._load()["models"]:
    if m.get("files"):
        try:
            for line in ensure_files(m):
                print("ok", line)
        except Exception as e:  # noqa: BLE001
            print("FAILED", m["id"], e, file=sys.stderr)
            rc = 1
sys.exit(rc)
