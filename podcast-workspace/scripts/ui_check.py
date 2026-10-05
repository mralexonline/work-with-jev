#!/usr/bin/env python3
"""Drive the real browser UI (headless Chromium via Playwright) through the free 30 s flow and screenshot each tab.

pip install playwright  (browsers are provided by the environment; do NOT run `playwright install` here)
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright  # noqa: E402

import verify_e2e as v  # noqa: E402

SHOTS = v.ROOT / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def main():
    st = v.Stack(ENABLE_TERMINAL="1")
    st.start_server(); st.start_worker()
    errors = []
    try:
        with sync_playwright() as p:
            exe = os.environ.get("CHROMIUM_PATH") or None
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(st.base)
            pg.wait_for_selector("#token")
            pg.screenshot(path=SHOTS / "00-login.png")
            pg.fill("#token", "wrong"); pg.click("#login-form button"); pg.wait_for_selector("#login-err:not(:empty)")
            v.check("UI: wrong token is rejected", "wrong" in pg.inner_text("#login-err").lower() or "token" in pg.inner_text("#login-err").lower(), pg.inner_text("#login-err"))
            pg.fill("#token", v.TOKEN); pg.click("#login-form button"); pg.wait_for_selector("#app:not([hidden])")
            v.check("UI: owner sign-in works", True)
            errors.clear()  # the 401s above are the expected signed-out probes; only errors after sign-in count
            pg.fill("#chat-input", "Create a 30-second podcast with Matt and Chloe about neighbourhood events, three camera angles, captions, and “Durham & Clarington Community REAL TALK” at the bottom left.")
            pg.click("#chat-form button"); pg.wait_for_selector(".msg.assistant .card button.primary", timeout=20000)
            pg.screenshot(path=SHOTS / "01-plan.png")
            txt = pg.inner_text("#chat-log")
            v.check("UI: plan card shows hosts, group name, free cost, animatic warning", all(s in txt for s in ("Matt & Chloe", "Durham & Clarington Community REAL TALK", "US$0.00", "stylised")), "")
            pg.click(".msg.assistant .card button.primary")
            pg.wait_for_selector(".progress", timeout=20000); time.sleep(6)
            pg.screenshot(path=SHOTS / "02-progress.png")
            pg.wait_for_selector("video[src*='episode.mp4']", timeout=300000)
            time.sleep(1)
            pg.screenshot(path=SHOTS / "03-result.png", full_page=True)
            v.check("UI: finished job shows preview player, download links and measured facts", "Download MP4" in pg.inner_text("#chat-log") and "Measured:" in pg.inner_text("#chat-log"))
            res = pg.evaluate("""new Promise(r => { const e = document.querySelector('video'); const done = () => r(e.error ? 'error:' + e.error.code : e.duration);
                if (e.readyState > 0) return done(); e.onloadedmetadata = done; e.onerror = done; setTimeout(() => r('timeout'), 10000); })""")
            if isinstance(res, (int, float)):
                v.check("UI: browser decodes the video (metadata duration ~30 s)", 27 < res < 33, f"{res:.1f}s")
            else:
                # Playwright's open-source Chromium ships without the H.264/AAC decoders; Chrome, Safari, Edge and Firefox have them.
                print(f"SKIP UI: in-browser playback not testable here ({res}); open-source Chromium lacks H.264. Media is verified by ffprobe + HTTP Range in verify_e2e.py")
                r = pg.request.get(st.base + pg.get_attribute("video", "src"), headers={"Range": "bytes=0-99"})
                v.check("UI: the player's src is fetchable with the session cookie (HTTP 206)", r.status == 206, str(r.status))
            for tab, shot in (("models", "04-models"), ("budget", "05-budget"), ("jobs", "06-jobs"), ("projects", "07-projects"), ("terminal", "08-terminal")):
                pg.click(f'#tabs [data-tab="{tab}"]'); time.sleep(1.2); pg.screenshot(path=SHOTS / f"{shot}.png", full_page=True)
            tbl = pg.inner_text("#tab-models")
            v.check("UI: models table shows status + licence class badges", all(s in tbl for s in ("placeholder", "blocked", "non-commercial", "permissive")))
            v.check("UI: terminal tab connects", "Connected as owner" in pg.inner_text("#term-msg"))
            v.check("UI: no browser console/page errors", not errors, "; ".join(errors)[:300])
            b.close()
    finally:
        st.stop()
    bad = [r for r in v.results if not r[1]]
    print(f"{len(v.results) - len(bad)}/{len(v.results)} UI checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
