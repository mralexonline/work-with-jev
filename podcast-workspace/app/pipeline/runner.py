"""Podcast job orchestration with checkpoints, progress, cancellation and cleanup."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .. import budget, db, jobq
from ..adapters.base import Cancelled, Ctx, NotConfigured
from ..adapters.llm import make_llm
from ..adapters.tts import make_tts
from ..config import get_settings
from . import assemble, ckpt, draft_render, script, timeline
from .spec import PodcastSpec

STAGES = [("script", 0.05), ("voice", 0.25), ("plan", 0.03), ("render", 0.52), ("assemble", 0.12), ("verify", 0.03)]


def project_dir(project_id: str) -> Path:
    d = get_settings().projects_dir / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class _Progress:
    def __init__(self, job_id: str):
        self.job_id, self.base = job_id, {}
        acc = 0.0
        for name, w in STAGES:
            self.base[name] = (acc, w)
            acc += w

    def __call__(self, stage: str, frac: float = 0.0):
        b, w = self.base[stage]
        jobq.progress(self.job_id, b + w * frac, stage)


def run_podcast(job: dict[str, Any], worker_id: str) -> dict[str, Any]:
    spec = PodcastSpec(**json.loads(job["spec_json"]))
    pid = job["project_id"]
    wd = project_dir(pid)
    jid = job["id"]

    def check_cancel():
        jobq.heartbeat(jid, worker_id)
        if jobq.cancel_requested(jid):
            raise Cancelled("cancelled by owner")

    ctx = Ctx(jid, wd, lambda m, level="info": jobq.log(jid, m, level), check_cancel)
    prog = _Progress(jid)
    try:
        # ---- script -> voice -> plan, with one closed-loop length correction.
        # Word counts only roughly predict spoken length (it depends on the TTS), so after the first
        # pass we measure the real runtime and, if it is >10% off the brief, re-target the script once.
        tts = make_tts(spec.tts)
        llm = make_llm(spec.llm)
        for attempt in (1, 2):
            prog("script")
            lines = script.build_script(spec, llm, ctx, pid, wd)
            words = sum(len(l["text"].split()) for l in lines)
            ctx.log(f"script ready: {len(lines)} lines, {words} words (pace {spec.pace:.2f})")
            prog("script", 1)
            items = timeline.synthesize_lines(spec, tts, lines, ctx, pid, wd, lambda f: (prog("voice", f), check_cancel()))
            prog("plan")
            tl = timeline.build_timeline(spec, items, wd)
            off = tl["total_s"] / spec.duration_s
            if attempt == 1 and abs(off - 1) > 0.10:
                spec = spec.model_copy(update={"pace": max(0.4, min(2.5, spec.pace / off))})
                ctx.log(f"spoken length {tl['total_s']:.0f}s vs requested {spec.duration_s}s: re-targeting script (pace -> {spec.pace:.2f})")
                continue
            break
        cams = {}
        for s in tl["shots"]:
            cams[s["camera"]] = cams.get(s["camera"], 0) + 1
        ctx.log(f"timeline {tl['total_s']}s, {len(tl['shots'])} shots {cams}")
        if abs(off - 1) > 0.10:
            ctx.log(f"note: spoken length {tl['total_s']:.0f}s is still {abs(off - 1) * 100:.0f}% off the requested {spec.duration_s}s", "warn")
        prog("plan", 1)

        # ---- render shots (parallel, checkpointed per shot)
        if spec.visuals != "procedural-draft":
            raise NotConfigured(f"{spec.visuals}: cloud visual pipeline is not implemented/verified (docs/STATUS.md)")
        clips = render_shots(spec, tl, wd, pid, ctx, prog)

        # ---- assemble + verify
        prog("assemble")
        out = assemble.assemble(spec, clips, wd, tl["total_s"], ctx)
        prog("verify")
        facts = assemble.verify(out, spec, tl["total_s"])
        meta = {"output": str(out), "captions_srt": str(wd / "exports" / "captions.srt"), "verified": facts,
                "renderer": spec.visuals, "tts": spec.tts, "llm": spec.llm, "shots": cams,
                "draft_quality": spec.visuals == "procedural-draft"}
        shutil.copy(wd / "captions.srt", wd / "exports" / "captions.srt")
        (wd / "exports" / "episode.json").write_text(json.dumps({**meta, "timeline": tl}, indent=1))
        ctx.log(f"verified output: {facts}")
        return meta
    finally:
        cleanup(wd)


def render_shots(spec: PodcastSpec, tl: dict, wd: Path, pid: str, ctx: Ctx, prog) -> list[Path]:
    shots_dir = wd / "shots"
    shots_dir.mkdir(exist_ok=True)
    env = str(wd / "envelopes.npz")
    env_sig = hashlib.sha256(Path(env).read_bytes()).hexdigest()[:16]  # envelopes derive from the actual audio
    sd = spec.model_dump()
    clips: list[Path | None] = [None] * len(tl["shots"])
    todo = []
    for i, sh in enumerate(tl["shots"]):
        k = ckpt.key_of("shot", draft_render.RENDERER_VERSION, sd["width"], sd["height"], sd["fps"], sh, env_sig, spec.seed)
        out = shots_dir / f"{k}.mp4"
        cp = ckpt.get(pid, k)
        if cp:
            clips[i] = Path(cp["path"])
        else:
            todo.append((i, k, out, sh))
    ctx.log(f"render: {len(tl['shots']) - len(todo)} shot(s) cached, {len(todo)} to render")
    done = len(tl["shots"]) - len(todo)
    total = len(tl["shots"])
    workers = max(1, min(len(todo), (os.cpu_count() or 2)))
    if todo:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {}
            for i, k, out, sh in todo:
                futs[pool.submit(draft_render.render_shot, {
                    "spec": sd, "shot": sh, "out": str(out), "env": env, "names": [h.name for h in spec.hosts],
                    "job_id": ctx.job_id})] = (i, k, out)
            try:
                for f in as_completed(futs):
                    i, k, out = futs[f]
                    f.result()
                    ckpt.put(pid, k, "render", out)
                    clips[i] = out
                    done += 1
                    prog("render", done / total)
                    ctx.check_cancel()
            except BaseException:
                for f in futs:
                    f.cancel()
                pool.shutdown(wait=True, cancel_futures=True)  # worker cleanup: no stray render processes
                raise
    return [c for c in clips if c]


def cleanup(wd: Path) -> None:
    """Remove half-written files; finished checkpoints and exports are kept for resume/download."""
    for p in wd.rglob("*.part*"):
        p.unlink(missing_ok=True)
