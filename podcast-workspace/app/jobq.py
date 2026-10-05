"""Persistent job queue on SQLite: leases, bounded retries with backoff, cancel, approvals."""
from __future__ import annotations

from typing import Any

from . import db
from .budget import BudgetExceeded, check_can_approve
from .config import get_settings
from .security import redact

RUNNABLE = "queued"
TERMINAL = {"succeeded", "failed", "cancelled"}


def enqueue(kind: str, project_id: str | None, spec: dict[str, Any], *, paid: bool = False,
            est_cost_usd: float = 0.0, max_attempts: int | None = None) -> dict[str, Any]:
    s = get_settings()
    jid = db.new_id("job")
    status = "awaiting_approval" if paid else "queued"
    db.x(
        "INSERT INTO jobs(id,project_id,kind,status,spec_json,max_attempts,est_cost_usd,paid,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (jid, project_id, kind, status, db.dumps(spec), max_attempts or s.max_attempts,
         est_cost_usd, int(paid), db.now()),
    )
    log(jid, f"job created ({kind}), status={status}")
    return get(jid)  # type: ignore[return-value]


def get(job_id: str) -> dict[str, Any] | None:
    return db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))


def list_jobs(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if project_id:
        return db.q("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit))
    return db.q("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))


def approve(job_id: str, cost_cap_usd: float) -> dict[str, Any]:
    """Owner approval for a paid job. Raises BudgetExceeded without changing anything."""
    j = get(job_id)
    if not j:
        raise KeyError(job_id)
    if j["status"] != "awaiting_approval":
        raise ValueError(f"job is {j['status']}, not awaiting approval")
    check_can_approve(cost_cap_usd)
    with db.tx() as c:
        c.execute("UPDATE jobs SET status='queued',cost_cap_usd=?,run_after=0 WHERE id=? AND status='awaiting_approval'",
                  (cost_cap_usd, job_id))
    log(job_id, f"approved by owner with cap US${cost_cap_usd:.2f}")
    return get(job_id)  # type: ignore[return-value]


def backoff_s(attempt: int) -> float:
    s = get_settings()
    return min(s.retry_base_s * (2 ** max(attempt - 1, 0)), s.retry_cap_s)


def requeue_expired() -> int:
    """Crashed worker: lease expired while running. Counts as an attempt."""
    n = 0
    with db.tx() as c:
        rows = c.execute("SELECT id,attempts,max_attempts,cancel_requested FROM jobs "
                         "WHERE status='running' AND lease_expires<?", (db.now(),)).fetchall()
        for r in rows:
            if r["cancel_requested"]:
                c.execute("UPDATE jobs SET status='cancelled',finished_at=?,lease_owner=NULL WHERE id=?", (db.now(), r["id"]))
            elif r["attempts"] >= r["max_attempts"]:
                c.execute("UPDATE jobs SET status='failed',error='worker lease expired; attempts exhausted',finished_at=?,"
                          "lease_owner=NULL WHERE id=?", (db.now(), r["id"]))
            else:
                c.execute("UPDATE jobs SET status='queued',run_after=?,lease_owner=NULL WHERE id=?",
                          (db.now() + backoff_s(r["attempts"]), r["id"]))
            n += 1
    return n


def claim(worker_id: str, kinds: tuple[str, ...] | None = None) -> dict[str, Any] | None:
    requeue_expired()
    s = get_settings()
    with db.tx() as c:
        sql = "SELECT id FROM jobs WHERE status='queued' AND run_after<=?"
        args: list[Any] = [db.now()]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        row = c.execute(sql + " ORDER BY created_at LIMIT 1", args).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_owner=?,lease_expires=?,"
                  "started_at=COALESCE(started_at,?),error=NULL WHERE id=?",
                  (worker_id, db.now() + s.lease_s, db.now(), row["id"]))
    j = get(row["id"])
    log(row["id"], f"claimed by {worker_id} (attempt {j['attempts']}/{j['max_attempts']})")  # type: ignore[index]
    return j


def heartbeat(job_id: str, worker_id: str) -> bool:
    with db.tx() as c:
        cur = c.execute("UPDATE jobs SET lease_expires=? WHERE id=? AND lease_owner=? AND status='running'",
                        (db.now() + get_settings().lease_s, job_id, worker_id))
        return cur.rowcount == 1


def progress(job_id: str, fraction: float, stage: str | None = None) -> None:
    db.x("UPDATE jobs SET progress=?,stage=COALESCE(?,stage) WHERE id=?", (max(0.0, min(1.0, fraction)), stage, job_id))


def cancel_requested(job_id: str) -> bool:
    r = db.q1("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,))
    return bool(r and r["cancel_requested"])


def cancel(job_id: str) -> dict[str, Any]:
    with db.tx() as c:
        j = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not j:
            raise KeyError(job_id)
        if j["status"] in ("queued", "awaiting_approval"):
            c.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE id=?", (db.now(), job_id))
        elif j["status"] == "running":
            c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    log(job_id, "cancel requested by owner")
    return get(job_id)  # type: ignore[return-value]


def mark_cancelled(job_id: str) -> None:
    db.x("UPDATE jobs SET status='cancelled',finished_at=?,lease_owner=NULL WHERE id=?", (db.now(), job_id))
    log(job_id, "job cancelled; worker cleanup done")


def complete(job_id: str, result: dict[str, Any]) -> None:
    db.x("UPDATE jobs SET status='succeeded',progress=1,result_json=?,finished_at=?,lease_owner=NULL WHERE id=?",
         (db.dumps(result), db.now(), job_id))
    log(job_id, "job succeeded")


def fail(job_id: str, error: str, retryable: bool = True) -> str:
    """Returns 'retry' or 'failed'. Retries are bounded by max_attempts with exponential backoff."""
    error = redact(error)[:2000]
    j = get(job_id)
    if not j:
        return "failed"
    if retryable and j["attempts"] < j["max_attempts"]:
        wait = backoff_s(j["attempts"])
        db.x("UPDATE jobs SET status='queued',run_after=?,lease_owner=NULL,error=? WHERE id=?",
             (db.now() + wait, error, job_id))
        log(job_id, f"attempt {j['attempts']} failed: {error} - retrying in {wait:.0f}s", "warn")
        return "retry"
    db.x("UPDATE jobs SET status='failed',error=?,finished_at=?,lease_owner=NULL WHERE id=?", (error, db.now(), job_id))
    log(job_id, f"job failed: {error}", "error")
    return "failed"


def log(job_id: str, message: str, level: str = "info") -> None:
    db.x("INSERT INTO job_events(job_id,ts,level,message) VALUES(?,?,?,?)",
         (job_id, db.now(), level, redact(message)[:4000]))


def events(job_id: str, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    return db.q("SELECT id,ts,level,message FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
                (job_id, after_id, limit))


__all__ = ["BudgetExceeded"]
