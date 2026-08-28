"""Comprehensive Production Performance Profiler for PayPilot.

Profiles:
1. Deterministic Analytics Tools execution latency
2. Individual LangGraph specialist agents execution latency
3. Aggregator & Recovery agent deterministic synthesis latency
4. Live NVIDIA LLM latency per node (Supervisor, Aggregator, Recovery)
5. Total LLM calls per request
6. Prompt character & token size measurements
7. End-to-end /api/v1/analyze API latency (sequential & concurrent)
8. Concurrency semaphore and thread pool behavior
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
from backend.agents.supervisor import supervisor_node, SUPERVISOR_SYSTEM_PROMPT
from backend.agents.revenue_agent import revenue_agent_node
from backend.agents.payment_agent import payment_agent_node
from backend.agents.checkout_agent import checkout_agent_node
from backend.agents.customer_agent import customer_agent_node
from backend.agents.aggregator import evidence_aggregator_node, _compact_evidence_for_prompt, SYNTHESIS_SYSTEM_PROMPT
from backend.agents.recovery_agent import recovery_agent_node, EXECUTIVE_SYNTHESIS_PROMPT
from backend.agents.llm_factory import get_llm, get_llm_info
from evaluation.mock_llm import patch_offline_evaluation_llm


def measure_tools(iterations: int = 10) -> Dict[str, Any]:
    load_transaction_data()
    tools = {
        "get_business_health_summary": lambda: get_business_health_summary(),
        "get_revenue_trend (Weekly)": lambda: get_revenue_trend(frequency="W"),
        "get_revenue_trend (Monthly)": lambda: get_revenue_trend(frequency="M"),
        "get_revenue_lost_by_failure": lambda: get_revenue_lost_by_failure(),
        "get_what_if_success_rate (+3%)": lambda: get_what_if_success_rate(target_success_rate=3.0),
        "get_payment_success_rate": lambda: get_payment_success_rate(),
        "get_payment_failure_rate": lambda: get_payment_failure_rate(),
        "get_revenue_by_payment_method": lambda: get_revenue_by_payment_method(),
        "get_failure_rate_by_payment_method": lambda: get_failure_rate_by_payment_method(),
        "get_failure_reasons (top 5)": lambda: get_failure_reasons(limit=5),
        "get_failed_payment_value": lambda: get_failed_payment_value(),
        "get_conversion_by_device": lambda: get_conversion_by_device(),
        "get_top_revenue_leaks (top 5)": lambda: get_top_revenue_leaks(limit=5),
        "get_conversion_by_customer_type": lambda: get_conversion_by_customer_type(),
        "get_category_performance": lambda: get_category_performance(),
        "get_refund_rate": lambda: get_refund_rate(),
    }

    results = {}
    for name, fn in tools.items():
        durations = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            fn()
            durations.append((time.perf_counter() - t0) * 1000.0)
        results[name] = {
            "mean_ms": round(sum(durations) / len(durations), 3),
            "min_ms": round(min(durations), 3),
            "max_ms": round(max(durations), 3),
        }
    return results


def measure_specialist_nodes(iterations: int = 10) -> Dict[str, Any]:
    query = "Why did my revenue decrease and where is my biggest revenue leakage?"
    timings = {
        "revenue_agent": [],
        "payment_agent": [],
        "checkout_agent": [],
        "customer_agent": [],
    }

    for _ in range(iterations):
        state = {
            "user_query": query,
            "intent": "revenue",
            "required_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
            "errors": [],
        }

        t0 = time.perf_counter()
        state = revenue_agent_node(state)
        timings["revenue_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = payment_agent_node(state)
        timings["payment_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = checkout_agent_node(state)
        timings["checkout_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = customer_agent_node(state)
        timings["customer_agent"].append((time.perf_counter() - t0) * 1000.0)

    summary = {}
    for node, times in timings.items():
        summary[node] = {
            "mean_ms": round(sum(times) / len(times), 3),
            "min_ms": round(min(times), 3),
            "max_ms": round(max(times), 3),
        }
    return summary


def measure_live_llm_nodes() -> Dict[str, Any]:
    """Measures live NVIDIA LLM latency for supervisor, aggregator, and recovery agent nodes."""
    query = "Why did my revenue decrease and what should I do?"
    results = {}

    # 1. Supervisor LLM
    llm_sup = get_llm(node_type="supervisor", temperature=0.0, max_tokens=256)
    if llm_sup is not None:
        from langchain_core.messages import SystemMessage, HumanMessage
        t0 = time.perf_counter()
        prompt = f"{SUPERVISOR_SYSTEM_PROMPT}\n\nMerchant Query: {query}\n\nRespond ONLY with valid JSON in this exact structure: {{\"intent\": \"<category>\", \"required_agents\": [\"<agent_name>\", ...]}}"
        try:
            res_sup = llm_sup.invoke([
                SystemMessage(content="You are PayPilot's Supervisor Agent. Return only valid raw JSON without markdown wrapping."),
                HumanMessage(content=prompt),
            ])
            dur = (time.perf_counter() - t0) * 1000.0
            content = getattr(res_sup, "content", str(res_sup))
            results["supervisor_llm"] = {
                "status": "success",
                "latency_ms": round(dur, 2),
                "model": getattr(llm_sup, "model", "supervisor"),
                "output_length_chars": len(content),
                "prompt_length_chars": len(prompt),
                "prompt_est_tokens": len(prompt) // 4,
            }
        except Exception as e:
            results["supervisor_llm"] = {"status": "failed", "error": str(e)}

    # Build full state for Aggregator & Recovery
    state = {
        "user_query": query,
        "intent": "revenue",
        "required_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "errors": [],
    }
    state = revenue_agent_node(state)
    state = payment_agent_node(state)
    state = checkout_agent_node(state)
    state = customer_agent_node(state)

    # 2. Aggregator LLM
    llm_agg = get_llm(node_type="aggregator", temperature=0.2, max_tokens=2048)
    if llm_agg is not None:
        from langchain_core.messages import SystemMessage, HumanMessage
        compact_ev = _compact_evidence_for_prompt(state.get("evidence", {}))
        ev_json = json.dumps(compact_ev, indent=2, default=str)
        prompt_agg = f"Merchant Query: {query}\n\nFactual Numerical Evidence Gathered:\n{ev_json}\n\nSynthesize this evidence into an Executive Diagnosis and Action Plan for the merchant."
        t0 = time.perf_counter()
        try:
            res_agg = llm_agg.invoke([
                SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
                HumanMessage(content=prompt_agg),
            ])
            dur = (time.perf_counter() - t0) * 1000.0
            content_agg = getattr(res_agg, "content", str(res_agg))
            results["aggregator_llm"] = {
                "status": "success",
                "latency_ms": round(dur, 2),
                "model": getattr(llm_agg, "model", "aggregator"),
                "output_length_chars": len(content_agg),
                "prompt_length_chars": len(prompt_agg),
                "prompt_est_tokens": len(prompt_agg) // 4,
            }
        except Exception as e:
            results["aggregator_llm"] = {"status": "failed", "error": str(e)}

    # 3. Recovery LLM
    llm_rec = get_llm(node_type="recovery", temperature=0.2, max_tokens=2048)
    if llm_rec is not None:
        from langchain_core.messages import SystemMessage, HumanMessage
        from backend.agents.recovery_agent import generate_candidate_recovery_actions, prioritize_actions
        candidates = generate_candidate_recovery_actions(state.get("evidence", {}), {})
        ranked = prioritize_actions(candidates)
        payload = {
            "user_query": query,
            "diagnosis_key_facts": {},
            "prioritized_actions": ranked,
            "estimated_recovery": {"total_estimated_recoverable_inr": 3488251.64},
        }
        prompt_rec = f"Merchant Question: {query}\n\nStructured Recovery Evidence & Ranked Actions:\n{json.dumps(payload, indent=2, default=str)}\n\nGenerate the final Executive Decision Briefing following the required format."
        t0 = time.perf_counter()
        try:
            res_rec = llm_rec.invoke([
                SystemMessage(content=EXECUTIVE_SYNTHESIS_PROMPT),
                HumanMessage(content=prompt_rec),
            ])
            dur = (time.perf_counter() - t0) * 1000.0
            content_rec = getattr(res_rec, "content", str(res_rec))
            results["recovery_llm"] = {
                "status": "success",
                "latency_ms": round(dur, 2),
                "model": getattr(llm_rec, "model", "recovery"),
                "output_length_chars": len(content_rec),
                "prompt_length_chars": len(prompt_rec),
                "prompt_est_tokens": len(prompt_rec) // 4,
            }
        except Exception as e:
            results["recovery_llm"] = {"status": "failed", "error": str(e)}

    return results


def measure_concurrent_api(concurrencies: List[int]) -> Dict[str, Any]:
    from evaluation.performance_benchmark import run_concurrent_benchmark
    results = {}
    with patch_offline_evaluation_llm():
        for c in concurrencies:
            stats = asyncio.run(run_concurrent_benchmark(c))
            results[f"concurrency_{c}"] = stats
    return results


def main():
    print("=========================================================================")
    print("       PAYPILOT PRODUCTION PERFORMANCE AUDIT — MEASUREMENT SUITE        ")
    print("=========================================================================")

    # 1. Tools
    print("\n--- 1. ANALYTICS TOOLS LATENCIES (10 iterations) ---")
    tool_timings = measure_tools(10)
    for name, stats in sorted(tool_timings.items(), key=lambda x: x[1]["mean_ms"], reverse=True):
        print(f"  • {name:<38}: {stats['mean_ms']:>6.3f} ms (min: {stats['min_ms']:.3f}ms, max: {stats['max_ms']:.3f}ms)")

    # 2. Specialist Agents
    print("\n--- 2. SPECIALIST AGENT EXECUTION LATENCIES (10 iterations) ---")
    node_timings = measure_specialist_nodes(10)
    total_specialists = sum(v["mean_ms"] for v in node_timings.values())
    for node, stats in node_timings.items():
        print(f"  • {node:<22}: {stats['mean_ms']:>6.3f} ms (min: {stats['min_ms']:.3f}ms, max: {stats['max_ms']:.3f}ms)")
    print(f"  -> Total Specialist Agent Stage (Sequential) : {total_specialists:.3f} ms")

    # 3. Live LLM Breakdown
    print("\n--- 3. LIVE NVIDIA LLM PER-NODE LATENCY & PROMPT SIZES ---")
    llm_timings = measure_live_llm_nodes()
    total_llm_time = 0.0
    for node_key, data in llm_timings.items():
        if data.get("status") == "success":
            lat = data["latency_ms"]
            total_llm_time += lat
            print(f"  • {node_key:<18}: {lat:>8.2f} ms | Prompt: {data['prompt_length_chars']} chars (~{data['prompt_est_tokens']} tok) | Model: {data['model']}")
        else:
            print(f"  • {node_key:<18}: FAILED ({data.get('error')})")
    print(f"  -> Total Live LLM Network / Generation Time  : {total_llm_time:.2f} ms ({total_llm_time/1000.0:.2f}s)")

    # 4. E2E API & Concurrency
    print("\n--- 4. OFFLINE CONCURRENT THROUGHPUT & LATENCY (MockChatNVIDIA) ---")
    conc_results = measure_concurrent_api([1, 5, 10, 25, 50])
    for label, stats in conc_results.items():
        print(f"  • {label:<16}: Mean: {stats['mean_ms']:>6.2f} ms | P95: {stats['p95_ms']:>6.2f} ms | P99: {stats['p99_ms']:>6.2f} ms | Throughput: {stats['throughput_req_sec']:>6.2f} req/s")

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": tool_timings,
        "specialist_nodes": node_timings,
        "total_specialists_ms": round(total_specialists, 3),
        "live_llm": llm_timings,
        "total_live_llm_ms": round(total_llm_time, 2),
        "concurrent_benchmarks": conc_results,
    }

    with open("evaluation/production_perf_audit_results.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\n=========================================================================")
    print("Performance audit results saved to: evaluation/production_perf_audit_results.json")
    print("=========================================================================")


if __name__ == "__main__":
    main()
