"""Worker loop: claims jobs from the persistent queue and runs them.

Run one with `python -m app.worker`. Safe to run several; leases make claims exclusive, and a
crashed worker's job is re-queued automatically (bounded by max_attempts).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
import traceback

from . import budget, db, jobq
from .adapters.base import Cancelled, NotConfigured
from .config import get_settings
from .security import install_log_redaction, redact

log = logging.getLogger("app.worker")
NON_RETRYABLE = (NotConfigured, budget.BudgetExceeded, ValueError, KeyError)


def _beat(job_id: str, worker_id: str, stop: threading.Event) -> None:
    """Keep the lease alive while a long stage (e.g. a multi-minute render) runs."""
    every = max(get_settings().lease_s / 3, 1.0)
    while not stop.wait(every):
        jobq.heartbeat(job_id, worker_id)


def handle(job: dict, worker_id: str) -> None:
    jid = job["id"]
    stop = threading.Event()
    threading.Thread(target=_beat, args=(jid, worker_id, stop), daemon=True).start()
    try:
        if job["kind"] == "podcast":
            from .pipeline.runner import run_podcast
            result = run_podcast(job, worker_id)
            db.x("UPDATE projects SET status='ready',updated_at=? WHERE id=?", (db.now(), job["project_id"]))
        elif job["kind"] == "install":
            from .installer import run_install
            result = run_install(job, worker_id)
        else:
            raise ValueError(f"unknown job kind {job['kind']}")
        jobq.complete(jid, result)
    except Cancelled:
        jobq.mark_cancelled(jid)
        if job.get("project_id"):
            db.x("UPDATE projects SET status='cancelled',updated_at=? WHERE id=?", (db.now(), job["project_id"]))
    except NON_RETRYABLE as e:
        jobq.fail(jid, f"{type(e).__name__}: {e}", retryable=False)
    except Exception as e:  # noqa: BLE001 - everything else is retried, bounded
        log.error("job %s failed: %s", jid, redact(traceback.format_exc(limit=3)))
        jobq.fail(jid, f"{type(e).__name__}: {e}", retryable=True)
    finally:
        stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    install_log_redaction()
    db.init()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log.info("worker %s started; data dir %s", worker_id, get_settings().data_dir)
    while not stop:
        job = jobq.claim(worker_id)
        if not job:
            time.sleep(1.0)
            continue
        handle(job, worker_id)
    log.info("worker stopping")


if __name__ == "__main__":
    main()
