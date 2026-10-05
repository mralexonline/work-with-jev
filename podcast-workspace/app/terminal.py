"""Owner-only browser terminal over a WebSocket PTY.

This is a real shell on the host running the app, by design. Safeguards: disabled unless
ENABLE_TERMINAL=1, owner session + same-origin check, one session at a time, idle and absolute
timeouts, scrubbed environment (no API keys), process-group kill on disconnect. For stronger
isolation set TERMINAL_CMD to a command that enters a separate container/user (docs/SECURITY.md).
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import shlex
import signal
import struct
import termios
import time

from fastapi import WebSocket, WebSocketDisconnect

from .config import get_settings

_active = {"on": False}
MAX_SESSION_S = 4 * 3600


def _env(home: str) -> dict[str, str]:
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "TERM": "xterm-256color", "LANG": "C.UTF-8",
            "PS1": r"\u@workspace:\w\$ ", "HISTFILE": "/dev/null"}


async def serve(ws: WebSocket) -> None:
    s = get_settings()
    if _active["on"]:
        await ws.close(code=4409)
        return
    await ws.accept()
    _active["on"] = True
    home = s.data_dir / "terminal"
    home.mkdir(exist_ok=True)
    cmd = shlex.split(os.environ.get("TERMINAL_CMD", "/bin/bash --noprofile --norc -i"))
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(home)
        os.execve(cmd[0] if os.path.isabs(cmd[0]) else (__import__("shutil").which(cmd[0]) or cmd[0]), cmd, _env(str(home)))
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[bytes] = asyncio.Queue()
    loop.add_reader(fd, lambda: q.put_nowait(_read(fd)))
    start = last = time.time()

    async def pump_out():
        while True:
            data = await q.get()
            if not data:
                return
            await ws.send_bytes(data)

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
            except asyncio.TimeoutError:
                if time.time() - last > s.terminal_idle_s or time.time() - start > MAX_SESSION_S or out_task.done():
                    break
                continue
            if msg["type"] == "websocket.disconnect":
                break
            last = time.time()
            if msg.get("bytes"):
                os.write(fd, msg["bytes"])
            elif msg.get("text"):
                try:
                    j = json.loads(msg["text"])
                    if j.get("type") == "resize":
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(j["rows"]), int(j["cols"]), 0, 0))
                    elif j.get("type") == "input":
                        os.write(fd, j["data"].encode())
                except (ValueError, KeyError):
                    os.write(fd, msg["text"].encode())
    except WebSocketDisconnect:
        pass
    finally:
        _active["on"] = False
        try:
            loop.remove_reader(fd)
        except Exception:
            pass
        out_task.cancel()
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            os.waitpid(pid, 0)
            os.close(fd)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


def _read(fd: int) -> bytes:
    try:
        return os.read(fd, 65536)
    except OSError:
        return b""
