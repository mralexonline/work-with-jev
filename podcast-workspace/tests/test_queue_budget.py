import time

import pytest

from app import budget, db, jobq
from app.security import redact, safe_join


def test_free_job_runs_without_approval():
    j = jobq.enqueue("podcast", None, {})
    assert j["status"] == "queued"
    c = jobq.claim("w1")
    assert c and c["id"] == j["id"] and c["attempts"] == 1
    assert jobq.claim("w2") is None  # leased, nobody else gets it


def test_paid_job_blocked_until_approved():
    j = jobq.enqueue("podcast", None, {}, paid=True, est_cost_usd=10)
    assert j["status"] == "awaiting_approval"
    assert jobq.claim("w1") is None
    jobq.approve(j["id"], 10)
    assert jobq.claim("w1")["id"] == j["id"]


def test_approval_rejected_over_caps(monkeypatch):
    j = jobq.enqueue("podcast", None, {}, paid=True)
    with pytest.raises(budget.BudgetExceeded):
        jobq.approve(j["id"], 999)           # above per-job cap
    assert jobq.get(j["id"])["status"] == "awaiting_approval"
    budget.record(None, "modal", "gpu_seconds", 1, "s", 70.0)  # near the monthly US$72 budget
    with pytest.raises(budget.BudgetExceeded):
        jobq.approve(j["id"], 10)


def test_reserved_money_counts_against_budget():
    a = jobq.enqueue("podcast", None, {}, paid=True)
    b = jobq.enqueue("podcast", None, {}, paid=True)
    jobq.approve(a["id"], 17)
    jobq.approve(b["id"], 17)
    c = jobq.enqueue("podcast", None, {}, paid=True)
    with pytest.raises(budget.BudgetExceeded):  # 34 + 17 +... fill to the 72 limit
        for _ in range(5):
            x = jobq.enqueue("podcast", None, {}, paid=True)
            jobq.approve(x["id"], 17)


def test_job_cap_enforced_during_run():
    j = jobq.enqueue("podcast", None, {}, paid=True)
    jobq.approve(j["id"], 5)
    jobq.claim("w")
    budget.record(j["id"], "modal", "gpu_seconds", 100, "s", 4.5)
    budget.enforce_job_cap(j["id"], 0.4)
    with pytest.raises(budget.BudgetExceeded):
        budget.enforce_job_cap(j["id"], 0.6)


def test_bounded_retries_then_fail():
    j = jobq.enqueue("podcast", None, {}, max_attempts=3)
    for i in range(3):
        c = jobq.claim("w")
        assert c and c["attempts"] == i + 1
        r = jobq.fail(j["id"], "boom")
        assert r == ("retry" if i < 2 else "failed")
    assert jobq.get(j["id"])["status"] == "failed"
    assert jobq.claim("w") is None


def test_non_retryable_fails_immediately():
    j = jobq.enqueue("podcast", None, {})
    jobq.claim("w")
    assert jobq.fail(j["id"], "bad input", retryable=False) == "failed"


def test_lease_expiry_requeues_crashed_worker(monkeypatch):
    j = jobq.enqueue("podcast", None, {})
    jobq.claim("w1")
    db.x("UPDATE jobs SET lease_expires=? WHERE id=?", (time.time() - 1, j["id"]))
    c = jobq.claim("w2")
    assert c and c["id"] == j["id"] and c["attempts"] == 2


def test_cancel_queued_and_running():
    a = jobq.enqueue("podcast", None, {})
    assert jobq.cancel(a["id"])["status"] == "cancelled"
    b = jobq.enqueue("podcast", None, {})
    jobq.claim("w")
    assert jobq.cancel(b["id"])["status"] == "running"
    assert jobq.cancel_requested(b["id"])
    jobq.mark_cancelled(b["id"])
    assert jobq.get(b["id"])["status"] == "cancelled"


def test_redaction(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "supersecretvalue12345")
    from app import config
    config.reset_settings_cache()
    t = redact("key=supersecretvalue12345 hf_abcdefghijklmnop1234 Authorization: Bearer abc.def-ghi12345 sk-ant-abcdefghijklmnop")
    assert "supersecretvalue12345" not in t and "hf_abcdefghijklmnop1234" not in t
    assert "abc.def-ghi12345" not in t and "sk-ant-abcdefghijklmnop" not in t
    jid = jobq.enqueue("x", None, {})["id"]
    jobq.log(jid, "token=hf_abcdefghijklmnop1234")
    assert "hf_abcdefghijklmnop1234" not in str(jobq.events(jid))


def test_safe_join(tmp_path):
    assert safe_join(tmp_path, "a/b.txt").parent.name == "a"
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(ValueError):
            safe_join(tmp_path, bad)


def test_estimate_marks_assumptions_and_switches_to_measured():
    e = budget.estimate(1200, {"lipsync": True, "image": True, "tts": True})
    assert any(l["basis"] == "ASSUMED" for l in e["lines"]) and e["high_usd"] > e["typical_usd"]
    budget.record(None, "modal", "gpu_seconds", 600, "s", 0.6, stage="lipsync", output_seconds=30)  # 20 gpu-s per s
    e2 = budget.estimate(1200, {"lipsync": True})
    assert e2["lines"][0]["basis"] == "measured median" and e2["lines"][0]["gpu_seconds"] == 24000.0
