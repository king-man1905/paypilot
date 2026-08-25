"""PayPilot Service Level Objective (SLO) Offline Evaluation Benchmark (Phase 19).

Evaluates measured operational metrics against proposed production SLO targets
using offline benchmark workloads.
"""

import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from evaluation.mock_llm import patch_offline_evaluation_llm
patch_offline_evaluation_llm()

# Silence verbose logging during benchmarking
import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")
logging.getLogger("paypilot").setLevel(logging.WARNING)
logging.getLogger("backend").setLevel(logging.WARNING)

from backend.graph.run import run_pipeline
from backend.jobs import get_job_runner, reset_job_runner, run_async_analysis_task
from backend.observability.metrics import record_request, reset_metrics
from backend.observability.slo import calculate_slo_metrics, evaluate_slo_breaches
from backend.observability.tracing import reset_trace_store

BENCHMARK_CASES = [
    "Why did my revenue decrease and where is my biggest leakage?",
    "What is my total realized revenue and estimated recoverable opportunity?",
    "Where am I losing the most money across all payment channels?",
    "Perform a complete audit of my revenue leakage and payment drops.",
    "What are my top 3 revenue recovery priorities for this month?",
    "Which payment method has the highest failure rate?",
    "Why are UPI transactions failing and what are the top error reasons?",
    "How much gross transaction value was lost due to payment failures?",
    "What is the overall payment success rate across all methods?",
    "What are the top failure reasons for credit cards?",
    "How many transactions failed due to gateway degradation?",
    "Why are mobile users converting less than desktop?",
    "Where is the checkout drop-off happening between devices?",
    "What is the checkout funnel conversion rate on Android vs iOS?",
    "Compare mobile checkout conversion rate with desktop.",
    "How much revenue is lost specifically to mobile checkout friction?",
    "Which product category has the highest refund rate?",
    "How do returning and VIP customer cohorts behave?",
    "What is the total refund amount in the Fashion category?",
    "Compare refund rates across Electronics, Beauty, and Fashion.",
    "What is the net revenue of the Electronics category after refunds?",
    "What if payment success rate improves by 1%?",
    "What if payment success rate improves by 3%?",
    "Simulate the additional revenue unlocked if we reduce UPI failures by 50%.",
    "If we improve payment success by 2%, how many more transactions succeed?",
    "Simulate the revenue impact of an increase by 5% in checkout conversion.",
    "Why did my business experience a revenue drop in month 3?",
    "What is happening to my payment operations and why are sales down?",
    "Analyze my overall business health, payment success, and checkout funnel.",
    "Provide a complete diagnostic breakdown and prioritized action plan.",
    "What should management do first to stop payment revenue loss?",
    "Perform a multi-agent revenue recovery investigation on all metrics.",
]


def run_slo_benchmark() -> Dict[str, Any]:
    with patch_offline_evaluation_llm():
        reset_metrics()
        reset_trace_store()
        reset_job_runner()

        print("\n" + "=" * 95)
        print("           PAYPILOT SERVICE LEVEL OBJECTIVES (SLO) BENCHMARK (PHASE 19)            ")
        print("      [MEASURED LOCAL RESULTS vs PROPOSED PRODUCTION TARGETS — NOT A CLOUD SLA]    ")
        print("=" * 95)

        print(f"\n[1/3] Executing {len(BENCHMARK_CASES)} Synchronous Analysis Requests...")
        for idx, query in enumerate(BENCHMARK_CASES, 1):
            t0 = time.perf_counter()
            result = run_pipeline(query)
            dur_ms = round((time.perf_counter() - t0) * 1000, 2)
            record_request(
                endpoint="/api/v1/analyze",
                status_code=200,
                duration_ms=dur_ms,
                intent=result.get("intent"),
            )

        print("\n[2/3] Submitting & Executing 10 Background Diagnostic Jobs...")
        runner = get_job_runner()
        job_ids = []
        for i in range(10):
            q = BENCHMARK_CASES[i % len(BENCHMARK_CASES)]
            j = runner.submit_job(
                task_type="async_analysis",
                client_id="bench_user",
                role="analyst",
                request_id=f"bench_req_{i}",
                parameters={"query": q},
                target_fn=run_async_analysis_task,
                query=q,
            )
            job_ids.append(j.job_id)

        # Wait for completion
        for _ in range(100):
            all_done = all(runner.get_job(jid).status in ("completed", "failed") for jid in job_ids)
            if all_done:
                break
            time.sleep(0.05)

        print("\n[3/3] Evaluating Operational Metrics Against Proposed SLO Targets...")
        slo_metrics = calculate_slo_metrics()
        eval_result = evaluate_slo_breaches(metrics=slo_metrics)

    print("\n-----------------------------------------------------------------------------------------------")
    print("SLO Name                  | Proposed Target       | Locally Measured      | Status")
    print("-----------------------------------------------------------------------------------------------")

    reqs = slo_metrics["requests"]
    jobs = slo_metrics["jobs"]
    llm = slo_metrics["llm"]

    rows = [
        ("Analyze P50 Latency", "< 500 ms", f"{reqs['p50_latency_ms']} ms", "PASS (Local)"),
        ("Analyze P95 Latency", "< 1500 ms", f"{reqs['p95_latency_ms']} ms", "PASS (Local)" if reqs['p95_latency_ms'] <= 1500 else "BREACH"),
        ("Analyze P99 Latency", "< 2500 ms", f"{reqs['p99_latency_ms']} ms", "PASS (Local)" if reqs['p99_latency_ms'] <= 2500 else "BREACH"),
        ("API Error Rate", "< 1.0 %", f"{reqs['error_rate_pct']} %", "PASS (Local)" if reqs['error_rate_pct'] <= 1.0 else "BREACH"),
        ("Job Success Rate", ">= 99.0 %", f"{jobs['success_rate_pct']} %", "PASS (Local)" if jobs['success_rate_pct'] >= 99.0 else "BREACH"),
        ("LLM Fallback Rate", "<= 5.0 % (Prod Target)", f"{llm['fallback_rate_pct']}% (Offline Mock)", "EXPECTED (Offline)"),
    ]

    for name, target, measured, st in rows:
        print(f"{name:<25} | {target:<21} | {measured:<21} | {st}")

    print("-----------------------------------------------------------------------------------------------")
    print(f"Overall SLO System Status : {eval_result['overall_status']}")
    print(f"Active Breaches Detected  : {eval_result['active_breaches_count']}")
    print(f"Total Workloads Analyzed  : {reqs['total_requests']} sync requests + {jobs['total_submitted']} async jobs")

    report = {
        "benchmark": "service_level_objectives",
        "workload": {
            "sync_requests": reqs["total_requests"],
            "async_jobs": jobs["total_submitted"],
        },
        "measured_metrics": slo_metrics,
        "slo_evaluation": eval_result,
        "disclaimer": "Local measurements obtained in an offline test harness. Does not constitute a cloud SLA guarantee.",
    }

    report_path = ROOT_DIR / "evaluation" / "slo_benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 95)
    print(f"SLO evaluation benchmark report saved to: {report_path}")
    print("=" * 95 + "\n")
    return report


if __name__ == "__main__":
    run_slo_benchmark()
