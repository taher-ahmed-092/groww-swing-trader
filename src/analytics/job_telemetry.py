"""
Job telemetry — records {job_name, started, duration_s, ok, error} for every
scheduled job run to data/cache/job_telemetry.json (last 500), so failures and
slow jobs are visible without grepping console logs.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TELEMETRY_FILE = Path("data/cache/job_telemetry.json")
_MAX_RECORDS = 500


def _load() -> list[dict]:
    if TELEMETRY_FILE.exists():
        try:
            return json.loads(TELEMETRY_FILE.read_text())
        except Exception:
            pass
    return []


def _save(records: list[dict]) -> None:
    try:
        TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        TELEMETRY_FILE.write_text(json.dumps(records[-_MAX_RECORDS:]))
    except OSError:
        pass


def record_run(job_name: str, started: datetime, duration_s: float, ok: bool, error: str | None) -> None:
    records = _load()
    records.append({
        "job_name": job_name,
        "started": started.isoformat(),
        "duration_s": round(duration_s, 2),
        "ok": ok,
        "error": error,
    })
    _save(records)


def track_job(func, job_name: str):
    """Wraps a scheduled job so every run — success or exception — records
    telemetry. Re-raises the original exception so APScheduler's own error
    handling/logging still fires."""
    def wrapped(*args, **kwargs):
        started = datetime.now(IST)
        start_perf = started.timestamp()
        ok, error = True, None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            ok = False
            error = f"{exc}\n{traceback.format_exc()[-500:]}"
            raise
        finally:
            duration = datetime.now(IST).timestamp() - start_perf
            record_run(job_name, started, duration, ok, error)
    wrapped.__name__ = getattr(func, "__name__", job_name)
    return wrapped


def get_job_stats(n_runs: int = 20) -> dict:
    """Per-job {last_run, avg_duration_s, failure_rate} over the last n_runs
    of each job."""
    records = _load()
    by_job: dict[str, list[dict]] = {}
    for r in records:
        by_job.setdefault(r["job_name"], []).append(r)

    stats: dict[str, dict] = {}
    for job_name, runs in by_job.items():
        recent = runs[-n_runs:]
        n = len(recent)
        failures = sum(1 for r in recent if not r.get("ok"))
        avg_duration = round(sum(r.get("duration_s", 0) for r in recent) / n, 2) if n else 0.0
        stats[job_name] = {
            "last_run": runs[-1]["started"] if runs else None,
            "last_ok": runs[-1]["ok"] if runs else None,
            "avg_duration_s": avg_duration,
            "failure_rate": round(failures / n, 3) if n else 0.0,
            "n_runs": n,
        }
    return stats


def get_unhealthy_jobs(failure_rate_threshold: float = 0.3, duration_threshold_s: float = 120.0) -> list[dict]:
    """Jobs whose recent failure rate or avg duration exceeds a threshold —
    surfaced by the health check."""
    stats = get_job_stats()
    unhealthy = []
    for job_name, s in stats.items():
        if s["failure_rate"] > failure_rate_threshold or s["avg_duration_s"] > duration_threshold_s:
            unhealthy.append({"job_name": job_name, **s})
    return unhealthy
