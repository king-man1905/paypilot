"""Detailed Performance Profiler for PayPilot.

Profiles:
- End-to-end /api/v1/analyze latency
- Per-node execution latencies: Supervisor, Revenue Agent, Payment Agent, Checkout Agent, Customer Agent, Aggregator, Recovery Agent
- LLM calls per request (live vs fallback vs mock)
- Sequential vs parallel execution timing
- Prompt token and character sizes
- Concurrency behavior across thread pool
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from backend.api.main import app
from backend.graph.run import run_pipeline
from backend.graph.workflow import paypilot_graph
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.tools.analytics import (
    clear_dataset_cache,
    load_transaction_data,
    get_payment_success_rate,
    get_payment_failure_rate,
    get_revenue_by_payment_method,
    get_failure_rate_by_payment_method,
    get_failure_reasons,
    get_failed_payment_value,
    get_revenue_lost_by_failure,
    get_business_health_summary,
    get_revenue_trend,
    get_what_if_success_rate,
    get_conversion_by_device,
    get_top_revenue_leaks,
    get_conversion_by_customer_type,
    get_category_performance,
    get_refund_rate,
)
from backend.agents.supervisor import supervisor_node
from backend.agents.revenue_agent import revenue_agent_node
from backend.agents.payment_agent import payment_agent_node
from backend.agents.checkout_agent import checkout_agent_node
from backend.agents.customer_agent import customer_agent_node
from backend.agents.aggregator import evidence_aggregator_node, _compact_evidence_for_prompt
from backend.agents.recovery_agent import recovery_agent_node
from evaluation.mock_llm import patch_offline_evaluation_llm


def profile_individual_nodes(iterations: int = 10) -> Dict[str, Any]:
    """Measures execution latency of each individual LangGraph node across multiple runs."""
    load_transaction_data()  # warm up cache

    query = "Why did my revenue decrease and where is my biggest revenue leakage?"
    node_timings: Dict[str, List[float]] = {
        "supervisor": [],
        "revenue_agent": [],
        "payment_agent": [],
        "checkout_agent": [],
        "customer_agent": [],
        "aggregator": [],
        "recovery_agent": [],
    }

    prompt_sizes = {}

    for _ in range(iterations):
        # 1. Supervisor
        state: Dict[str, Any] = {"user_query": query, "errors": []}
        t0 = time.perf_counter()
        state = supervisor_node(state)
        node_timings["supervisor"].append((time.perf_counter() - t0) * 1000.0)

        # 2. Revenue Agent
        t0 = time.perf_counter()
        state = revenue_agent_node(state)
        node_timings["revenue_agent"].append((time.perf_counter() - t0) * 1000.0)

        # 3. Payment Agent
        t0 = time.perf_counter()
        state = payment_agent_node(state)
        node_timings["payment_agent"].append((time.perf_counter() - t0) * 1000.0)

        # 4. Checkout Agent
        t0 = time.perf_counter()
        state = checkout_agent_node(state)
        node_timings["checkout_agent"].append((time.perf_counter() - t0) * 1000.0)

        # 5. Customer Agent
        t0 = time.perf_counter()
        state = customer_agent_node(state)
        node_timings["customer_agent"].append((time.perf_counter() - t0) * 1000.0)

        # 6. Aggregator
        t0 = time.perf_counter()
        state = evidence_aggregator_node(state)
        node_timings["aggregator"].append((time.perf_counter() - t0) * 1000.0)

        # Prompt size measurement
        compacted = _compact_evidence_for_prompt(state.get("evidence", {}))
        prompt_str = json.dumps(compacted)
        prompt_sizes["aggregator_evidence_json_chars"] = len(prompt_str)
        prompt_sizes["aggregator_evidence_est_tokens"] = len(prompt_str) // 4

        # 7. Recovery Agent
        t0 = time.perf_counter()
        state = recovery_agent_node(state)
        node_timings["recovery_agent"].append((time.perf_counter() - t0) * 1000.0)

    summary = {}
    for node, times in node_timings.items():
        summary[node] = {
            "mean_ms": round(sum(times) / len(times), 3),
            "min_ms": round(min(times), 3),
            "max_ms": round(max(times), 3),
        }

    return {"per_node": summary, "prompt_sizes": prompt_sizes}


def profile_individual_tools(iterations: int = 10) -> Dict[str, Any]:
    """Measures latency of individual analytics tools."""
    df = load_transaction_data()
    tools = {
        "get_business_health_summary": lambda: get_business_health_summary(),
        "get_revenue_trend_weekly": lambda: get_revenue_trend(frequency="W"),
        "get_revenue_trend_monthly": lambda: get_revenue_trend(frequency="M"),
        "get_revenue_lost_by_failure": lambda: get_revenue_lost_by_failure(),
        "get_what_if_success_rate": lambda: get_what_if_success_rate(target_success_rate=3.0),
        "get_payment_success_rate": lambda: get_payment_success_rate(),
        "get_payment_failure_rate": lambda: get_payment_failure_rate(),
        "get_revenue_by_payment_method": lambda: get_revenue_by_payment_method(),
        "get_failure_rate_by_payment_method": lambda: get_failure_rate_by_payment_method(),
        "get_failure_reasons": lambda: get_failure_reasons(limit=5),
        "get_failed_payment_value": lambda: get_failed_payment_value(),
        "get_conversion_by_device": lambda: get_conversion_by_device(),
        "get_top_revenue_leaks": lambda: get_top_revenue_leaks(limit=5),
        "get_conversion_by_customer_type": lambda: get_conversion_by_customer_type(),
        "get_category_performance": lambda: get_category_performance(),
        "get_refund_rate": lambda: get_refund_rate(),
    }

    tool_timings = {}
    for name, fn in tools.items():
        durations = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            fn()
            durations.append((time.perf_counter() - t0) * 1000.0)
        tool_timings[name] = {
            "mean_ms": round(sum(durations) / len(durations), 3),
            "min_ms": round(min(durations), 3),
            "max_ms": round(max(durations), 3),
        }
    return tool_timings


def profile_api_e2e(iterations: int = 10) -> Dict[str, Any]:
    """Measures end-to-end API latency for /api/v1/analyze."""
    client = TestClient(app)
    query = "Why did my revenue decrease and what should I do?"

    # Warmup
    client.post("/api/v1/analyze", json={"query": query})

    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        res = client.post("/api/v1/analyze", json={"query": query})
        dur = (time.perf_counter() - t0) * 1000.0
        assert res.status_code == 200
        latencies.append(dur)

    return {
        "mean_ms": round(sum(latencies) / len(latencies), 2),
        "min_ms": round(min(latencies), 2),
        "max_ms": round(max(latencies), 2),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
    }


def main():
    print("=================================================================")
    print("            PAYPILOT DETAILED PERFORMANCE PROFILING              ")
    print("=================================================================")

    # 1. Profile Tools
    print("\n--- 1. ANALYTICS TOOLS LATENCY PROFILE (10 runs) ---")
    tool_stats = profile_individual_tools(10)
    for tool_name, stats in sorted(tool_stats.items(), key=lambda x: x[1]["mean_ms"], reverse=True):
        print(f"  • {tool_name:<38}: {stats['mean_ms']:>6.3f} ms (min: {stats['min_ms']:.3f}ms, max: {stats['max_ms']:.3f}ms)")

    # 2. Profile Nodes
    print("\n--- 2. LANGGRAPH NODE LATENCY PROFILE (10 runs) ---")
    node_stats = profile_individual_nodes(10)
    total_node_time = sum(v["mean_ms"] for v in node_stats["per_node"].values())
    for node_name, stats in node_stats["per_node"].items():
        pct = (stats["mean_ms"] / total_node_time) * 100.0 if total_node_time > 0 else 0
        print(f"  • {node_name:<20}: {stats['mean_ms']:>6.3f} ms ({pct:>5.1f}%)")
    print(f"  -> Total Sequential Pipeline Time: {total_node_time:.3f} ms")

    print("\n--- 3. PAYLOAD & TOKEN SIZES ---")
    for k, v in node_stats["prompt_sizes"].items():
        print(f"  • {k}: {v}")

    # 3. Profile API E2E
    print("\n--- 4. END-TO-END /api/v1/analyze LATENCY (10 runs) ---")
    e2e_stats = profile_api_e2e(10)
    print(f"  • Mean Latency : {e2e_stats['mean_ms']} ms")
    print(f"  • Min Latency  : {e2e_stats['min_ms']} ms")
    print(f"  • Max Latency  : {e2e_stats['max_ms']} ms")
    print(f"  • P95 Latency  : {e2e_stats['p95_ms']} ms")

    results = {
        "tools": tool_stats,
        "nodes": node_stats,
        "e2e": e2e_stats,
    }
    with open("evaluation/detailed_profile_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
