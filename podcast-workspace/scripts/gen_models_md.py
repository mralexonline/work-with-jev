#!/usr/bin/env python3
"""Regenerate docs/MODELS.md from config/models.yaml so the doc cannot drift from the registry."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
reg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
order = ["llm", "tts", "image", "video", "lipsync"]
titles = {"llm": "Script / scene-plan LLMs", "tts": "Text-to-speech", "image": "Image generation / visuals", "video": "Video generation", "lipsync": "Lip-sync / audio-driven video"}
out = [f"# Models and licences\n\n*Generated from `config/models.yaml` by `scripts/gen_models_md.py`. Licence, size and revision facts were read from the Hugging Face API on **{reg['verified_on']}**. "
       "Hugging Face metadata is a hint, not legal advice: read each model card and licence text before commercial use, and check training-data and dependency terms separately.*\n",
       "**Status words** (strict): `working` = exercised end-to-end in this repo's tests; `implemented-untested` = code exists, never run against the real service; "
       "`placeholder` = stub only; `candidate` = researched, no adapter; `blocked` = licence forbids commercial use.\n",
       "**Licence classes**: `permissive` (e.g. Apache-2.0, MIT) · `open-weight-restricted` (use-based or custom terms such as OpenRAIL, or an API with its own terms) · `non-commercial` (disabled by default).\n"]
for k in order:
    ms = [m for m in reg["models"] if m["kind"] == k]
    notes = []
    if not ms:
        continue
    out.append(f"\n## {titles[k]}\n\n| Model | Status | Licence | Class | Weights | Suggested GPU | Runs on | Pinned revision |\n|---|---|---|---|---|---|---|---|")
    for m in ms:
        hw = m.get("hardware") or {}
        src = m.get("source") or {}
        rev = (src.get("revision") or "")[:10]
        repo = src.get("repo")
        name = f"[{m['name']}](https://huggingface.co/{repo})" if repo else m["name"]
        out.append(f"| {name}<br><code>{m['id']}</code> | {m['status']} | {m['license']['name']} | {m['license']['class']} | "
                   f"{str(hw.get('weights_gb', '—')) + (' GB' if hw.get('weights_gb') else '')} | {hw.get('suggested_gpu', '—')} | {m['runs_on']} | `{rev or '—'}` |")
        if m.get("notes"):
            notes.append(f"- `{m['id']}`: {m['notes']}")
    if notes:
        out.append("\n" + "\n".join(notes))
    notes = []
(ROOT / "docs" / "MODELS.md").write_text("\n".join(out) + "\n")
print("wrote docs/MODELS.md")
