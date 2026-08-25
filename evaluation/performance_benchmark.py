"""PayPilot Scalability & Performance Benchmark Harness.

Executes reproducible offline latency, throughput, and concurrency benchmarks
against the PayPilot multi-agent engine with zero external network dependencies.
"""

import asyncio
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport


from backend.api.main import app
from backend.observability.metrics import reset_metrics
from backend.security.rate_limiter import rate_limiter
from evaluation.mock_llm import patch_offline_evaluation_llm

# Silence verbose logging during benchmarking
logging.getLogger("paypilot").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

BENCHMARK_QUERIES = [
    "Why did my revenue decrease and where is my biggest revenue leakage?",
    "Which payment method has the highest failure rate and how much is lost?",
    "Why are mobile users converting less than desktop users?",
    "Which product category has the highest refund rate and what is the loss?",
    "What if payment success rate improves by 3%?",
    "Perform a complete audit of my revenue leakage and suggest top priorities.",
]


def calculate_latency_stats(latencies_ms: List[float], total_wall_time_sec: float) -> Dict[str, float]:
    """Calculates summary statistics for a list of latency measurements."""
    if not latencies_ms:
        return {
            "count": 0,
            "total_wall_time_sec": round(total_wall_time_sec, 3),
            "throughput_req_sec": 0.0,
            "mean_ms": 0.0,
            "median_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
        }

    sorted_lat = sorted(latencies_ms)
    n = len(sorted_lat)

    mean_val = statistics.mean(sorted_lat)
    median_val = statistics.median(sorted_lat)
    min_val = min(sorted_lat)
    max_val = max(sorted_lat)

    p95_idx = min(int(n * 0.95), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)
    p95_val = sorted_lat[p95_idx]
    p99_val = sorted_lat[p99_idx]

    throughput = round(n / total_wall_time_sec, 2) if total_wall_time_sec > 0 else 0.0

    return {
        "count": n,
        "total_wall_time_sec": round(total_wall_time_sec, 3),
        "throughput_req_sec": throughput,
        "mean_ms": round(mean_val, 2),
        "median_ms": round(median_val, 2),
        "p95_ms": round(p95_val, 2),
        "p99_ms": round(p99_val, 2),
        "min_ms": round(min_val, 2),
        "max_ms": round(max_val, 2),
    }


def run_sequential_benchmark(client: TestClient, request_count: int) -> Dict[str, Any]:
    """Runs a fixed count of sequential requests via TestClient."""
    latencies: List[float] = []
    successes = 0
    failures = 0

    t_start = time.perf_counter()
    for i in range(request_count):
        query = BENCHMARK_QUERIES[i % len(BENCHMARK_QUERIES)]
        req_start = time.perf_counter()
        res = client.post("/api/v1/analyze", json={"query": query})
        req_dur = (time.perf_counter() - req_start) * 1000.0
        latencies.append(req_dur)

        if res.status_code == 200:
            successes += 1
        else:
            failures += 1

    total_wall_sec = time.perf_counter() - t_start
    stats = calculate_latency_stats(latencies, total_wall_sec)
    stats["successful"] = successes
    stats["failed"] = failures
    return stats


async def _send_single_async_request(client: AsyncClient, query: str) -> Dict[str, Any]:
    """Sends a single async request and records timing."""
    req_start = time.perf_counter()
    res = await client.post("/api/v1/analyze", json={"query": query})
    dur_ms = (time.perf_counter() - req_start) * 1000.0
    return {
        "status_code": res.status_code,
        "duration_ms": dur_ms,
    }


async def run_concurrent_benchmark(concurrency_level: int) -> Dict[str, Any]:
    """Runs a concurrent load test using httpx.AsyncClient and ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as client:
        tasks = []
        for i in range(concurrency_level):
            query = BENCHMARK_QUERIES[i % len(BENCHMARK_QUERIES)]
            tasks.append(_send_single_async_request(client, query))

        t_start = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_wall_sec = time.perf_counter() - t_start

    latencies: List[float] = []
    successes = 0
    failures = 0

    for r in results:
        if isinstance(r, dict):
            latencies.append(r["duration_ms"])
            if r["status_code"] == 200:
                successes += 1
            else:
                failures += 1
        else:
            failures += 1

    stats = calculate_latency_stats(latencies, total_wall_sec)
    stats["successful"] = successes
    stats["failed"] = failures
    stats["concurrency_level"] = concurrency_level
    return stats


def run_full_performance_suite() -> Dict[str, Any]:
    """Executes the full suite of sequential and concurrent performance benchmarks."""
    reset_metrics()
    print("==========================================================================================")
    print("                    PAYPILOT SCALABILITY & PERFORMANCE BENCHMARK                         ")
    print("==========================================================================================")
    print("Execution Mode      : OFFLINE (MockChatNVIDIA)")
    print("Live API Calls      : False")
    print("Dataset             : data/processed/merchant_transactions.csv (15,000 rows)")
    print("------------------------------------------------------------------------------------------\n")

    with patch_offline_evaluation_llm(), patch.object(rate_limiter, "enabled", False):
        client = TestClient(app)

        # 1. Warm-up
        print("[1/6] Running warm-up single request...")
        baseline_1 = run_sequential_benchmark(client, 1)
        print(f"      Latency: {baseline_1['mean_ms']}ms | Status: {baseline_1['successful']} OK\n")

        # 2. Sequential 5 Requests
        print("[2/6] Running 5 sequential requests...")
        seq_5 = run_sequential_benchmark(client, 5)
        print(f"      Mean: {seq_5['mean_ms']}ms | P95: {seq_5['p95_ms']}ms | Throughput: {seq_5['throughput_req_sec']} req/s\n")

        # 3. Sequential 10 Requests
        print("[3/6] Running 10 sequential requests...")
        seq_10 = run_sequential_benchmark(client, 10)
        print(f"      Mean: {seq_10['mean_ms']}ms | P95: {seq_10['p95_ms']}ms | Throughput: {seq_10['throughput_req_sec']} req/s\n")

        # 4. Concurrent 10 Requests
        print("[4/6] Running 10 concurrent requests (load test)...")
        conc_10 = asyncio.run(run_concurrent_benchmark(10))
        print(f"      Mean: {conc_10['mean_ms']}ms | P95: {conc_10['p95_ms']}ms | Throughput: {conc_10['throughput_req_sec']} req/s\n")

        # 5. Concurrent 25 Requests
        print("[5/6] Running 25 concurrent requests (load test)...")
        conc_25 = asyncio.run(run_concurrent_benchmark(25))
        print(f"      Mean: {conc_25['mean_ms']}ms | P95: {conc_25['p95_ms']}ms | Throughput: {conc_25['throughput_req_sec']} req/s\n")

        # 6. Concurrent 50 Requests
        print("[6/6] Running 50 concurrent requests (stress test)...")
        conc_50 = asyncio.run(run_concurrent_benchmark(50))
        print(f"      Mean: {conc_50['mean_ms']}ms | P95: {conc_50['p95_ms']}ms | Throughput: {conc_50['throughput_req_sec']} req/s\n")

    report = {
        "metadata": {
            "mode": "OFFLINE_MOCK_LLM",
            "live_api_calls": False,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "dataset_rows": 15000,
        },
        "benchmarks": {
            "single_request": baseline_1,
            "sequential_5": seq_5,
            "sequential_10": seq_10,
            "concurrent_10": conc_10,
            "concurrent_25": conc_25,
            "concurrent_50": conc_50,
        },
    }

    report_path = Path("evaluation/performance_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("------------------------------------------------------------------------------------------")
    print(f"Performance report saved to: {report_path.resolve()}")
    print("==========================================================================================")
    return report


if __name__ == "__main__":
    run_full_performance_suite()
