"""Runtime settings. Everything comes from environment variables (see .env.example).

Secrets live only in the process environment or the 0600 owner-token file. They are
never part of the repr, never returned by the API, and are scrubbed from logs by
app.security.redact.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    owner_token: str = field(repr=False)

    # Spending controls. The monthly cap is a hard gate inside this app; set the same
    # number as a Modal workspace spend limit so the provider enforces it independently.
    monthly_budget_cad: float = 100.0
    soft_alert_pct: float = 0.8
    per_job_cap_cad: float = 25.0
    usd_per_cad: float = 0.72  # UPDATE: the app cannot know today's exchange rate

    # Queue behaviour
    max_attempts: int = 3
    retry_base_s: float = 5.0
    retry_cap_s: float = 300.0
    lease_s: float = 60.0

    # Terminal (disabled unless explicitly enabled)
    enable_terminal: bool = False
    terminal_idle_s: int = 900
    allowed_origins: tuple[str, ...] = ()

    # Cloud GPU provider
    cloud_enabled: bool = False
    modal_app_name: str = "podcast-workspace"

    # LLM provider for script writing (adapter ids live in config/models.yaml)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = field(default="", repr=False)
    anthropic_api_key: str = field(default="", repr=False)
    hf_token: str = field(default="", repr=False)
    github_token: str = field(default="", repr=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def models_dir(self) -> Path:
        return Path(os.environ.get("MODELS_DIR", ROOT / "models"))

    @property
    def usd_budget(self) -> float:
        return self.monthly_budget_cad * self.usd_per_cad

    def secret_values(self) -> list[str]:
        vals = [self.owner_token, self.llm_api_key, self.anthropic_api_key, self.hf_token, self.github_token]
        for k, v in os.environ.items():
            if v and len(v) >= 8 and any(t in k.upper() for t in ("TOKEN", "KEY", "SECRET", "PASSWORD")):
                vals.append(v)
        return sorted({v for v in vals if v and len(v) >= 8}, key=len, reverse=True)


def _owner_token(data_dir: Path) -> str:
    env = os.environ.get("OWNER_TOKEN", "").strip()
    if env:
        return env
    f = data_dir / "owner_token"
    if f.exists():
        return f.read_text().strip()
    tok = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    origins = tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(_num("PORT", 8000)),
        owner_token=_owner_token(data_dir),
        monthly_budget_cad=_num("MONTHLY_BUDGET_CAD", 100),
        soft_alert_pct=_num("SOFT_ALERT_PCT", 0.8),
        per_job_cap_cad=_num("PER_JOB_CAP_CAD", 25),
        usd_per_cad=_num("USD_PER_CAD", 0.72),
        max_attempts=int(_num("MAX_ATTEMPTS", 3)),
        retry_base_s=_num("RETRY_BASE_S", 5),
        retry_cap_s=_num("RETRY_CAP_S", 300),
        lease_s=_num("LEASE_S", 60),
        enable_terminal=_bool("ENABLE_TERMINAL", False),
        terminal_idle_s=int(_num("TERMINAL_IDLE_S", 900)),
        allowed_origins=origins,
        cloud_enabled=_bool("CLOUD_ENABLED", False),
        modal_app_name=os.environ.get("MODAL_APP_NAME", "podcast-workspace"),
        llm_base_url=os.environ.get("LLM_BASE_URL", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        hf_token=os.environ.get("HF_TOKEN", ""),
        github_token=os.environ.get("GITHUB_TOKEN", ""),
    )


def reset_settings_cache() -> None:  # used by tests
    get_settings.cache_clear()
