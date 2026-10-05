"""Voice, timing, camera plan and captions.

Output of this stage is the single source of truth for rendering: a timeline with each line's
start/end, per-speaker mouth-energy envelopes, a shot list and an ASS caption file.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from ..adapters.base import Ctx, TTSAdapter
from . import ckpt
from .spec import PodcastSpec

SR_OUT = 24000
MIN_SHOT_S = 2.4  # a line shorter than this is not worth a close-up
MAX_CLOSE_S = 14.0
TAIL_S = 1.2


def synthesize_lines(spec: PodcastSpec, tts: TTSAdapter, lines: list[dict[str, str]], ctx: Ctx,
                     project_id: str, workdir: Path, progress) -> list[dict[str, Any]]:
    voices = {h.name: (h.voice or "") for h in spec.hosts}
    audio_dir = workdir / "audio"
    audio_dir.mkdir(exist_ok=True)
    items = []
    for i, ln in enumerate(lines):
        ctx.check_cancel()
        k = ckpt.key_of("tts", tts.id, voices[ln["speaker"]], ln["text"])
        wav = audio_dir / f"{k}.wav"
        cp = ckpt.get(project_id, k)
        if cp:
            meta = json.loads(cp["meta_json"])
        else:
            meta = tts.synthesize(ln["text"], voices[ln["speaker"]], wav)
            ckpt.put(project_id, k, "voice", wav, meta)
        items.append({**ln, "wav": str(wav), "seconds": meta["seconds"], "sr": meta["sample_rate"]})
        progress((i + 1) / len(lines))
    return items


def build_timeline(spec: PodcastSpec, items: list[dict[str, Any]], workdir: Path) -> dict[str, Any]:
    rng = random.Random(spec.seed)
    t = 0.4  # short room-tone lead-in
    placed = []
    prev = None
    chunks, env_src = [np.zeros(int(0.4 * SR_OUT), dtype=np.float32)], []
    for it in items:
        gap = 0.0 if prev is None else (rng.uniform(0.12, 0.28) if prev == it["speaker"] else rng.uniform(0.2, 0.5))
        if gap:
            chunks.append(np.zeros(int(gap * SR_OUT), dtype=np.float32))
            t += gap
        x, sr = sf.read(it["wav"], dtype="float32")
        if sr != SR_OUT:
            x = np.interp(np.linspace(0, len(x), int(len(x) * SR_OUT / sr), endpoint=False), np.arange(len(x)), x).astype(np.float32)
        start = t
        chunks.append(x)
        t += len(x) / SR_OUT
        placed.append({"speaker": it["speaker"], "text": it["text"], "start": round(start, 3), "end": round(t, 3)})
        env_src.append((it["speaker"], start, x))
        prev = it["speaker"]
    chunks.append(np.zeros(int(TAIL_S * SR_OUT), dtype=np.float32))
    total = t + TAIL_S
    audio = np.concatenate(chunks)
    sf.write(workdir / "voice.wav", audio, SR_OUT, subtype="PCM_16")

    # per-speaker mouth-energy envelope at video fps (RMS per frame, normalised, lightly smoothed)
    n_frames = int(np.ceil(total * spec.fps))
    envs = {h.name: np.zeros(n_frames, dtype=np.float32) for h in spec.hosts}
    win = SR_OUT // spec.fps
    for spk, start, x in env_src:
        f0 = int(start * spec.fps)
        n = int(len(x) / win)
        for j in range(n):
            seg = x[j * win:(j + 1) * win]
            if f0 + j < n_frames:
                envs[spk][f0 + j] = float(np.sqrt(np.mean(seg ** 2)))
    for name, e in envs.items():
        ref = np.percentile(e[e > 0], 90) if np.any(e > 0) else 1.0
        e = np.clip(e / max(ref, 1e-6), 0, 1.2)
        k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
        envs[name] = np.convolve(e, k, mode="same").astype(np.float32)
    np.savez_compressed(workdir / "envelopes.npz", **{f"h{i}": envs[h.name] for i, h in enumerate(spec.hosts)})

    shots = plan_shots(spec, placed, total)
    (workdir / "captions.ass").write_text(make_ass(spec, placed))
    (workdir / "captions.srt").write_text(make_srt(placed))
    tl = {"total_s": round(total, 3), "fps": spec.fps, "lines": placed, "shots": shots,
          "hosts": [h.name for h in spec.hosts]}
    (workdir / "timeline.json").write_text(json.dumps(tl, indent=1))
    return tl


def plan_shots(spec: PodcastSpec, lines: list[dict[str, Any]], total: float) -> list[dict[str, Any]]:
    """Three angles: wide establishing/transition shots, individual close-ups on the speaker.

    Invariant: a close-up never plays while the OTHER host is talking. Lines too short for a
    close-up (< MIN_SHOT_S) play on the wide, which shows both hosts. Rhythm rules: open wide,
    return to wide after a few close-ups, break up long close-ups with a cut to wide.
    """
    idx = {h.name: i for i, h in enumerate(spec.hosts)}
    cams: list[tuple[float, str]] = []
    since_wide = 0
    for n, ln in enumerate(lines):
        dur = ln["end"] - ln["start"]
        if n == 0 or since_wide >= 3 or dur < MIN_SHOT_S:
            cam, since_wide = "wide", 0
        else:
            cam, since_wide = f"close_{idx[ln['speaker']]}", since_wide + 1
        cams.append((ln["start"] if n else 0.0, cam))
    shots: list[dict[str, Any]] = []
    for start, cam in cams:
        if shots and shots[-1]["camera"] == cam:
            continue
        shots.append({"camera": cam, "t0": start})
    for i, sh in enumerate(shots):
        sh["t1"] = shots[i + 1]["t0"] if i + 1 < len(shots) else total
    out: list[dict[str, Any]] = []
    for sh in shots:
        if sh["camera"] == "wide":
            out.append(sh)
            continue
        t = sh["t0"]  # same speaker, long monologue: keep cutting away to the wide and back
        while sh["t1"] - t > MAX_CLOSE_S:
            rem = sh["t1"] - t
            piece = 10.0 if rem - 12.5 >= MIN_SHOT_S else rem - 2.5 - MIN_SHOT_S
            out.append({"camera": sh["camera"], "t0": t, "t1": t + piece})
            out.append({"camera": "wide", "t0": t + piece, "t1": t + piece + 2.5})
            t += piece + 2.5
        out.append({"camera": sh["camera"], "t0": t, "t1": sh["t1"]})
    for sh in out:
        sh["t0"], sh["t1"] = round(sh["t0"], 3), round(sh["t1"], 3)
    out[-1]["camera"] = "wide"  # finish on the wide
    out[-1]["t1"] = round(total, 3)
    merged: list[dict[str, Any]] = []
    for sh in out:  # forcing the last shot wide can create wide+wide
        if merged and merged[-1]["camera"] == sh["camera"] == "wide":
            merged[-1]["t1"] = sh["t1"]
        else:
            merged.append(sh)
    return merged


def _chunks(text: str, max_chars: int = 42) -> list[str]:
    words, cur, res = text.split(), "", []
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            res.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        res.append(cur)
    # pair into two-line captions
    return ["\\N".join(res[i:i + 2]) for i in range(0, len(res), 2)]


def _ts_ass(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _timed_chunks(lines):
    """Spread each line's duration over its caption chunks proportionally to character count."""
    for ln in lines:
        parts = _chunks(ln["text"])
        weights = [max(len(p.replace("\\N", " ")), 1) for p in parts]
        tot, t = sum(weights), ln["start"]
        for p, w in zip(parts, weights):
            d = (ln["end"] - ln["start"]) * w / tot
            yield t, t + d, p
            t += d


def make_ass(spec: PodcastSpec, lines) -> str:
    w, h = spec.width, spec.height
    fs = round(h * 0.052)
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
            "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Cap,DejaVu Sans,{fs},&H00FFFFFF,&H000000FF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,{round(w*0.08)},{round(w*0.08)},{round(h*0.115)},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    ev = "".join(f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(b)},Cap,,0,0,0,,{p}\n" for a, b, p in _timed_chunks(lines))
    return head + ev


def make_srt(lines) -> str:
    out = []
    for i, (a, b, p) in enumerate(_timed_chunks(lines), 1):
        out.append(f"{i}\n{_ts_srt(a)} --> {_ts_srt(b)}\n{p.replace(chr(92) + 'N', chr(10))}\n")
    return "\n".join(out)
