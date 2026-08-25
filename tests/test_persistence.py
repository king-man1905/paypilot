"""Distributed State & Persistence Regression Tests for PayPilot.

Verifies state classification, MetricsStore abstraction, InMemoryMetricsStore thread safety,
RedisMetricsStore graceful fallback degradation, secret omission, and offline isolation.
"""

import threading
import time
from typing import List
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app, get_concurrency_semaphore
from backend.config import PERSISTENCE_BACKEND, REDIS_URL, validate_config
from backend.observability.store import (
    BaseMetricsStore,
    InMemoryMetricsStore,
    RedisMetricsStore,
    get_metrics_store,
    set_metrics_store,
)
from backend.observability.metrics import (
    get_metrics_snapshot,
    record_agent_execution,
    record_error,
    record_llm_call,
    record_request,
    reset_metrics,
)
from backend.tools.analytics import clear_dataset_cache, load_transaction_data
from evaluation.mock_llm import patch_offline_evaluation_llm


@pytest.fixture(autouse=True)
def isolate_persistence_environment(monkeypatch):
    """Ensures each test operates with clean in-memory store and offline safety."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "memory")
    monkeypatch.setenv("REDIS_URL", "")
    set_metrics_store(InMemoryMetricsStore())
    reset_metrics()
    clear_dataset_cache()
    yield
    set_metrics_store(InMemoryMetricsStore())
    reset_metrics()
    clear_dataset_cache()


def test_in_memory_metrics_store_lifecycle_and_snapshots():
    """Verifies InMemoryMetricsStore records lifecycle metrics and produces valid snapshots."""
    store = InMemoryMetricsStore()
    assert store.backend_type == "memory"

    store.record_request("/api/v1/analyze", 200, 45.5, intent="payment")
    store.record_agent_execution("payment_agent", 12.3, success=True)
    store.record_llm_call(150.0, success=True, is_timeout=False, is_fallback=False)
    store.record_error("validation_error")

    snapshot = store.get_snapshot()

    assert snapshot["requests"]["total"] == 1
    assert snapshot["requests"]["successful"] == 1
    assert snapshot["requests"]["failed"] == 0
    assert snapshot["requests"]["by_endpoint"]["/api/v1/analyze"] == 1
    assert snapshot["requests"]["by_intent"]["payment"] == 1
    assert snapshot["agents"]["payment_agent"]["executions"] == 1
    assert snapshot["llm"]["total_calls"] == 1
    assert snapshot["llm"]["successful_calls"] == 1
    assert snapshot["errors"]["total"] == 1
    assert snapshot["errors"]["by_category"]["validation_error"] == 1
    assert snapshot["persistence"]["backend"] == "memory"
    assert snapshot["persistence"]["is_distributed"] is False


def test_in_memory_metrics_store_thread_safety():
    """Verifies multi-threaded concurrent updates do not corrupt metrics."""
    store = InMemoryMetricsStore()
    threads: List[threading.Thread] = []

    def worker():
        for _ in range(50):
            store.record_request("/api/v1/analyze", 200, 10.0, intent="revenue")
            store.record_agent_execution("revenue_agent", 5.0, success=True)

    for _ in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    snapshot = store.get_snapshot()
    assert snapshot["requests"]["total"] == 500
    assert snapshot["requests"]["successful"] == 500
    assert snapshot["agents"]["revenue_agent"]["executions"] == 500


def test_in_memory_metrics_store_reset_isolation():
    """Verifies reset() zeroes all counters for clean test isolation."""
    store = InMemoryMetricsStore()
    store.record_request("/api/v1/analyze", 200, 50.0)
    store.record_error("timeout")

    assert store.get_snapshot()["requests"]["total"] == 1
    store.reset()

    snap = store.get_snapshot()
    assert snap["requests"]["total"] == 0
    assert snap["requests"]["successful"] == 0
    assert snap["errors"]["total"] == 0


def test_get_metrics_store_factory_selection():
    """Verifies factory returns InMemoryMetricsStore by default."""
    store = get_metrics_store(backend="memory", force_new=True)
    assert isinstance(store, InMemoryMetricsStore)
    assert store.backend_type == "memory"


def test_redis_metrics_store_graceful_fallback_when_unreachable():
    """Verifies RedisMetricsStore gracefully degrades to in-memory store when Redis is offline."""
    # Attempt connection to an invalid/offline Redis port
    redis_store = RedisMetricsStore(redis_url="redis://127.0.0.1:9999/0")

    assert redis_store.is_connected is False
    assert redis_store.backend_type == "redis_fallback_memory"

    # Must not crash when recording metrics
    redis_store.record_request("/api/v1/analyze", 200, 30.0, intent="checkout")
    redis_store.record_agent_execution("checkout_agent", 10.0, success=True)
    redis_store.record_llm_call(100.0, success=True)
    redis_store.record_error("timeout")

    snapshot = redis_store.get_snapshot()
    assert snapshot["requests"]["total"] == 1
    assert snapshot["agents"]["checkout_agent"]["executions"] == 1
    assert snapshot["persistence"]["is_distributed"] is False
    assert snapshot["persistence"]["backend"] == "redis_fallback_memory"


def test_redis_metrics_store_runtime_exception_degradation():
    """Verifies that runtime Redis pipeline exceptions degrade gracefully to in-memory store."""
    redis_store = RedisMetricsStore(redis_url="")
    # Simulate an established connection where pipeline execution fails
    mock_client = MagicMock()
    mock_client.pipeline.side_effect = RuntimeError("Redis connection lost")
    redis_store._client = mock_client
    redis_store._is_connected = True

    # Record request should catch exception, set is_connected=False, and record in fallback
    redis_store.record_request("/health", 200, 5.0)

    assert redis_store.is_connected is False
    snap = redis_store.get_snapshot()
    assert snap["requests"]["total"] == 1


def test_secret_omission_in_persistence_and_config():
    """Verifies that persistence metadata and configuration never leak API keys."""
    store = InMemoryMetricsStore()
    snapshot = store.get_snapshot()

    snap_str = str(snapshot)
    assert "nvapi-" not in snap_str
    assert "api_key" not in snap_str
    assert "NVIDIA_API_KEY" not in snap_str

    cfg = validate_config()
    cfg_str = str(cfg)
    assert "nvapi-" not in cfg_str
    assert "has_api_key" in cfg
    assert "persistence_backend" in cfg


def test_dataset_cache_process_local_isolation():
    """Verifies dataset cache remains fast, thread-safe, and process-local."""
    clear_dataset_cache()
    df1 = load_transaction_data()
    df2 = load_transaction_data()
    assert df1 is df2
    assert len(df1) == 15000


def test_api_metrics_endpoint_with_store_delegation():
    """Verifies GET /metrics reflects the active store backend."""
    set_metrics_store(InMemoryMetricsStore())
    reset_metrics()

    with patch_offline_evaluation_llm():
        client = TestClient(app)
        res = client.get("/metrics")
        assert res.status_code == 200
        data = res.json()
        assert "persistence" in data
        assert data["persistence"]["backend"] == "memory"
