"""Performance and Scalability Regression Tests for PayPilot.

Verifies latency statistics calculation, thread-safe in-memory caching,
concurrency semaphore management, offline mock execution safety, and
numerical consistency across high-throughput request cycles.
"""

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import List
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from backend.api.main import app, get_concurrency_semaphore
from backend.observability.metrics import reset_metrics
from backend.tools.analytics import (
    clear_dataset_cache,
    get_total_revenue,
    get_payment_success_rate,
    load_transaction_data,
)
from evaluation.mock_llm import patch_offline_evaluation_llm
from evaluation.performance_benchmark import (
    calculate_latency_stats,
    run_concurrent_benchmark,
    run_sequential_benchmark,
)


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Ensures each test operates with clean metrics, cache, and offline mock safety."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    reset_metrics()
    clear_dataset_cache()
    yield
    reset_metrics()
    clear_dataset_cache()


def test_latency_statistics_calculation():
    """Tests mathematical correctness of summary statistics calculation."""
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    total_time = 1.0  # 1 second for 10 requests = 10 req/s

    stats = calculate_latency_stats(latencies, total_time)

    assert stats["count"] == 10
    assert stats["throughput_req_sec"] == 10.0
    assert stats["mean_ms"] == 55.0
    assert stats["median_ms"] == 55.0
    assert stats["min_ms"] == 10.0
    assert stats["max_ms"] == 100.0
    assert stats["p95_ms"] == 100.0
    assert stats["p99_ms"] == 100.0


def test_latency_statistics_empty_list():
    """Tests edge-case handling for empty latency lists."""
    stats = calculate_latency_stats([], 0.0)
    assert stats["count"] == 0
    assert stats["throughput_req_sec"] == 0.0
    assert stats["mean_ms"] == 0.0
    assert stats["p95_ms"] == 0.0


def test_dataset_in_memory_caching_and_invalidation():
    """Verifies that load_transaction_data caches the parsed DataFrame and supports clearing."""
    clear_dataset_cache()

    # First load - reads from disk and populates cache
    t0 = time.perf_counter()
    df1 = load_transaction_data()
    dur1 = time.perf_counter() - t0

    # Second load - retrieves from in-memory cache
    t1 = time.perf_counter()
    df2 = load_transaction_data()
    dur2 = time.perf_counter() - t1

    assert df1 is df2  # Same object identity in memory
    assert len(df1) == 15000
    assert dur2 < dur1 or dur2 < 0.05  # In-memory lookup is sub-millisecond

    # Force reload bypasses cache
    df3 = load_transaction_data(force_reload=True)
    assert len(df3) == 15000

    # Cache clear resets cache
    clear_dataset_cache()
    df4 = load_transaction_data()
    assert len(df4) == 15000


def test_analytics_numerical_consistency_with_caching():
    """Verifies that caching produces 100% identical business numbers to raw disk reads."""
    clear_dataset_cache()
    df_raw = load_transaction_data(force_reload=True)

    rev_raw = get_total_revenue(df_raw)
    rate_raw = get_payment_success_rate(df_raw)

    # Use cached retrieval
    rev_cached = get_total_revenue()
    rate_cached = get_payment_success_rate()

    assert rev_raw == rev_cached == 50092576.66
    assert rate_raw == rate_cached == 81.71


def test_thread_safe_dataset_concurrent_access():
    """Verifies multi-threaded concurrent calls to load_transaction_data produce valid results."""
    clear_dataset_cache()
    results: List[pd.DataFrame] = []
    errors: List[Exception] = []

    def worker():
        try:
            df = load_transaction_data()
            results.append(df)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10
    for df in results:
        assert len(df) == 15000


def test_event_loop_concurrency_semaphore():
    """Verifies get_concurrency_semaphore is loop-safe and returns an active Semaphore."""
    sem1 = get_concurrency_semaphore()
    assert isinstance(sem1, asyncio.Semaphore)
    sem2 = get_concurrency_semaphore()
    assert sem1 is sem2


def test_offline_sequential_benchmark_execution():
    """Verifies that sequential benchmark runs cleanly with zero failures under MockChatNVIDIA."""
    with patch_offline_evaluation_llm():
        client = TestClient(app)
        stats = run_sequential_benchmark(client, 3)

        assert stats["count"] == 3
        assert stats["successful"] == 3
        assert stats["failed"] == 0
        assert stats["mean_ms"] > 0.0
        assert stats["throughput_req_sec"] > 0.0


def test_offline_concurrent_benchmark_execution():
    """Verifies that concurrent benchmark runs cleanly under AsyncClient with MockChatNVIDIA."""
    with patch_offline_evaluation_llm():
        stats = asyncio.run(run_concurrent_benchmark(4))

        assert stats["count"] == 4
        assert stats["successful"] == 4
        assert stats["failed"] == 0
        assert stats["concurrency_level"] == 4
        assert stats["throughput_req_sec"] > 0.0



def test_never_invokes_real_nvidia_during_performance_suite():
    """Strictly proves performance benchmark harness will not invoke real NVIDIA network endpoints."""
    with patch_offline_evaluation_llm():
        with patch("langchain_nvidia_ai_endpoints.ChatNVIDIA.invoke") as mock_nvidia:
            client = TestClient(app)
            res = client.post("/api/v1/analyze", json={"query": "Which payment method fails most?"})
            assert res.status_code == 200
            assert mock_nvidia.call_count == 0
