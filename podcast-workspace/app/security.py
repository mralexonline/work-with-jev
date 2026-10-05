"""Auth, secret redaction and path safety helpers."""
from __future__ import annotations

import hmac
import logging
import re
import time
from pathlib import Path

from .config import get_settings

_PATTERNS = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"\b(sk-ant-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"\b(hf_[A-Za-z0-9]{12,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(a[ks]-[A-Za-z0-9]{16,})"),  # Modal token id / secret shape
    re.compile(r"(?i)(authorization:\s*bearer\s+)([A-Za-z0-9._\-]{8,})"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)([^\s'\",]{6,})"),
]


def redact(text: str) -> str:
    """Remove known secret values and common token shapes from a string."""
    if not text:
        return text
    for v in get_settings().secret_values():
        text = text.replace(v, "[REDACTED]")
    for p in _PATTERNS:
        if p.groups == 2:
            text = p.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = p.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never break logging
            pass
        return True


def install_log_redaction() -> None:
    f = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "app"):
        logging.getLogger(name).addFilter(f)
    for h in logging.getLogger().handlers:
        h.addFilter(f)


def token_ok(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate.encode(), get_settings().owner_token.encode())


class LoginThrottle:
    """Tiny in-memory brake on token guessing (per client key)."""

    def __init__(self, max_fail: int = 8, window_s: int = 300):
        self.max_fail, self.window_s = max_fail, window_s
        self.fails: dict[str, list[float]] = {}

    def blocked(self, key: str) -> bool:
        now = time.time()
        self.fails[key] = [t for t in self.fails.get(key, []) if now - t < self.window_s]
        return len(self.fails[key]) >= self.max_fail

    def record_failure(self, key: str) -> None:
        self.fails.setdefault(key, []).append(time.time())


def safe_join(base: Path, rel: str) -> Path:
    """Resolve rel under base; raise ValueError if it escapes (../, absolute, symlink)."""
    base = base.resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise ValueError("path escapes base directory")
    return p


def origin_allowed(origin: str | None, host_header: str | None) -> bool:
    """Same-origin check for state-changing and WebSocket requests."""
    if not origin:
        return False
    allowed = get_settings().allowed_origins
    if origin in allowed:
        return True
    if host_header:
        return origin in (f"http://{host_header}", f"https://{host_header}")
    return False
