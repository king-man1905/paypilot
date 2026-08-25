"""Thread-Safe In-Memory Observability & Metrics Registry for PayPilot.

Tracks request lifecycles, specialist agent performance, LLM provider reliability,
and categorized error events without storing sensitive query content or credentials.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

KNOWN_AGENTS = (
    "revenue_agent",
    "payment_agent",
    "checkout_agent",
    "customer_agent",
    "recovery_agent",
)

KNOWN_ERROR_CATEGORIES = (
    "validation_error",
    "timeout",
    "provider_error",
    "routing_error",
    "analytics_error",
    "persistence_error",
    "auth_error",
    "forbidden_error",
    "internal_error",
    "rate_limit_exceeded",
    "quota_exceeded",
    "concurrency_limit_exceeded",
    "queue_full",
    "idempotency_conflict",
    "overload_rejected",
)


class MetricsRegistry:
    """Thread-safe in-memory metrics registry for PayPilot telemetry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self.reset()

    def reset(self) -> None:
        """Resets all metric counters to initial state (used for testing/isolation)."""
        with self._lock:
            self._start_time = time.time()

            # Request counters
            self.total_requests = 0
            self.successful_requests = 0
            self.failed_requests = 0
            self.total_request_duration_ms = 0.0
            self.requests_by_endpoint: Dict[str, int] = {}
            self.requests_by_status: Dict[str, int] = {}
            self.requests_by_intent: Dict[str, int] = {}

            # Traffic & Quota counters (Phase 21)
            self.rate_limit_rejections = 0
            self.quota_rejections = 0
            self.concurrency_rejections = 0
            self.queue_full_rejections = 0
            self.idempotency_replays = 0
            self.idempotency_conflicts = 0
            self.overload_rejections = 0

            # Agent counters
            self.agent_metrics: Dict[str, Dict[str, Any]] = {}
            for agent in KNOWN_AGENTS:
                self.agent_metrics[agent] = {
                    "executions": 0,
                    "failures": 0,
                    "total_duration_ms": 0.0,
                }

            # LLM counters
            self.llm_total_calls = 0
            self.llm_successful_calls = 0
            self.llm_failed_calls = 0
            self.llm_timeouts = 0
            self.llm_fallbacks = 0
            self.llm_total_latency_ms = 0.0

            # Error taxonomy counters
            self.errors_total = 0
            self.errors_by_category: Dict[str, int] = {
                cat: 0 for cat in KNOWN_ERROR_CATEGORIES
            }

    def record_request(
        self,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        intent: Optional[str] = None,
    ) -> None:
        """Records an HTTP request outcome and lifecycle timing."""
        with self._lock:
            self.total_requests += 1
            self.total_request_duration_ms += duration_ms

            # 2xx / 3xx are considered successful
            if 200 <= status_code < 400:
                self.successful_requests += 1
            else:
                self.failed_requests += 1

            # Endpoint breakdown
            norm_ep = endpoint.split("?")[0]
            self.requests_by_endpoint[norm_ep] = (
                self.requests_by_endpoint.get(norm_ep, 0) + 1
            )

            # Status code breakdown
            status_str = str(status_code)
            self.requests_by_status[status_str] = (
                self.requests_by_status.get(status_str, 0) + 1
            )

            # Intent breakdown (if present)
            if intent:
                norm_intent = str(intent).strip().lower()
                self.requests_by_intent[norm_intent] = (
                    self.requests_by_intent.get(norm_intent, 0) + 1
                )

    def record_agent_execution(
        self,
        agent_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """Records a specialist agent execution cycle."""
        with self._lock:
            if agent_name not in self.agent_metrics:
                self.agent_metrics[agent_name] = {
                    "executions": 0,
                    "failures": 0,
                    "total_duration_ms": 0.0,
                }

            metrics = self.agent_metrics[agent_name]
            metrics["executions"] += 1
            metrics["total_duration_ms"] += duration_ms
            if not success:
                metrics["failures"] += 1

    def record_llm_call(
        self,
        duration_ms: float,
        success: bool = True,
        is_timeout: bool = False,
        is_fallback: bool = False,
    ) -> None:
        """Records an LLM invocation attempt, duration, and fallback status."""
        with self._lock:
            self.llm_total_calls += 1
            self.llm_total_latency_ms += duration_ms

            if success:
                self.llm_successful_calls += 1
            else:
                self.llm_failed_calls += 1

            if is_timeout:
                self.llm_timeouts += 1

            if is_fallback:
                self.llm_fallbacks += 1

    def record_error(self, category: str) -> None:
        """Records an error event mapped to the standard error taxonomy."""
        norm_cat = category.strip().lower()
        with self._lock:
            self.errors_total += 1
            if norm_cat in self.errors_by_category:
                self.errors_by_category[norm_cat] += 1
            else:
                self.errors_by_category["internal_error"] += 1

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns a consolidated, JSON-serializable telemetry snapshot."""
        with self._lock:
            # Calculate averages safely
            avg_req_dur = (
                round(self.total_request_duration_ms / self.total_requests, 2)
                if self.total_requests > 0
                else 0.0
            )

            # Format agent stats
            formatted_agents: Dict[str, Dict[str, Any]] = {}
            for agent, data in self.agent_metrics.items():
                exec_count = data["executions"]
                avg_dur = (
                    round(data["total_duration_ms"] / exec_count, 2)
                    if exec_count > 0
                    else 0.0
                )
                formatted_agents[agent] = {
                    "executions": exec_count,
                    "failures": data["failures"],
                    "total_duration_ms": round(data["total_duration_ms"], 2),
                    "average_duration_ms": avg_dur,
                }

            # Format LLM stats
            avg_llm_lat = (
                round(self.llm_total_latency_ms / self.llm_total_calls, 2)
                if self.llm_total_calls > 0
                else 0.0
            )

            from backend.agents.llm_factory import get_llm_info
            llm_info = get_llm_info()

            return {
                "requests": {
                    "total": self.total_requests,
                    "successful": self.successful_requests,
                    "failed": self.failed_requests,
                    "total_duration_ms": round(self.total_request_duration_ms, 2),
                    "average_duration_ms": avg_req_dur,
                    "by_endpoint": dict(self.requests_by_endpoint),
                    "by_status": dict(self.requests_by_status),
                    "by_intent": dict(self.requests_by_intent),
                },
                "agents": formatted_agents,
                "llm": {
                    "provider": llm_info.get("active_provider", "nvidia"),
                    "configured_provider": llm_info.get("configured_provider", "nvidia"),
                    "model": llm_info.get("active_model", "meta/llama-3.3-70b-instruct"),
                    "is_live_llm": llm_info.get("is_live_llm", False),
                    "total_calls": self.llm_total_calls,
                    "successful_calls": self.llm_successful_calls,
                    "failed_calls": self.llm_failed_calls,
                    "timeouts": self.llm_timeouts,
                    "fallbacks": self.llm_fallbacks,
                    "total_latency_ms": round(self.llm_total_latency_ms, 2),
                    "average_latency_ms": avg_llm_lat,
                },
                "errors": {
                    "total": self.errors_total,
                    "by_category": dict(self.errors_by_category),
                },
                "uptime_seconds": round(time.time() - self._start_time, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


from backend.observability.store import (
    BaseMetricsStore,
    InMemoryMetricsStore,
    RedisMetricsStore,
    get_metrics_store,
    set_metrics_store,
)

# Singleton registry instance proxying the configured store
class _MetricsRegistryProxy:
    def __getattr__(self, name: str) -> Any:
        store = get_metrics_store()
        return getattr(store, name)


metrics_registry = _MetricsRegistryProxy()


def record_request(
    endpoint: str,
    status_code: int,
    duration_ms: float,
    intent: Optional[str] = None,
) -> None:
    """Helper to record request metrics on active store."""
    get_metrics_store().record_request(endpoint, status_code, duration_ms, intent)


def record_agent_execution(
    agent_name: str,
    duration_ms: float,
    success: bool = True,
) -> None:
    """Helper to record agent metrics on active store."""
    get_metrics_store().record_agent_execution(agent_name, duration_ms, success)


def record_llm_call(
    duration_ms: float,
    success: bool = True,
    is_timeout: bool = False,
    is_fallback: bool = False,
) -> None:
    """Helper to record LLM metrics on active store."""
    get_metrics_store().record_llm_call(duration_ms, success, is_timeout, is_fallback)


def record_retry() -> None:
    """Helper to record an upstream LLM retry attempt."""
    get_metrics_store().record_retry()


def record_error(category: str) -> None:
    """Helper to record categorized error on active store."""
    get_metrics_store().record_error(category)


# Background Job Metrics Tracking (Phase 16)
_JOB_METRICS_LOCK = threading.Lock()
_JOB_METRICS = {
    "jobs_submitted": 0,
    "jobs_completed": 0,
    "jobs_failed": 0,
    "total_duration_ms": 0.0,
}


def record_job_submitted() -> None:
    """Records a background job submission."""
    with _JOB_METRICS_LOCK:
        _JOB_METRICS["jobs_submitted"] += 1


def record_job_completed(duration_ms: float = 0.0) -> None:
    """Records a completed background job and execution duration."""
    with _JOB_METRICS_LOCK:
        _JOB_METRICS["jobs_completed"] += 1
        _JOB_METRICS["total_duration_ms"] += duration_ms


def record_job_failed() -> None:
    """Records a failed background job."""
    with _JOB_METRICS_LOCK:
        _JOB_METRICS["jobs_failed"] += 1


def get_job_metrics_snapshot() -> Dict[str, Any]:
    """Returns runtime telemetry for background jobs."""
    with _JOB_METRICS_LOCK:
        completed = _JOB_METRICS["jobs_completed"]
        tot_dur = _JOB_METRICS["total_duration_ms"]
        avg_dur = round(tot_dur / completed, 2) if completed > 0 else 0.0
        return {
            "jobs_submitted": _JOB_METRICS["jobs_submitted"],
            "jobs_completed": completed,
            "jobs_failed": _JOB_METRICS["jobs_failed"],
            "avg_duration_ms": avg_dur,
        }


def reset_job_metrics() -> None:
    """Resets job metric counters."""
    with _JOB_METRICS_LOCK:
        _JOB_METRICS["jobs_submitted"] = 0
        _JOB_METRICS["jobs_completed"] = 0
        _JOB_METRICS["jobs_failed"] = 0
        _JOB_METRICS["total_duration_ms"] = 0.0


_TRAFFIC_METRICS_LOCK = threading.Lock()
_TRAFFIC_METRICS: Dict[str, int] = {
    "rate_limit_rejections": 0,
    "quota_rejections": 0,
    "concurrency_rejections": 0,
    "queue_full_rejections": 0,
    "idempotency_replays": 0,
    "idempotency_conflicts": 0,
    "overload_rejections": 0,
}


def record_rate_limit_rejection() -> None:
    """Records a rate limit rejection event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["rate_limit_rejections"] += 1
    record_error("rate_limit_exceeded")


def record_quota_rejection() -> None:
    """Records a tenant quota exhaustion event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["quota_rejections"] += 1
    record_error("quota_exceeded")


def record_concurrency_rejection() -> None:
    """Records a concurrency pool exhaustion event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["concurrency_rejections"] += 1
    record_error("concurrency_limit_exceeded")


def record_queue_full_rejection() -> None:
    """Records a background job queue overflow event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["queue_full_rejections"] += 1
    record_error("queue_full")


def record_idempotency_replay() -> None:
    """Records a duplicate idempotent request replay."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["idempotency_replays"] += 1


def record_idempotency_conflict() -> None:
    """Records an idempotency payload conflict event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["idempotency_conflicts"] += 1
    record_error("idempotency_conflict")


def record_overload_rejection() -> None:
    """Records a load shedding rejection event."""
    with _TRAFFIC_METRICS_LOCK:
        _TRAFFIC_METRICS["overload_rejections"] += 1
    record_error("overload_rejected")


def get_traffic_metrics_snapshot() -> Dict[str, int]:
    """Returns runtime telemetry for traffic management and quotas."""
    with _TRAFFIC_METRICS_LOCK:
        return dict(_TRAFFIC_METRICS)


def reset_traffic_metrics() -> None:
    """Resets traffic management metric counters."""
    with _TRAFFIC_METRICS_LOCK:
        for k in _TRAFFIC_METRICS:
            _TRAFFIC_METRICS[k] = 0


def get_metrics_snapshot() -> Dict[str, Any]:
    """Helper to retrieve consolidated metrics snapshot."""
    snapshot = get_metrics_store().get_snapshot()
    snapshot["jobs"] = get_job_metrics_snapshot()
    snapshot["traffic"] = get_traffic_metrics_snapshot()
    return snapshot


def reset_metrics() -> None:
    """Helper to reset all metrics counters (for test isolation)."""
    get_metrics_store().reset()
    reset_job_metrics()
    reset_traffic_metrics()


