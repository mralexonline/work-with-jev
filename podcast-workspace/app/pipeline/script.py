"""Script stage: outline -> per-segment dialogue, each segment checkpointed."""
from __future__ import annotations

import json
import re
from typing import Any

from ..adapters.base import Ctx
from ..adapters.llm import LLMBase
from . import ckpt
from .spec import PodcastSpec


def _clean(lines: list[dict[str, str]], names: list[str]) -> list[dict[str, str]]:
    out = []
    by_lower = {n.lower(): n for n in names}
    for ln in lines:
        spk = by_lower.get(str(ln.get("speaker", "")).strip().lower())
        txt = re.sub(r"\s+", " ", str(ln.get("text", ""))).strip()
        txt = re.sub(r"[*_#`]|\[[^\]]*\]|\([^)]*\)", "", txt)  # strip markdown/stage directions
        txt = re.sub(r"\s+", " ", txt).strip()
        if spk and txt:
            out.append({"speaker": spk, "text": txt})
    if not out:
        raise ValueError("LLM returned no usable dialogue lines")
    return out


def build_script(spec: PodcastSpec, llm: LLMBase, ctx: Ctx, project_id: str, workdir) -> list[dict[str, str]]:
    brief: dict[str, Any] = {"topic": spec.topic, "duration_s": spec.duration_s, "seed": spec.seed,
                             "hosts": [{"name": h.name, "description": h.description} for h in spec.hosts],
                             "show_name": spec.spoken_group, "pace": round(spec.pace, 3)}
    names = [h.name for h in spec.hosts]
    ok = ckpt.key_of("outline", brief, llm.id)
    cp = ckpt.get(project_id, ok)
    if cp:
        outline = json.loads(open(cp["path"]).read())
    else:
        outline = llm.write_outline(brief)
        p = workdir / "outline.json"
        p.write_text(json.dumps(outline, indent=1))
        ckpt.put(project_id, ok, "script", p)
    ctx.log(f"outline: {len(outline)} segment(s)")
    lines: list[dict[str, str]] = []
    for i, seg in enumerate(outline):
        ctx.check_cancel()
        k = ckpt.key_of("segment", brief, llm.id, i, seg)
        cp = ckpt.get(project_id, k)
        if cp:
            seg_lines = json.loads(open(cp["path"]).read())
        else:
            seg_lines = _clean(llm.write_segment(brief, seg, lines, i, len(outline)), names)
            p = workdir / f"script_seg{i}.json"
            p.write_text(json.dumps(seg_lines, indent=1))
            ckpt.put(project_id, k, "script", p)
        lines += seg_lines
        ctx.log(f"segment {i + 1}/{len(outline)}: {len(seg_lines)} lines")
    (workdir / "script.json").write_text(json.dumps(lines, indent=1))
    return lines
