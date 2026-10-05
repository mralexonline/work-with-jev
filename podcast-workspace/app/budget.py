"""Spending controls: price list, usage ledger, estimates, hard caps.

Two independent caps should exist: this gate, and a spend limit set in the provider's
dashboard (Modal: Settings > Usage and Billing). This module cannot see charges that
the provider has not reported to us, so treat it as the first line, not the last.
"""
from __future__ import annotations

import datetime as dt
import statistics
from functools import lru_cache
from typing import Any

import yaml

from . import db
from .config import ROOT, get_settings


class BudgetExceeded(RuntimeError):
    """Raised before money is spent; never retried."""


@lru_cache(maxsize=1)
def pricing() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text())


def gpu_rate(gpu: str) -> float:
    return float(pricing()["modal"]["gpu_usd_per_second"][gpu])


def month_start_ts(now: float | None = None) -> float:
    d = dt.datetime.fromtimestamp(now or db.now(), dt.timezone.utc)
    return dt.datetime(d.year, d.month, 1, tzinfo=dt.timezone.utc).timestamp()


def month_spend_usd() -> float:
    r = db.q1("SELECT COALESCE(SUM(cost_usd),0) s FROM usage WHERE ts>=?", (month_start_ts(),))
    return float(r["s"]) if r else 0.0


def reserved_usd() -> float:
    """Unspent cap of jobs that are approved and still able to spend."""
    r = db.q1(
        "SELECT COALESCE(SUM(MAX(cost_cap_usd-spent_usd,0)),0) s FROM jobs "
        "WHERE paid=1 AND status IN ('queued','running')"
    )
    return float(r["s"]) if r else 0.0


def check_can_approve(cap_usd: float) -> None:
    s = get_settings()
    if cap_usd <= 0:
        raise BudgetExceeded("approval cap must be greater than zero")
    if cap_usd > s.per_job_cap_cad * s.usd_per_cad + 1e-9:
        raise BudgetExceeded(
            f"cap US${cap_usd:.2f} exceeds the per-job limit of C${s.per_job_cap_cad:.2f} "
            f"(US${s.per_job_cap_cad * s.usd_per_cad:.2f}); raise PER_JOB_CAP_CAD deliberately if intended"
        )
    projected = month_spend_usd() + reserved_usd() + cap_usd
    if projected > s.usd_budget + 1e-9:
        raise BudgetExceeded(
            f"would project US${projected:.2f} this month against a US${s.usd_budget:.2f} "
            f"(C${s.monthly_budget_cad:.2f}) budget"
        )


def record(job_id: str | None, provider: str, resource: str, units: float, unit: str,
           cost_usd: float, stage: str | None = None, output_seconds: float | None = None,
           measured: bool = True) -> None:
    db.x(
        "INSERT INTO usage(ts,job_id,provider,resource,units,unit,cost_usd,stage,output_seconds,measured)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (db.now(), job_id, provider, resource, units, unit, cost_usd, stage, output_seconds, int(measured)),
    )
    if job_id and cost_usd:
        db.x("UPDATE jobs SET spent_usd=spent_usd+? WHERE id=?", (cost_usd, job_id))


def enforce_job_cap(job_id: str, extra_usd: float = 0.0) -> None:
    j = db.q1("SELECT paid,cost_cap_usd,spent_usd FROM jobs WHERE id=?", (job_id,))
    if not j or not j["paid"]:
        return
    if j["spent_usd"] + extra_usd > j["cost_cap_usd"] + 1e-9:
        raise BudgetExceeded(
            f"job cap reached: spent US${j['spent_usd']:.2f} + next US${extra_usd:.2f} > cap US${j['cost_cap_usd']:.2f}"
        )
    if month_spend_usd() + extra_usd > get_settings().usd_budget + 1e-9:
        raise BudgetExceeded("monthly budget reached")


def measured_ratio(stage: str) -> float | None:
    """Median measured GPU-seconds per output-second for a stage, if any real runs exist."""
    rows = db.q(
        "SELECT units/output_seconds r FROM usage WHERE stage=? AND resource='gpu_seconds' "
        "AND measured=1 AND output_seconds>0 ORDER BY id DESC LIMIT 20", (stage,))
    vals = [r["r"] for r in rows]
    return statistics.median(vals) if vals else None


def estimate(duration_s: float, uses_cloud: dict[str, bool]) -> dict[str, Any]:
    """Cost estimate (USD) for an episode. uses_cloud maps stage -> bool. Free stages cost 0."""
    p = pricing()
    a = p["assumed_throughput"]
    lines: list[dict[str, Any]] = []

    def add(stage: str, gpu: str, gpu_s: float, basis: str):
        lines.append({"stage": stage, "gpu": gpu, "gpu_seconds": round(gpu_s, 1),
                      "usd": round(gpu_s * gpu_rate(gpu), 2), "basis": basis})

    if uses_cloud.get("tts"):
        m = measured_ratio("tts")
        add("tts", a["tts"]["gpu"], duration_s * (m or a["tts"]["gpu_s_per_output_s"]),
            "measured median" if m else "ASSUMED")
    if uses_cloud.get("image"):
        n = a["image"]["items_per_episode"]
        add("image", a["image"]["gpu"], n * a["image"]["gpu_s_per_item"], "ASSUMED")
    if uses_cloud.get("lipsync"):
        m = measured_ratio("lipsync")
        add("lipsync", a["lipsync"]["gpu"], duration_s * (m or a["lipsync"]["gpu_s_per_output_s"]),
            "measured median" if m else "ASSUMED")
    typical = sum(l["usd"] for l in lines)
    high = typical * float(p["uncertainty_high_factor"])
    s = get_settings()
    return {"lines": lines, "typical_usd": round(typical, 2), "high_usd": round(high, 2),
            "typical_cad": round(typical / s.usd_per_cad, 2), "high_cad": round(high / s.usd_per_cad, 2),
            "all_measured": bool(lines) and all(l["basis"] == "measured median" for l in lines)}


def summary() -> dict[str, Any]:
    s = get_settings()
    spend, reserved = month_spend_usd(), reserved_usd()
    by = db.q("SELECT provider,resource,ROUND(SUM(cost_usd),4) usd,ROUND(SUM(units),1) units,unit FROM usage "
              "WHERE ts>=? GROUP BY provider,resource,unit", (month_start_ts(),))
    return {
        "monthly_budget_cad": s.monthly_budget_cad, "monthly_budget_usd": round(s.usd_budget, 2),
        "usd_per_cad": s.usd_per_cad, "per_job_cap_cad": s.per_job_cap_cad,
        "spent_usd": round(spend, 4), "spent_cad": round(spend / s.usd_per_cad, 2),
        "reserved_usd": round(reserved, 4), "remaining_usd": round(s.usd_budget - spend - reserved, 2),
        "soft_alert": (spend + reserved) >= s.soft_alert_pct * s.usd_budget,
        "breakdown": by,
    }
