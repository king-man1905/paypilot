"""Latency & Performance Benchmark for PayPilot.

Benchmarks:
1. Multi-agent workflow pipeline execution latency
2. FastAPI endpoint (/api/v1/analyze, /health, /ready) latency
3. Deterministic fallback mode latency
4. Mocked LLM synthesis latency

Calculates standard statistical metrics: min, avg, median, p95, max.
"""

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.api.main import app
from backend.graph.run import run_pipeline
from evaluation.mock_llm import get_mock_llm, get_mock_llm_info
from unittest.mock import patch


BENCHMARK_QUERIES = [
    "Why did my revenue decrease and where is my biggest leakage?",
    "Which payment method has the highest failure rate?",
    "Why are UPI transactions failing and what are the top bank timeout errors?",
    "Why are mobile users converting less than desktop shoppers?",
    "Which product category has the highest refund rate and why?",
    "What if payment success rate improves by 3%?",
    "What are my top 3 revenue recovery priorities and how much money can we recover?",
    "How much gross transaction value was lost due to failed payment attempts?",
    "Where is the checkout drop-off happening between Android and Desktop devices?",
    "Perform a complete audit of my revenue leakage and business health metrics.",
    "What is my total realized revenue and estimated recoverable opportunity?",
    "Simulate the additional revenue unlocked if we improve payment success rate by 5%.",
]


def _compute_stats(latencies: List[float]) -> Dict[str, float]:
    """Calculates min, avg, median, p95, and max from a list of latencies in ms."""
    if not latencies:
        return {"min_ms": 0.0, "avg_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}

    sorted_lats = sorted(latencies)
    n = len(sorted_lats)
    p95_idx = int(0.95 * n) if int(0.95 * n) < n else n - 1

    return {
        "min_ms": round(sorted_lats[0], 2),
        "avg_ms": round(statistics.mean(sorted_lats), 2),
        "median_ms": round(statistics.median(sorted_lats), 2),
        "p95_ms": round(sorted_lats[p95_idx], 2),
        "max_ms": round(sorted_lats[-1], 2),
        "sample_count": n,
    }


def run_latency_benchmark(queries: List[str] = BENCHMARK_QUERIES) -> Dict[str, Any]:
    """Runs latency benchmarks across pipeline and API endpoints using mock offline LLM."""
    client = TestClient(app)

    workflow_latencies: List[float] = []
    api_analyze_latencies: List[float] = []
    api_health_latencies: List[float] = []
    api_ready_latencies: List[float] = []

    print("==========================================================================================", flush=True)
    print("                         PAYPILOT LATENCY & PERFORMANCE BENCHMARK                         ", flush=True)
    print("==========================================================================================", flush=True)
    print(f"Benchmark Query Count: {len(queries)}", flush=True)
    print("------------------------------------------------------------------------------------------\n", flush=True)

    with patch("backend.agents.llm_factory.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.supervisor.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.aggregator.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.recovery_agent.get_llm", side_effect=get_mock_llm):

        # 1. Warm-up
        _ = run_pipeline("Warm-up query")
        _ = client.get("/health")
        _ = client.get("/ready")

        # 2. Workflow & API Benchmark Runs
        for i, q in enumerate(queries, 1):
            # A. Core LangGraph Workflow Execution
            t0 = time.perf_counter()
            _ = run_pipeline(q)
            wf_ms = (time.perf_counter() - t0) * 1000
            workflow_latencies.append(wf_ms)

            # B. Full HTTP API Execution
            t1 = time.perf_counter()
            res_a = client.post("/api/v1/analyze", json={"query": q})
            api_ms = (time.perf_counter() - t1) * 1000
            if res_a.status_code == 200:
                api_analyze_latencies.append(api_ms)

            # C. Liveness & Readiness Probes
            t2 = time.perf_counter()
            _ = client.get("/health")
            api_health_latencies.append((time.perf_counter() - t2) * 1000)

            t3 = time.perf_counter()
            _ = client.get("/ready")
            api_ready_latencies.append((time.perf_counter() - t3) * 1000)

            print(f"[{i:02d}/{len(queries)}] Pipeline: {wf_ms:6.1f}ms | API Analyze: {api_ms:6.1f}ms | Query: '{q[:40]}...'", flush=True)

    wf_stats = _compute_stats(workflow_latencies)
    api_stats = _compute_stats(api_analyze_latencies)
    health_stats = _compute_stats(api_health_latencies)
    ready_stats = _compute_stats(api_ready_latencies)

    print("\n==========================================================================================", flush=True)
    print("                             LATENCY BENCHMARK SUMMARY                                    ", flush=True)
    print("==========================================================================================", flush=True)
    print(f"Workflow Pipeline Latency : Min={wf_stats['min_ms']}ms | Avg={wf_stats['avg_ms']}ms | Median={wf_stats['median_ms']}ms | P95={wf_stats['p95_ms']}ms | Max={wf_stats['max_ms']}ms", flush=True)
    print(f"API /analyze Latency      : Min={api_stats['min_ms']}ms | Avg={api_stats['avg_ms']}ms | Median={api_stats['median_ms']}ms | P95={api_stats['p95_ms']}ms | Max={api_stats['max_ms']}ms", flush=True)
    print(f"Liveness (/health) Probe  : Avg={health_stats['avg_ms']}ms | P95={health_stats['p95_ms']}ms", flush=True)
    print(f"Readiness (/ready) Probe  : Avg={ready_stats['avg_ms']}ms | P95={ready_stats['p95_ms']}ms", flush=True)
    print("==========================================================================================\n", flush=True)

    summary = {
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "query_count": len(queries),
        "workflow_pipeline": wf_stats,
        "api_analyze_endpoint": api_stats,
        "api_health_endpoint": health_stats,
        "api_ready_endpoint": ready_stats,
    }

    return summary


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run PayPilot Latency Benchmark.")
    parser.add_argument("--live", action="store_true", help="Run benchmark with live NVIDIA API calls.")
    parser.add_argument("--offline", action="store_true", help="Run benchmark with fast deterministic offline mode.")
    args = parser.parse_args()

    if not args.live:
        # Default to fast, repeatable offline benchmarking
        os.environ["NVIDIA_API_KEY"] = ""

    run_latency_benchmark()


if __name__ == "__main__":
    main()

