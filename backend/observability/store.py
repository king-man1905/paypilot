"""Pluggable Metrics & State Store Abstraction for PayPilot.

Defines the BaseMetricsStore interface with InMemoryMetricsStore (default, process-local)
and optional RedisMetricsStore (shared distributed counters with resilient in-memory fallback).
"""

import abc
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import PERSISTENCE_BACKEND, REDIS_URL

logger = logging.getLogger("paypilot.persistence")

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



class BaseMetricsStore(abc.ABC):
    """Abstract base class for telemetry and metrics persistence stores."""

    @abc.abstractmethod
    def reset(self) -> None:
        """Resets all metrics counters (for test isolation / initialization)."""
        pass

    @abc.abstractmethod
    def record_request(
        self,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        intent: Optional[str] = None,
    ) -> None:
        """Records an HTTP request outcome and lifecycle duration."""
        pass

    @abc.abstractmethod
    def record_agent_execution(
        self,
        agent_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """Records specialist agent execution latency and outcome."""
        pass

    @abc.abstractmethod
    def record_llm_call(
        self,
        duration_ms: float,
        success: bool = True,
        is_timeout: bool = False,
        is_fallback: bool = False,
    ) -> None:
        """Records LLM invocation metrics and fallback occurrences."""
        pass

    @abc.abstractmethod
    def record_retry(self) -> None:
        """Records an upstream retry attempt."""
        pass

    @abc.abstractmethod
    def record_error(self, category: str) -> None:
        """Records an operational error grouped by category."""
        pass

    @abc.abstractmethod
    def get_snapshot(self) -> Dict[str, Any]:
        """Returns consolidated metrics snapshot as a structured dictionary."""
        pass

    @property
    @abc.abstractmethod
    def backend_type(self) -> str:
        """Returns the active persistence backend type name."""
        pass


class InMemoryMetricsStore(BaseMetricsStore):
    """Thread-safe process-local in-memory metrics store (default)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self.reset()

    @property
    def backend_type(self) -> str:
        return "memory"

    def reset(self) -> None:
        """Resets all in-memory counters to initial zero state."""
        with self._lock:
            self._start_time = time.time()

            # Request counters
            self.total_requests = 0
            self.successful_requests = 0
            self.failed_requests = 0
            self.total_request_duration_ms = 0.0
            self.request_durations: List[float] = []
            self.requests_by_endpoint: Dict[str, int] = {}
            self.requests_by_status: Dict[str, int] = {}
            self.requests_by_intent: Dict[str, int] = {}

            # Specialist agent counters
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
            self.llm_retries = 0
            self.llm_total_latency_ms = 0.0

            # Error categories
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
        with self._lock:
            self.total_requests += 1
            self.total_request_duration_ms += duration_ms
            self.request_durations.append(duration_ms)
            if len(self.request_durations) > 2000:
                self.request_durations.pop(0)

            if 200 <= status_code < 400:
                self.successful_requests += 1
            else:
                self.failed_requests += 1

            norm_ep = endpoint.split("?")[0]
            self.requests_by_endpoint[norm_ep] = (
                self.requests_by_endpoint.get(norm_ep, 0) + 1
            )

            status_str = str(status_code)
            self.requests_by_status[status_str] = (
                self.requests_by_status.get(status_str, 0) + 1
            )

            if intent:
                self.requests_by_intent[intent] = (
                    self.requests_by_intent.get(intent, 0) + 1
                )

    def record_agent_execution(
        self,
        agent_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        with self._lock:
            if agent_name not in self.agent_metrics:
                self.agent_metrics[agent_name] = {
                    "executions": 0,
                    "failures": 0,
                    "total_duration_ms": 0.0,
                }

            self.agent_metrics[agent_name]["executions"] += 1
            self.agent_metrics[agent_name]["total_duration_ms"] += duration_ms
            if not success:
                self.agent_metrics[agent_name]["failures"] += 1

    def record_llm_call(
        self,
        duration_ms: float,
        success: bool = True,
        is_timeout: bool = False,
        is_fallback: bool = False,
    ) -> None:
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

    def record_retry(self) -> None:
        with self._lock:
            self.llm_retries += 1

    def record_error(self, category: str) -> None:
        with self._lock:
            self.errors_total += 1
            cat_key = category if category in KNOWN_ERROR_CATEGORIES else "internal_error"
            self.errors_by_category[cat_key] = (
                self.errors_by_category.get(cat_key, 0) + 1
            )

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            avg_req_dur = (
                round(self.total_request_duration_ms / self.total_requests, 2)
                if self.total_requests > 0
                else 0.0
            )
            avg_llm_lat = (
                round(self.llm_total_latency_ms / self.llm_total_calls, 2)
                if self.llm_total_calls > 0
                else 0.0
            )

            formatted_agents: Dict[str, Dict[str, Any]] = {}
            for agent, data in self.agent_metrics.items():
                exec_count = data.get("executions", 0)
                tot_dur = data.get("total_duration_ms", 0.0)
                avg_dur = round(tot_dur / exec_count, 2) if exec_count > 0 else 0.0
                formatted_agents[agent] = {
                    "executions": exec_count,
                    "failures": data.get("failures", 0),
                    "total_duration_ms": round(tot_dur, 2),
                    "average_duration_ms": avg_dur,
                }

            from backend.agents.llm_factory import get_llm_info
            llm_info = get_llm_info()

            return {
                "requests": {
                    "total": self.total_requests,
                    "successful": self.successful_requests,
                    "failed": self.failed_requests,
                    "total_duration_ms": round(self.total_request_duration_ms, 2),
                    "average_duration_ms": avg_req_dur,
                    "durations_ms": list(self.request_durations),
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
                    "retries": self.llm_retries,
                    "total_latency_ms": round(self.llm_total_latency_ms, 2),
                    "average_latency_ms": avg_llm_lat,
                },
                "errors": {
                    "total": self.errors_total,
                    "by_category": dict(self.errors_by_category),
                },
                "uptime_seconds": round(time.time() - self._start_time, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "persistence": {
                    "backend": self.backend_type,
                    "is_distributed": False,
                },
            }



class RedisMetricsStore(BaseMetricsStore):
    """Pluggable Redis-backed metrics store with resilient in-memory fallback.

    If Redis is unreachable or the redis-py library is not installed, gracefully
    falls back to an internal InMemoryMetricsStore while logging categorized warnings.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or REDIS_URL
        self._fallback = InMemoryMetricsStore()
        self._client: Any = None
        self._is_connected = False
        self._connect()

    def _connect(self) -> None:
        """Attempts connection to Redis with graceful fallback handling."""
        if not self.redis_url:
            logger.info("No REDIS_URL configured; operating with in-memory store.")
            self._is_connected = False
            return

        try:
            import importlib
            redis_mod = importlib.import_module("redis")
            client = redis_mod.Redis.from_url(self.redis_url, socket_timeout=2.0, decode_responses=True)
            client.ping()
            self._client = client
            self._is_connected = True
            logger.info(f"Connected to Redis metrics backend at: {self.redis_url}")
        except Exception as e:
            logger.warning(
                f"Redis connection failed ({type(e).__name__}: {e}). "
                f"Gracefully falling back to in-memory metrics store."
            )
            self._client = None
            self._is_connected = False

    @property
    def backend_type(self) -> str:
        return "redis" if self._is_connected else "redis_fallback_memory"

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def reset(self) -> None:
        self._fallback.reset()
        if self._is_connected and self._client:
            try:
                # Clear paypilot metrics keys
                keys = self._client.keys("paypilot:metrics:*")
                if keys:
                    self._client.delete(*keys)
            except Exception as e:
                logger.warning(f"Failed to reset Redis keys ({e}); resetting fallback memory.")
                self._is_connected = False

    def record_request(
        self,
        endpoint: str,
        status_code: int,
        duration_ms: float,
        intent: Optional[str] = None,
    ) -> None:
        self._fallback.record_request(endpoint, status_code, duration_ms, intent)
        if self._is_connected and self._client:
            try:
                pipe = self._client.pipeline()
                pipe.incr("paypilot:metrics:requests:total")
                pipe.incrbyfloat("paypilot:metrics:requests:duration_ms", duration_ms)
                if 200 <= status_code < 400:
                    pipe.incr("paypilot:metrics:requests:successful")
                else:
                    pipe.incr("paypilot:metrics:requests:failed")
                pipe.execute()
            except Exception as e:
                logger.warning(f"Redis request recording notice ({e}); using local store.")
                self._is_connected = False

    def record_agent_execution(
        self,
        agent_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        self._fallback.record_agent_execution(agent_name, duration_ms, success)
        if self._is_connected and self._client:
            try:
                pipe = self._client.pipeline()
                pipe.hincrby(f"paypilot:metrics:agent:{agent_name}", "executions", 1)
                pipe.hincrbyfloat(f"paypilot:metrics:agent:{agent_name}", "duration_ms", duration_ms)
                if not success:
                    pipe.hincrby(f"paypilot:metrics:agent:{agent_name}", "failures", 1)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Redis agent recording notice ({e}); using local store.")
                self._is_connected = False

    def record_llm_call(
        self,
        duration_ms: float,
        success: bool = True,
        is_timeout: bool = False,
        is_fallback: bool = False,
    ) -> None:
        self._fallback.record_llm_call(duration_ms, success, is_timeout, is_fallback)
        if self._is_connected and self._client:
            try:
                pipe = self._client.pipeline()
                pipe.incr("paypilot:metrics:llm:total")
                pipe.incrbyfloat("paypilot:metrics:llm:latency_ms", duration_ms)
                if success:
                    pipe.incr("paypilot:metrics:llm:successful")
                else:
                    pipe.incr("paypilot:metrics:llm:failed")
                if is_timeout:
                    pipe.incr("paypilot:metrics:llm:timeouts")
                if is_fallback:
                    pipe.incr("paypilot:metrics:llm:fallbacks")
                pipe.execute()
            except Exception as e:
                logger.warning(f"Redis LLM recording notice ({e}); using local store.")
                self._is_connected = False

    def record_retry(self) -> None:
        self._fallback.record_retry()
        if self._is_connected and self._client:
            try:
                self._client.incr("paypilot:metrics:llm:retries")
            except Exception as e:
                logger.warning(f"Redis retry recording notice ({e}); using local store.")
                self._is_connected = False

    def record_error(self, category: str) -> None:

        self._fallback.record_error(category)
        if self._is_connected and self._client:
            try:
                safe_cat = category if category in KNOWN_ERROR_CATEGORIES else "internal_error"
                pipe = self._client.pipeline()
                pipe.incr("paypilot:metrics:errors:total")
                pipe.hincrby("paypilot:metrics:errors:by_category", safe_cat, 1)
                pipe.execute()
            except Exception as e:
                logger.warning(f"Redis error recording notice ({e}); using local store.")
                self._is_connected = False

    def get_snapshot(self) -> Dict[str, Any]:
        snapshot = self._fallback.get_snapshot()
        snapshot["persistence"] = {
            "backend": self.backend_type,
            "is_distributed": self._is_connected,
            "redis_url_configured": bool(self.redis_url),
        }
        return snapshot


# Singleton store management
_STORE_LOCK = threading.Lock()
_GLOBAL_STORE: Optional[BaseMetricsStore] = None


def get_metrics_store(
    backend: Optional[str] = None,
    redis_url: Optional[str] = None,
    force_new: bool = False,
) -> BaseMetricsStore:
    """Factory retrieving or initializing the singleton MetricsStore."""
    global _GLOBAL_STORE
    target_backend = (backend or PERSISTENCE_BACKEND).strip().lower()

    with _STORE_LOCK:
        if _GLOBAL_STORE is not None and not force_new:
            return _GLOBAL_STORE

        if target_backend == "redis":
            _GLOBAL_STORE = RedisMetricsStore(redis_url=redis_url)
        else:
            _GLOBAL_STORE = InMemoryMetricsStore()

        return _GLOBAL_STORE


def set_metrics_store(store: BaseMetricsStore) -> None:
    """Explicitly sets the active metrics store instance (for testing)."""
    global _GLOBAL_STORE
    with _STORE_LOCK:
        _GLOBAL_STORE = store
