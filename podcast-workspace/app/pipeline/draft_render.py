"""Free CPU 'draft' scene renderer: a stylised animatic of two hosts in a studio.

HONEST SCOPE: this is flat-shaded cartoon art drawn with Pillow. It is NOT realistic and does
not use any generative model. Its job is to prove the parts of the pipeline that do not need a
GPU - timing, three camera angles, mouth movement driven by the real audio, idle body motion,
shot checkpoints, cancellation - and to produce the same per-shot clip files a cloud
image-to-video/lip-sync adapter would produce, so assembly code is shared.
"""
from __future__ import annotations

import math
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import jobq
from ..adapters.base import Cancelled

RENDERER_VERSION = "draft-1"
WORLD_W, WORLD_H = 1920, 1080
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SS = 2  # supersampling factor

# Camera framing in world coordinates: (centre x, centre y, zoom)
CAMERAS = {"wide": (960, 520, 1.0), "close_0": (640, 470, 2.15), "close_1": (1280, 470, 2.15)}
HOST_X = (640, 1280)

PALETTES = {
    0: dict(skin=(196, 148, 108), shade=(168, 120, 86), hair=(52, 36, 28), shirt=(52, 70, 96), jacket=(36, 44, 58),
            lips=(150, 92, 80), iris=(70, 46, 30), long_hair=False, stubble=True, width=1.0),
    1: dict(skin=(247, 224, 210), shade=(224, 196, 182), hair=(178, 58, 34), shirt=(116, 146, 120), jacket=None,
            lips=(190, 98, 98), iris=(60, 98, 110), long_hair=True, stubble=False, width=0.88),
}


class View:
    def __init__(self, w: int, h: int, cam: tuple[float, float, float], push: float = 0.0, dx: float = 0.0, dy: float = 0.0):
        cx, cy, z = cam
        self.w, self.h = w, h
        self.z = z * (1 + push) * (w / WORLD_W)
        self.cx, self.cy = cx + dx, cy + dy

    def p(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.cx) * self.z + self.w / 2, (y - self.cy) * self.z + self.h / 2)

    def pts(self, seq):
        return [self.p(x, y) for x, y in seq]

    def r(self, v: float) -> float:
        return v * self.z

    def box(self, x0, y0, x1, y1):
        a, b = self.p(x0, y0), self.p(x1, y1)
        return [a[0], a[1], b[0], b[1]]


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD, max(size, 6))


@lru_cache(maxsize=12)
def _background(w: int, h: int, cam: tuple[float, float, float]) -> Image.Image:
    """Static studio behind the hosts, drawn once per camera/process."""
    v = View(w, h, cam)
    # wall gradient
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    top, bot = np.array([34, 46, 58], np.float32), np.array([22, 28, 36], np.float32)
    img = Image.fromarray((top * (1 - ys) + bot * ys).repeat(w, axis=1).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    # wood slat panel
    d.rectangle(v.box(0, 230, WORLD_W, 860), fill=(92, 62, 42))
    for i in range(0, WORLD_W, 48):
        shade = 8 if (i // 48) % 2 else -6
        d.rectangle(v.box(i, 230, i + 40, 860), fill=(104 + shade, 72 + shade, 50 + shade))
    # shelves with books and plant (left) and (right)
    for sx in (60, 1540):
        d.rectangle(v.box(sx, 330, sx + 320, 346), fill=(60, 40, 28))
        d.rectangle(v.box(sx, 560, sx + 320, 576), fill=(60, 40, 28))
        x = sx + 14
        for k, c in enumerate([(176, 70, 60), (60, 110, 150), (220, 190, 90), (90, 140, 100), (150, 90, 150), (200, 120, 70)]):
            hgt = 120 + (k * 37) % 50
            d.rectangle(v.box(x, 330 - hgt, x + 34, 330), fill=c)
            x += 38 + (k % 2) * 6
        d.ellipse(v.box(sx + 230, 470, sx + 300, 560), fill=(50, 120, 74))
        d.rectangle(v.box(sx + 245, 540, sx + 285, 560), fill=(150, 80, 60))
        for k in range(4):
            d.rectangle(v.box(sx + 20 + k * 50, 560 - 90, sx + 54 + k * 50, 560), fill=[(220, 220, 210), (70, 90, 130), (190, 90, 70), (110, 150, 120)][k])
    # neon sign (a studio prop; the owner's group name is the bottom-left overlay, added in assembly)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    f = _font(int(v.r(74)))
    txt = "REAL TALK"
    tw = gd.textlength(txt, font=f)
    px, py = v.p(960, 150)
    gd.text((px - tw / 2, py - v.r(36)), txt, font=f, fill=(255, 120, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(max(2, v.r(18))))
    img = Image.fromarray(np.clip(np.asarray(img, np.int16) + np.asarray(glow, np.int16) * 0.9, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    d.text((px - tw / 2, py - v.r(36)), txt, font=f, fill=(255, 214, 190))
    # pendant lamps
    for lx in (330, 960, 1590):
        d.line([v.p(lx, 0), v.p(lx, 70)], fill=(20, 20, 20), width=max(1, int(v.r(3))))
        d.pieslice(v.box(lx - 46, 40, lx + 46, 130), 180, 360, fill=(30, 30, 34))
        d.ellipse(v.box(lx - 30, 98, lx + 30, 128), fill=(255, 226, 160))
    # floor strip
    d.rectangle(v.box(0, 860, WORLD_W, WORLD_H), fill=(40, 34, 32))
    return img


def _env(path: str, idx: int, cache: dict = {}) -> np.ndarray:  # noqa: B006 - process-local cache on purpose
    if path not in cache:
        cache[path] = np.load(path)
    return cache[path][f"h{idx}"]


def _noise(t: float, seed: float) -> float:
    return (math.sin(t * 0.9 + seed) + 0.6 * math.sin(t * 1.7 + seed * 2.1) + 0.3 * math.sin(t * 3.1 + seed * 0.7)) / 1.9


def _blink(t: float, seed: int) -> float:
    """1.0 = open, 0 = closed; a blink roughly every 3-5 s."""
    period = 3.6 + (seed % 3) * 0.7
    ph = (t + seed * 1.3) % period
    return 1.0 - max(0.0, 1.0 - abs(ph - 0.08) / 0.09) if ph < 0.18 else 1.0


def _person(d: ImageDraw.ImageDraw, v: View, idx: int, t: float, mouth: float, energy: float, other_energy: float):
    P = PALETTES[idx]
    cx = HOST_X[idx]
    wid = P["width"]
    breathe = math.sin(t * 1.5 + idx) * 3.5
    sway = _noise(t, idx * 3.3) * (4 + 11 * energy)
    nod = (math.sin(t * 3.4 + idx) * 4.0) if (other_energy > 0.35 and energy < 0.15) else 0.0
    head_dx, head_dy = sway * 0.8, breathe + nod
    body_dx = sway * 0.4
    S = lambda *a: v.p(*a)  # noqa: E731

    # long hair behind head/shoulders (Chloe)
    if P["long_hair"]:
        d.ellipse(v.box(cx - 132 + head_dx, 250 + head_dy, cx + 132 + head_dx, 560 + head_dy), fill=P["hair"])
        d.polygon(v.pts([(cx - 132 + head_dx, 440 + head_dy), (cx - 170 + body_dx, 800), (cx - 60 + body_dx, 840), (cx - 60 + head_dx, 470 + head_dy)]), fill=P["hair"])
        d.polygon(v.pts([(cx + 132 + head_dx, 440 + head_dy), (cx + 170 + body_dx, 800), (cx + 60 + body_dx, 840), (cx + 60 + head_dx, 470 + head_dy)]), fill=P["hair"])

    # torso
    tw = 175 * wid
    d.rounded_rectangle(v.box(cx - tw + body_dx, 520 + breathe, cx + tw + body_dx, 1120), radius=int(v.r(95)), fill=P["shirt"])
    if P["jacket"]:
        d.rounded_rectangle(v.box(cx - tw - 6 + body_dx, 520 + breathe, cx + tw + 6 + body_dx, 1120), radius=int(v.r(95)), fill=P["jacket"])
        d.polygon(v.pts([(cx - 52 + body_dx, 524 + breathe), (cx + 52 + body_dx, 524 + breathe), (cx + 0 + body_dx, 700 + breathe)]), fill=P["shirt"])
        d.line([S(cx - 52 + body_dx, 524 + breathe), S(cx - 16 + body_dx, 800)], fill=(26, 32, 42), width=max(2, int(v.r(7))))
        d.line([S(cx + 52 + body_dx, 524 + breathe), S(cx + 16 + body_dx, 800)], fill=(26, 32, 42), width=max(2, int(v.r(7))))
    else:
        d.polygon(v.pts([(cx - 60 + body_dx, 526 + breathe), (cx + 60 + body_dx, 526 + breathe), (cx + body_dx, 640 + breathe)]), fill=P["skin"])
    # neck
    d.rounded_rectangle(v.box(cx - 36 + body_dx * 0.7, 440 + head_dy, cx + 36 + body_dx * 0.7, 540 + breathe), radius=int(v.r(18)), fill=P["shade"])

    # head
    hx, hy = cx + head_dx, 365 + head_dy
    d.ellipse(v.box(hx - 84 * (0.95 if idx else 1), hy - 104, hx + 84 * (0.95 if idx else 1), hy + 104), fill=P["skin"])
    d.ellipse(v.box(hx - 98, hy - 12, hx - 78, hy + 40), fill=P["skin"])  # ears
    d.ellipse(v.box(hx + 78, hy - 12, hx + 98, hy + 40), fill=P["skin"])
    # jaw shade / stubble
    if P["stubble"]:
        d.pieslice(v.box(hx - 80, hy - 40, hx + 80, hy + 106), 15, 165, fill=tuple(int(c * 0.86) for c in P["skin"]))
        d.ellipse(v.box(hx - 60, hy + 6, hx + 60, hy + 100), fill=P["skin"])
    # hair on top
    if P["long_hair"]:
        d.pieslice(v.box(hx - 92, hy - 124, hx + 92, hy + 40), 180, 360, fill=P["hair"])
        d.polygon(v.pts([(hx - 92, hy - 40), (hx - 30, hy - 100), (hx + 40, hy - 74), (hx + 92, hy - 20), (hx + 92, hy - 70), (hx, hy - 118), (hx - 92, hy - 70)]), fill=P["hair"])
        d.polygon(v.pts([(hx - 90, hy - 30), (hx - 40, hy - 96), (hx + 6, hy - 86), (hx - 60, hy + 30), (hx - 84, hy + 60)]), fill=tuple(min(255, c + 14) for c in P["hair"]))
    else:
        d.pieslice(v.box(hx - 90, hy - 128, hx + 90, hy + 20), 180, 360, fill=P["hair"])
        d.polygon(v.pts([(hx - 90, hy - 44), (hx - 74, hy - 8), (hx - 84, hy - 4)]), fill=P["hair"])
        d.polygon(v.pts([(hx + 90, hy - 44), (hx + 74, hy - 8), (hx + 84, hy - 4)]), fill=P["hair"])

    # eyes
    gaze = (-1 if idx else 1) * 5 + _noise(t, idx + 9) * 2
    op = _blink(t, idx + 1)
    brow_up = 7 * min(energy * 1.4, 1.0)
    for sx in (-34, 34):
        ex, ey = hx + sx, hy - 4
        d.ellipse(v.box(ex - 17, ey - 11 * op - 1, ex + 17, ey + 11 * op + 1), fill=(250, 250, 248))
        if op > 0.35:
            d.ellipse(v.box(ex + gaze - 8, ey - 8 * op, ex + gaze + 8, ey + 8 * op), fill=P["iris"])
            d.ellipse(v.box(ex + gaze - 3.5, ey - 3.5 * op, ex + gaze + 3.5, ey + 3.5 * op), fill=(12, 12, 14))
        d.line([S(ex - 20, ey - 28 - brow_up), S(ex + 20, ey - 31 - brow_up + (4 if sx < 0 else 0))], fill=P["hair"], width=max(2, int(v.r(7))))
    # nose
    d.polygon(v.pts([(hx - 4, hy + 4), (hx - 17, hy + 44), (hx + 17, hy + 44)]), fill=P["shade"])
    # mouth: height follows the real audio envelope
    m = max(0.0, min(1.0, mouth))
    my = hy + 68
    mw = 25 + 7 * m
    mh = 3 + 27 * m
    d.ellipse(v.box(hx - mw - 3, my - mh * 0.5 - 4, hx + mw + 3, my + mh * 0.5 + 5), fill=P["lips"])
    d.ellipse(v.box(hx - mw, my - mh * 0.5, hx + mw, my + mh * 0.5 + 1), fill=(66, 26, 32))
    if m > 0.35:
        d.rectangle(v.box(hx - mw * 0.7, my - mh * 0.5, hx + mw * 0.7, my - mh * 0.5 + 6 + 4 * m), fill=(246, 244, 240))
    if m < 0.12:  # resting half-smile
        d.arc(v.box(hx - 28, my - 16, hx + 28, my + 16), 20, 160, fill=tuple(int(c * 0.7) for c in P["lips"]), width=max(2, int(v.r(4))))

    # headphones
    band = max(2, int(v.r(10)))
    d.arc(v.box(hx - 104, hy - 126, hx + 104, hy + 40), 188, 352, fill=(24, 24, 28), width=band)
    for sx in (-1, 1):
        d.rounded_rectangle(v.box(hx + sx * 104 - 20, hy - 26, hx + sx * 104 + 20, hy + 46), radius=int(v.r(14)), fill=(28, 28, 34))
        d.rounded_rectangle(v.box(hx + sx * 104 - 12, hy - 18, hx + sx * 104 + 12, hy + 38), radius=int(v.r(10)), fill=(52, 52, 60))

    # gesturing hand while speaking
    g = max(0.0, math.sin(t * 2.3 + idx * 1.7)) * min(energy * 1.6, 1.0)
    if g > 0.05:
        ex, ey = cx + (tw + 10) * (1 if idx == 0 else -1) + body_dx, 860 - 30 * g
        hxp, hyp = ex + (60 if idx == 0 else -60) * g, 760 - 210 * g
        d.line([S(ex, 880), S(hxp, hyp)], fill=P["jacket"] or P["shirt"], width=max(3, int(v.r(46))))
        d.ellipse(v.box(hxp - 30, hyp - 30, hxp + 30, hyp + 30), fill=P["skin"])


def _foreground(d: ImageDraw.ImageDraw, v: View, names: list[str]):
    # desk top and front
    d.polygon(v.pts([(0, 850), (WORLD_W, 850), (WORLD_W, 900), (0, 900)]), fill=(150, 104, 70))
    d.rectangle(v.box(0, 900, WORLD_W, WORLD_H), fill=(112, 76, 50))
    d.rectangle(v.box(0, 850, WORLD_W, 858), fill=(176, 128, 90))
    # mics on boom arms, one per host, offset so they never cover the mouth
    for i, hx in enumerate(HOST_X):
        sx = hx + (-150 if i == 0 else 150)
        d.line([v.p(sx + (-200 if i == 0 else 200), 840), v.p(sx, 610)], fill=(26, 26, 30), width=max(2, int(v.r(10))))
        d.ellipse(v.box(sx - 22, 570, sx + 22, 650), fill=(36, 36, 42))
        d.ellipse(v.box(sx - 28, 580, sx + 28, 640), outline=(70, 70, 76), width=max(1, int(v.r(3))))
    # mugs
    for i, hx in enumerate(HOST_X):
        mx = hx + (230 if i == 0 else -230)
        d.rectangle(v.box(mx - 24, 800, mx + 24, 852), fill=(236, 232, 224))
        d.arc(v.box(mx + 12, 812, mx + 46, 842), 270, 90, fill=(236, 232, 224), width=max(2, int(v.r(6))))


def render_frame(spec: dict[str, Any], camera: str, t: float, shot_t: float, shot_len: float, frame_idx: int,
                 env_path: str, names: list[str]) -> Image.Image:
    W, H = spec["width"] * SS, spec["height"] * SS
    cam = CAMERAS[camera]
    prog = shot_t / max(shot_len, 0.01)
    push = 0.05 * prog if camera == "wide" else 0.035 * prog
    v = View(W, H, cam, push=push, dx=_noise(t, 1.7) * (3 if camera == "wide" else 7), dy=_noise(t, 4.2) * (2 if camera == "wide" else 5))
    img = _background(W, H, cam).copy()
    d = ImageDraw.Draw(img)
    e0, e1 = _env(env_path, 0, ), _env(env_path, 1)
    i = min(frame_idx, len(e0) - 1)
    en = [float(e0[i]), float(e1[i])]
    # slow-moving energy for gestures/brows (average over the last ~0.5 s)
    lo = max(0, i - 12)
    slow = [float(np.mean(e0[lo:i + 1])), float(np.mean(e1[lo:i + 1]))]
    for idx in (0, 1):
        _person(d, v, idx, t, mouth=en[idx] * 0.95, energy=slow[idx], other_energy=slow[1 - idx])
    _foreground(d, v, names)
    return img.resize((spec["width"], spec["height"]), Image.LANCZOS)


def render_shot(args: dict[str, Any]) -> str:
    """Render one shot to an H.264 clip (no audio, no overlays). Runs inside a worker process."""
    spec, shot, out = args["spec"], args["shot"], Path(args["out"])
    fps = spec["fps"]
    n = max(1, round((shot["t1"] - shot["t0"]) * fps))
    part = out.with_suffix(".part.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{spec['width']}x{spec['height']}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
           "-g", str(fps * 2), "-an", str(part)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        first = round(shot["t0"] * fps)
        for k in range(n):
            if k % fps == 0 and args.get("job_id") and jobq.cancel_requested(args["job_id"]):
                raise Cancelled("cancelled during render")
            fi = first + k
            img = render_frame(spec, shot["camera"], fi / fps, k / fps, n / fps, fi, args["env"], args["names"])
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg shot encode failed: {err[-500:]}")
        os.replace(part, out)
        return str(out)
    except BaseException:
        proc.kill()
        part.unlink(missing_ok=True)
        raise


class DraftRenderer:
    id = "procedural-draft"
    paid = False
