"""Service Level Objective (SLO) Metrics & Breach Evaluation Engine for PayPilot.

Calculates operational service levels (P50/P95/P99 latency, error rates, LLM fallback rates,
job completion rates) and detects deterministic SLO breaches with cooldown alert deduplication.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional

from backend.config import (
    get_slo_alert_cooldown_seconds,
    get_slo_analyze_p95_ms,
    get_slo_error_rate_percent,
    get_slo_job_success_percent,
)
from backend.observability.metrics import get_metrics_snapshot
from backend.observability.store import get_metrics_store

logger = logging.getLogger("paypilot.observability.slo")


@dataclass
class SLOBreachEvent:
    """Immutable event describing a detected Service Level Objective breach."""
    slo_name: str
    observed_value: float
    target_value: float
    unit: str
    status: str  # BREACHED, HEALTHY
    severity: str  # WARNING, CRITICAL
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SLOCooldownManager:
    """Maintains last alert trigger timestamps per SLO to prevent alert flooding."""

    def __init__(self) -> None:
        self._last_breach_times: Dict[str, float] = {}
        self._lock = threading.Lock()

    def should_emit_alert(self, slo_name: str, cooldown_seconds: Optional[float] = None) -> bool:
        """Determines if enough time has passed since the last alert for this SLO."""
        cooldown = cooldown_seconds if cooldown_seconds is not None else get_slo_alert_cooldown_seconds()
        now = time.monotonic()
        with self._lock:
            last_time = self._last_breach_times.get(slo_name, 0.0)
            if now - last_time >= cooldown:
                self._last_breach_times[slo_name] = now
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._last_breach_times.clear()


_GLOBAL_COOLDOWN_MANAGER = SLOCooldownManager()


def _percentile(values: List[float], p: float) -> float:
    """Calculates the p-th percentile (0-100) of a list of floats."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_vals[int(k)], 2)
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return round(d0 + d1, 2)


def calculate_slo_metrics() -> Dict[str, Any]:
    """Aggregates active metrics store snapshot into a structured SLO metrics report."""
    from backend.observability.metrics import get_metrics_snapshot
    snapshot = get_metrics_snapshot()

    req_data = snapshot.get("requests", {})
    total_reqs = req_data.get("total", 0)
    durations = req_data.get("durations_ms", [])
    status_counts = req_data.get("by_status", {})

    err_count = sum(cnt for st, cnt in status_counts.items() if str(st).startswith("4") or str(st).startswith("5"))
    err_rate_pct = round((err_count / total_reqs * 100.0), 2) if total_reqs > 0 else 0.0
    success_count = sum(cnt for st, cnt in status_counts.items() if str(st).startswith("2"))
    success_rate_pct = round((success_count / total_reqs * 100.0), 2) if total_reqs > 0 else 100.0

    p50_latency = _percentile(durations, 50.0)
    p95_latency = _percentile(durations, 95.0)
    p99_latency = _percentile(durations, 99.0)
    mean_latency = round(sum(durations) / len(durations), 2) if durations else 0.0

    llm_data = snapshot.get("llm", {})
    total_llm = llm_data.get("total_calls", 0)
    llm_fallbacks = llm_data.get("fallbacks", 0)
    llm_retries = llm_data.get("retries", 0)
    llm_fallback_rate_pct = round((llm_fallbacks / total_llm * 100.0), 2) if total_llm > 0 else 0.0

    agent_data = snapshot.get("agents", {})
    agent_latencies = {}
    for ag_name, ag_info in agent_data.items():
        if isinstance(ag_info, dict):
            agent_latencies[ag_name] = {
                "count": ag_info.get("executions", ag_info.get("count", 0)),
                "avg_duration_ms": ag_info.get("average_duration_ms", ag_info.get("avg_duration_ms", 0.0)),
            }

    # Job metrics
    job_data = snapshot.get("jobs", {})
    job_total = job_data.get("jobs_submitted", job_data.get("total_submitted", 0))
    job_completed = job_data.get("jobs_completed", job_data.get("completed", 0))
    job_failed = job_data.get("jobs_failed", job_data.get("failed", 0))
    job_success_rate_pct = (
        round((job_completed / job_total * 100.0), 2)
        if job_total > 0
        else 100.0
    )


    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "requests": {
            "total_requests": total_reqs,
            "successful_requests": success_count,
            "error_requests": err_count,
            "success_rate_pct": success_rate_pct,
            "error_rate_pct": err_rate_pct,
            "mean_latency_ms": mean_latency,
            "p50_latency_ms": p50_latency,
            "p95_latency_ms": p95_latency,
            "p99_latency_ms": p99_latency,
        },
        "llm": {
            "total_calls": total_llm,
            "retries": llm_retries,
            "fallbacks": llm_fallbacks,
            "fallback_rate_pct": llm_fallback_rate_pct,
        },
        "jobs": {
            "total_submitted": job_total,
            "completed": job_completed,
            "failed": job_failed,
            "success_rate_pct": job_success_rate_pct,
        },
        "agents": agent_latencies,
    }


def evaluate_slo_breaches(
    metrics: Optional[Dict[str, Any]] = None,
    cooldown_manager: Optional[SLOCooldownManager] = None,
) -> Dict[str, Any]:
    """Evaluates current operational metrics against configured SLO targets and detects breaches."""
    current_metrics = metrics or calculate_slo_metrics()
    mgr = cooldown_manager or _GLOBAL_COOLDOWN_MANAGER

    target_p95_ms = get_slo_analyze_p95_ms()
    target_error_rate_pct = get_slo_error_rate_percent()
    target_job_success_pct = get_slo_job_success_percent()

    req_metrics = current_metrics.get("requests", {})
    obs_p95 = req_metrics.get("p95_latency_ms", 0.0)
    obs_error_rate = req_metrics.get("error_rate_pct", 0.0)
    total_reqs = req_metrics.get("total_requests", 0)

    job_metrics = current_metrics.get("jobs", {})
    obs_job_success = job_metrics.get("success_rate_pct", 100.0)
    job_total = job_metrics.get("total_submitted", 0)

    llm_metrics = current_metrics.get("llm", {})
    obs_fallback_rate = llm_metrics.get("fallback_rate_pct", 0.0)

    evaluated_slos = []
    active_breaches = []
    new_alerts_emitted = []

    # 1. SLO: Analyze P95 Latency
    p95_breached = total_reqs > 0 and obs_p95 > target_p95_ms
    p95_event = SLOBreachEvent(
        slo_name="analyze_p95_latency",
        observed_value=obs_p95,
        target_value=target_p95_ms,
        unit="ms",
        status="BREACHED" if p95_breached else "HEALTHY",
        severity="WARNING" if obs_p95 < (target_p95_ms * 1.5) else "CRITICAL",
        details={"total_requests": total_reqs},
    )
    evaluated_slos.append(p95_event)
    if p95_breached:
        active_breaches.append(p95_event)
        if mgr.should_emit_alert("analyze_p95_latency"):
            new_alerts_emitted.append(p95_event)

    # 2. SLO: API Error Rate
    err_breached = total_reqs >= 5 and obs_error_rate > target_error_rate_pct
    err_event = SLOBreachEvent(
        slo_name="api_error_rate",
        observed_value=obs_error_rate,
        target_value=target_error_rate_pct,
        unit="percent",
        status="BREACHED" if err_breached else "HEALTHY",
        severity="CRITICAL" if obs_error_rate > (target_error_rate_pct * 3) else "WARNING",
        details={"total_requests": total_reqs, "error_count": req_metrics.get("error_requests", 0)},
    )
    evaluated_slos.append(err_event)
    if err_breached:
        active_breaches.append(err_event)
        if mgr.should_emit_alert("api_error_rate"):
            new_alerts_emitted.append(err_event)

    # 3. SLO: Background Job Success Rate
    job_breached = job_total >= 5 and obs_job_success < target_job_success_pct
    job_event = SLOBreachEvent(
        slo_name="job_success_rate",
        observed_value=obs_job_success,
        target_value=target_job_success_pct,
        unit="percent",
        status="BREACHED" if job_breached else "HEALTHY",
        severity="CRITICAL" if obs_job_success < 90.0 else "WARNING",
        details={"total_submitted": job_total, "failed_jobs": job_metrics.get("failed", 0)},
    )
    evaluated_slos.append(job_event)
    if job_breached:
        active_breaches.append(job_event)
        if mgr.should_emit_alert("job_success_rate"):
            new_alerts_emitted.append(job_event)

    # 4. SLO: LLM Fallback Rate
    llm_target_fallback_pct = 5.0  # target fallback <= 5%
    llm_breached = llm_metrics.get("total_calls", 0) >= 5 and obs_fallback_rate > llm_target_fallback_pct
    llm_event = SLOBreachEvent(
        slo_name="llm_fallback_rate",
        observed_value=obs_fallback_rate,
        target_value=llm_target_fallback_pct,
        unit="percent",
        status="BREACHED" if llm_breached else "HEALTHY",
        severity="WARNING",
        details={"total_calls": llm_metrics.get("total_calls", 0)},
    )
    evaluated_slos.append(llm_event)
    if llm_breached:
        active_breaches.append(llm_event)
        if mgr.should_emit_alert("llm_fallback_rate"):
            new_alerts_emitted.append(llm_event)

    all_healthy = len(active_breaches) == 0

    return {
        "overall_status": "HEALTHY" if all_healthy else "BREACHED",
        "total_slos_evaluated": len(evaluated_slos),
        "active_breaches_count": len(active_breaches),
        "new_alerts_emitted_count": len(new_alerts_emitted),
        "evaluated_slos": [e.to_dict() for e in evaluated_slos],
        "active_breaches": [e.to_dict() for e in active_breaches],
        "new_alerts_emitted": [e.to_dict() for e in new_alerts_emitted],
        "metrics_evaluated": current_metrics,
    }
