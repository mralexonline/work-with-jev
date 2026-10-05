"""SQLite persistence (WAL). One short-lived connection per call keeps threads simple."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, instruction TEXT NOT NULL,
  spec_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, project_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
  spec_json TEXT NOT NULL DEFAULT '{}', progress REAL NOT NULL DEFAULT 0, stage TEXT DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  run_after REAL NOT NULL DEFAULT 0, lease_owner TEXT, lease_expires REAL,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  est_cost_usd REAL NOT NULL DEFAULT 0, cost_cap_usd REAL NOT NULL DEFAULT 0,
  spent_usd REAL NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0,
  error TEXT, result_json TEXT,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status, run_after);
CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts REAL NOT NULL,
  level TEXT NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ev_job ON job_events(job_id, id);
CREATE TABLE IF NOT EXISTS checkpoints(
  project_id TEXT NOT NULL, key TEXT NOT NULL, stage TEXT NOT NULL, path TEXT,
  meta_json TEXT NOT NULL DEFAULT '{}', cost_usd REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL,
  PRIMARY KEY(project_id, key));
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, job_id TEXT, provider TEXT NOT NULL,
  resource TEXT NOT NULL, units REAL NOT NULL, unit TEXT NOT NULL, cost_usd REAL NOT NULL,
  stage TEXT, output_seconds REAL, measured INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS models_state(
  model_id TEXT PRIMARY KEY, status TEXT NOT NULL, detail TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, role TEXT NOT NULL, text TEXT NOT NULL,
  data_json TEXT, ts REAL NOT NULL);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(get_settings().db_path, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init() -> None:
    c = connect()
    try:
        c.executescript(SCHEMA)
    finally:
        c.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE so concurrent workers serialize on claim/update."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def q(sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
    c = connect()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


def q1(sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: tuple | list = ()) -> None:
    c = connect()
    try:
        c.execute(sql, args)
    finally:
        c.close()


def now() -> float:
    return time.time()


def dumps(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"), default=str)
