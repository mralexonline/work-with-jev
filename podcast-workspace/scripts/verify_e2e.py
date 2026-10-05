#!/usr/bin/env python3
"""Real end-to-end verification against REAL processes (server + worker), no mocks.

Needs: ffmpeg/ffprobe, the local Kokoro files (python scripts/fetch_models.py). Costs: US$0.
Writes a transcript to docs/VERIFICATION.md. Exits non-zero if any check fails.
"""
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TOKEN = "e2e-" + os.urandom(12).hex()
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    def __init__(self, **env):
        self.data = tempfile.mkdtemp(prefix="pw-e2e-")
        self.port = free_port()
        self.env = {**os.environ, "DATA_DIR": self.data, "OWNER_TOKEN": TOKEN, "PORT": str(self.port), "PYTHONPATH": str(ROOT),
                    "RETRY_BASE_S": "1", "LEASE_S": "6", **{k: str(v) for k, v in env.items()}}
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = self.worker = None
        self.log = open(Path(self.data) / "stack.log", "wb")

    def start_server(self):
        self.server = subprocess.Popen([PY, "-m", "app.main"], cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(60):
            try:
                if httpx.get(self.base + "/api/health", timeout=1).status_code == 200:
                    return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("server did not start")

    def start_worker(self):
        self.worker = subprocess.Popen([PY, "-m", "app.worker"], cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)

    def kill_worker(self, sig=signal.SIGKILL):
        if self.worker:
            self.worker.send_signal(sig)
            self.worker.wait()
            self.worker = None

    def stop(self):
        for p in (self.worker, self.server):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()

    def db(self):
        c = sqlite3.connect(Path(self.data) / "app.db")
        c.row_factory = sqlite3.Row
        return c


def session(stack: Stack) -> httpx.Client:
    c = httpx.Client(base_url=stack.base, timeout=30, headers={"Origin": stack.base})
    r = c.post("/api/login", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return c


def wait_job(c: httpx.Client, jid: str, timeout=600, until=("succeeded", "failed", "cancelled")):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in until:
            return j
        time.sleep(1)
    raise TimeoutError(jid)


def ffprobe(path: Path) -> dict:
    return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))


def frame_stats(video: Path, t: float):
    """Return (watermark_dark_frac, watermark_bright_frac, caption_bright_px) from the real decoded frame."""
    import numpy as np
    from PIL import Image
    png = Path(tempfile.mkdtemp()) / "f.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", str(png)], check=True)
    im = np.asarray(Image.open(png).convert("L"), dtype=np.float32)
    h, w = im.shape
    wm = im[int(h * 0.88):int(h * 0.975), int(w * 0.02):int(w * 0.36)]
    cap = im[int(h * 0.70):int(h * 0.86), int(w * 0.15):int(w * 0.85)]
    return float((wm < 70).mean()), float((wm > 220).mean()), int((cap > 248).sum())  # caption fill is pure white; props like mugs are <240


def scenario_full_30s():
    print("\n=== A. full 30-second episode through the real server + worker ===")
    st = Stack()
    st.start_server(); st.start_worker()
    try:
        c = session(st)
        brief = ("Create a 30-second realistic podcast with Matt and Chloe about neighbourhood events, natural voices, lip-sync, "
                 "body movement, three camera angles, captions, and “Durham & Clarington Community REAL TALK” at the bottom left.")
        r = c.post("/api/chat", json={"text": brief}).json()
        prop = r["proposal"]
        check("chat turns the brief into a project plan (nothing started, nothing billed)", prop["project"]["status"] == "draft" and prop["estimate"]["typical_usd"] == 0)
        check("plan warns that the free renderer is a stylised animatic", any("animatic" in n for n in prop["notes"] + prop["preflight"]["warnings"]))
        pid = prop["project"]["id"]
        t0 = time.time()
        s = c.post(f"/api/projects/{pid}/start", json={})
        check("free job queued without approval", s.status_code == 200 and not s.json()["needs_approval"], s.text[:120])
        j = wait_job(c, s.json()["job"]["id"])
        took = time.time() - t0
        check("job succeeded", j["status"] == "succeeded", f"{j['status']} {j['error']} in {took:.0f}s")
        if j["status"] != "succeeded":
            return None
        mp4 = Path(tempfile.mkdtemp()) / "episode.mp4"
        r = c.get(f"/api/projects/{pid}/files/episode.mp4?download=true")
        mp4.write_bytes(r.content)
        check("MP4 downloads with attachment disposition", r.status_code == 200 and "attachment" in r.headers.get("content-disposition", ""), f"{len(r.content)/1e6:.2f} MB")
        info = ffprobe(mp4)
        v = next(x for x in info["streams"] if x["codec_type"] == "video"); a = next(x for x in info["streams"] if x["codec_type"] == "audio")
        dur = float(info["format"]["duration"])
        check("ffprobe: H.264 1280x720 + AAC, ~30 s", v["codec_name"] == "h264" and (v["width"], v["height"]) == (1280, 720) and a["codec_name"] == "aac" and 27 <= dur <= 33, f"{dur:.2f}s")
        rr = c.get(f"/api/projects/{pid}/files/episode.mp4", headers={"Range": "bytes=0-99"})
        check("video preview supports HTTP Range (seekable)", rr.status_code == 206, str(rr.status_code))
        tl = json.loads(c.get(f"/api/projects/{pid}/files/timeline.json").text)
        cams = {s["camera"] for s in tl["shots"]}
        check("three camera angles are used", cams == {"wide", "close_0", "close_1"}, str(sorted(cams)))
        # watermark present throughout: sample 10 frames
        marks = []
        for k in range(10):
            t = 0.5 + k * (dur - 1.0) / 9
            dark, bright, _ = frame_stats(mp4, t)
            marks.append(dark > 0.35 and bright > 0.02)
        check("bottom-left group name box present in 10/10 sampled frames", all(marks), str(marks))
        # captions: a frame mid-speech has bright caption pixels; the final tail frame (after speech) does not
        lines = tl["lines"]; mid = (lines[2]["start"] + lines[2]["end"]) / 2
        _, _, cap_mid = frame_stats(mp4, mid)
        _, _, cap_end = frame_stats(mp4, dur - 0.3)
        check("captions appear during speech and not after it", cap_mid > 400 and cap_end < 30, f"mid={cap_mid}px end={cap_end}px")
        # audio really contains speech: loudness measurement
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp4), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
        lufs = float(out.split("I:")[-1].split("LUFS")[0])
        check("audio is normalised speech (~ -16 LUFS)", -19 < lufs < -13, f"{lufs} LUFS")
        # sync check: mouth energy envelope vs audio energy, from the saved artefacts
        import numpy as np
        wd = Path(st.data) / "projects" / pid
        env = np.load(wd / "envelopes.npz")
        speaking = sum(1 for l in lines for _ in [0])
        for i, name in enumerate(tl["hosts"]):
            e = env[f"h{i}"]
            mine = [l for l in lines if l["speaker"] == name]
            inside = np.mean([e[int(l["start"] * 24):int(l["end"] * 24)].mean() for l in mine])
            outside_mask = np.ones(len(e), bool)
            for l in mine:
                outside_mask[int(l["start"] * 24):int(l["end"] * 24) + 1] = False
            outside = e[outside_mask].mean() if outside_mask.any() else 0
            check(f"{name}: mouth energy is high only while {name} speaks", inside > 0.3 and outside < 0.02, f"inside={inside:.2f} outside={outside:.3f}")
        # saved result is queryable after "browser closed": new session, same data
        c2 = session(st)
        again = c2.get(f"/api/projects/{pid}").json()
        check("project/job state survives a new browser session", again["project"]["has_video"] and any(j["status"] == "succeeded" for j in again["project"]["jobs"]))
        # secrets never present in API output or the log
        blob = json.dumps(again) + c2.get(f"/api/jobs/{j['id']}/events").text + Path(st.data, "stack.log").read_text(errors="ignore")
        check("owner token appears nowhere in API output or logs", TOKEN not in blob)
        shutil_copy = ROOT / "docs" / "sample_30s_draft.mp4"
        shutil_copy.write_bytes(mp4.read_bytes())
        return mp4
    finally:
        st.stop()


def scenario_cancel():
    print("\n=== B. cancellation mid-render + worker cleanup ===")
    st = Stack(); st.start_server(); st.start_worker()
    try:
        c = session(st)
        pid = c.post("/api/projects", json={"instruction": "a 60 second podcast about parks"}).json()["project"]["id"]
        jid = c.post(f"/api/projects/{pid}/start", json={}).json()["job"]["id"]
        j = None
        for _ in range(240):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["stage"] == "render" and j["progress"] > 0.3:
                break
            time.sleep(0.5)
        check("job reached the render stage", j["stage"] == "render", j["stage"])
        c.post(f"/api/jobs/{jid}/cancel")
        j = wait_job(c, jid, 60)
        check("job ends as cancelled", j["status"] == "cancelled", j["status"])
        time.sleep(1)
        left = subprocess.run(["pgrep", "-f", f"ffmpeg.*{st.data}"], capture_output=True, text=True).stdout.split()
        parts = list(Path(st.data).rglob("*.part*"))
        check("no stray ffmpeg processes or .part files after cancel", not left and not parts, f"procs={left} parts={parts}")
        ev = c.get(f"/api/jobs/{jid}/events").json()
        check("cancel is logged", any("cancel" in e["message"] for e in ev))
    finally:
        st.stop()


def scenario_resume():
    print("\n=== C. worker crash (kill -9) mid-render, then resume from checkpoints ===")
    st = Stack(); st.start_server(); st.start_worker()
    try:
        c = session(st)
        pid = c.post("/api/projects", json={"instruction": "a 60 second podcast about parks"}).json()["project"]["id"]
        jid = c.post(f"/api/projects/{pid}/start", json={}).json()["job"]["id"]
        for _ in range(400):
            n = st.db().execute("SELECT COUNT(*) FROM checkpoints WHERE stage='render'").fetchone()[0]
            if n >= 1:
                break
            time.sleep(0.25)
        check("at least one shot checkpoint exists before the crash", n >= 1, f"{n} shot(s)")
        st.kill_worker(signal.SIGKILL)
        time.sleep(1)
        # kill -9 on the worker leaves orphaned render children; they exit when their pipe closes
        st.start_worker()
        j = wait_job(c, jid, 600)
        check("job completes after the crash (lease expired -> re-queued -> resumed)", j["status"] == "succeeded", f"{j['status']} attempts={j['attempts']} {j['error']}")
        ev = " ".join(e["message"] for e in c.get(f"/api/jobs/{jid}/events").json())
        check("second attempt reused cached work", "attempt 2" in ev and "cached" in ev and "0 shot(s) cached" not in ev.split("attempt 2")[-1], ev[-300:])
    finally:
        st.stop()


def scenario_terminal():
    print("\n=== D. terminal is owner-only and has a scrubbed environment ===")
    import asyncio
    import websockets
    st = Stack(ENABLE_TERMINAL="1", SECRET_API_KEY="shh-should-not-appear-0123456789")
    st.start_server()
    try:
        url = f"ws://127.0.0.1:{st.port}/api/terminal"

        async def attempt(headers):
            try:
                async with websockets.connect(url, additional_headers=headers, open_timeout=5) as ws:
                    await asyncio.sleep(0.3)
                    await ws.recv()
                    return "connected"
            except Exception as e:  # noqa: BLE001
                return type(e).__name__

        r_noauth = asyncio.run(attempt({"Origin": st.base}))
        check("terminal rejects unauthenticated websocket", r_noauth != "connected", r_noauth)
        c = session(st)
        cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        r_origin = asyncio.run(attempt({"Cookie": cookie, "Origin": "http://evil.example"}))
        check("terminal rejects cross-origin websocket even with a valid cookie", r_origin != "connected", r_origin)

        async def run():
            async with websockets.connect(url, additional_headers={"Cookie": cookie, "Origin": st.base}) as ws:
                await ws.send(json.dumps({"type": "resize", "rows": 24, "cols": 100}))
                await asyncio.sleep(0.5)
                await ws.send(b"echo ANS=$((6*7)); env | grep -c -E 'OWNER_TOKEN|SECRET_API_KEY'; echo DONE\n")
                buf = b""
                t0 = time.time()
                while b"DONE\r\n" not in buf.replace(b"echo DONE", b"") and time.time() - t0 < 8:
                    try:
                        buf += await asyncio.wait_for(ws.recv(), 1)
                    except asyncio.TimeoutError:
                        pass
                return buf.decode(errors="ignore")
        out = asyncio.run(run())
        check("owner gets a working shell (6*7=42)", "ANS=42" in out, out[-120:].replace("\r\n", " | "))
        check("shell environment contains no OWNER_TOKEN / secret keys", "\r\n0\r\n" in out, out[-120:].replace("\r\n", " | "))
    finally:
        st.stop()


def main():
    (ROOT / "docs").mkdir(exist_ok=True)
    scenario_full_30s(); scenario_cancel(); scenario_resume(); scenario_terminal()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    body = "\n".join(f"- {'PASS' if ok else '**FAIL**'} {n}" + (f" — `{d}`" if d else "") for n, ok, d in results)
    (ROOT / "docs" / "VERIFICATION.md").write_text(
        f"# Verification transcript\n\nGenerated by `python scripts/verify_e2e.py` on {stamp}. Real server + worker processes, real Kokoro voices, "
        f"real ffmpeg; no mocks, no paid services, US$0.\n\n{body}\n\n**{len(results) - len(failed)}/{len(results)} checks passed.**\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
