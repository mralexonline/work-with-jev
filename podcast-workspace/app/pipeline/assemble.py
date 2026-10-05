"""Assembly: concat shot clips, mix audio, burn captions and the bottom-left group name.

Shared by every renderer: the draft renderer and (later) cloud video adapters both hand in
per-shot mp4 clips of identical size/fps with no audio.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..adapters.base import Ctx

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def run(cmd: list[str], cwd: Path, ctx: Ctx | None = None) -> None:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-800:]}")


def ffprobe(path: Path) -> dict[str, Any]:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def assemble(spec, clips: list[Path], workdir: Path, total_s: float, ctx: Ctx) -> Path:
    exports = workdir / "exports"
    exports.mkdir(exist_ok=True)
    out = exports / "episode.mp4"
    part = exports / "episode.part.mp4"
    lst = workdir / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    (workdir / "watermark.txt").write_text(spec.group_name)  # textfile avoids drawtext escaping of '&'

    fs = round(spec.height * 0.036)
    vf = []
    if spec.captions:
        vf.append(f"subtitles=captions.ass:fontsdir={Path(FONT).parent}")
    vf.append(f"drawtext=fontfile={FONT}:textfile=watermark.txt:fontcolor=white:fontsize={fs}:"
              f"box=1:boxcolor=black@0.55:boxborderw={round(fs * 0.4)}:x={round(spec.width * 0.02)}:y=h-th-{round(spec.height * 0.03)}")
    af = ("[1:a]aresample=48000,highpass=f=70,acompressor=threshold=-20dB:ratio=3:attack=5:release=90,"
          "loudnorm=I=-16:TP=-1.5:LRA=11[v];"
          f"anoisesrc=d={total_s:.2f}:c=pink:a=0.0015:r=48000,lowpass=f=1800[n];"
          "[v][n]amix=inputs=2:duration=first:normalize=0[a]")
    ctx.log("assembling: concat + captions + watermark + audio mix")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-i", "voice.wav",
         "-filter_complex", af, "-vf", ",".join(vf), "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(spec.fps),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total_s:.3f}", str(part)],
        cwd=workdir)
    part.replace(out)
    return out


def verify(out: Path, spec, total_s: float) -> dict[str, Any]:
    """Facts about the finished file, measured by ffprobe - not assumed."""
    info = ffprobe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    res = {"duration_s": round(dur, 2), "expected_s": round(total_s, 2), "width": v["width"], "height": v["height"],
           "video_codec": v["codec_name"], "audio_codec": a["codec_name"] if a else None,
           "size_mb": round(int(info["format"]["size"]) / 1e6, 2)}
    problems = []
    if abs(dur - total_s) > 0.5:
        problems.append(f"duration {dur:.2f}s differs from audio timeline {total_s:.2f}s")
    if (v["width"], v["height"]) != (spec.width, spec.height):
        problems.append("unexpected resolution")
    if not a:
        problems.append("no audio stream")
    res["problems"] = problems
    if problems:
        raise RuntimeError("output verification failed: " + "; ".join(problems))
    return res
