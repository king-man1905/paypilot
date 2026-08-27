"""PayPilot End-to-End Evaluation & Benchmarking Orchestrator.

Executes all 30+ benchmark cases and calculates deterministic metrics:
1. Routing Accuracy
2. Agent Recall & Precision
3. Evidence Coverage
4. Numerical Consistency
5. Recommendation Correctness
6. Response Completeness
7. Latency (Avg & P95)
8. Categorized Failure Analysis
"""

import argparse
import json
import logging
import os
import io
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(line_buffering=True)

from backend.graph.run import run_pipeline
from backend.agents.llm_factory import get_llm_info
from evaluation.mock_llm import MockChatNVIDIA, get_mock_llm, get_mock_llm_info
from evaluation.routing_evaluator import evaluate_routing
from evaluation.evidence_evaluator import evaluate_evidence
from evaluation.numerical_evaluator import evaluate_numerical_consistency
from evaluation.recommendation_evaluator import evaluate_recommendations
from unittest.mock import patch

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("paypilot.eval")


def _evaluate_response_completeness(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluates whether responses contain all required executive sections."""
    required_sections = [
        ("diagnosis", ["diagnosis", "health", "overview", "realized revenue"]),
        ("evidence", ["leak", "failed", "conversion", "refund"]),
        ("actions", ["prioritized actions", "p1", "p2", "recommendations", "action"]),
        ("recommendation", ["executive recommendation", "management", "prioritize", "execute"]),
        ("upside", ["recoverable", "what-if", "opportunity", "uplift", "potential"]),
    ]

    total_checks = len(results) * len(required_sections)
    passed_checks = 0
    case_completeness = []

    for r in results:
        final_ans = (str(r.get("final_answer", "") or "") + " " + str(r.get("executive_summary", "") or "")).lower()
        sections_found = []
        for sec_name, keywords in required_sections:
            if any(k in final_ans for k in keywords):
                passed_checks += 1
                sections_found.append(sec_name)

        case_pct = round((len(sections_found) / len(required_sections)) * 100, 2)
        case_completeness.append({
            "id": r.get("user_query", "")[:30],
            "completeness_pct": case_pct,
            "sections_found": sections_found,
            "passed": case_pct >= 80.0,
        })

    completeness_pct = round((passed_checks / total_checks) * 100, 2) if total_checks > 0 else 100.0
    return {
        "response_completeness_pct": completeness_pct,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "case_completeness": case_completeness,
    }


def run_full_evaluation(dataset_path: Path, offline: bool = True) -> Dict[str, Any]:
    """Executes the full evaluation suite with mocked offline LLM."""
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    queries: List[Dict[str, Any]] = data.get("queries", [])
    mock_info = get_mock_llm_info()

    pipeline_results: List[Dict[str, Any]] = []
    latencies: List[float] = []
    execution_errors = []

    print("==========================================================================================", flush=True)
    print("                       PAYPILOT MULTI-AGENT BENCHMARK EVALUATION                          ", flush=True)
    print("==========================================================================================", flush=True)
    print(f"Evaluation Provider: {mock_info['active_provider']} | Model: {mock_info['active_model']} | Live API Calls: {mock_info['is_live_llm']}", flush=True)
    print(f"Total Benchmark Cases: {len(queries)}", flush=True)
    print("------------------------------------------------------------------------------------------\n", flush=True)

    # 1. Execute Pipeline for All Cases with Mocked Offline LLM
    with patch("backend.agents.llm_factory.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.supervisor.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.aggregator.get_llm", side_effect=get_mock_llm), \
         patch("backend.agents.recovery_agent.get_llm", side_effect=get_mock_llm):
        for i, q in enumerate(queries, 1):
            qid = q["id"]
            query_text = q["query"]
            category = q.get("category", "")

            t0 = time.perf_counter()
            try:
                state = run_pipeline(query_text)
                latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                latencies.append(latency_ms)
                pipeline_results.append(state)
                print(f"[{qid}] [OK] Category: {category:8s} | Latency: {latency_ms:6.1f}ms | '{query_text[:45]}...'", flush=True)
            except Exception as e:
                latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                latencies.append(latency_ms)
                print(f"[{qid}] [ERROR] Category: {category:8s} | Error: {type(e).__name__}: {e}", flush=True)
                pipeline_results.append({
                    "intent": "error",
                    "executed_agents": [],
                    "evidence": {},
                    "analysis": {},
                    "priority_actions": [],
                    "final_answer": "",
                    "error": str(e),
                })
                execution_errors.append({"id": qid, "query": query_text, "error": str(e)})

    # 2. Run Evaluators
    routing_eval = evaluate_routing(queries, pipeline_results)
    evidence_eval = evaluate_evidence(queries, pipeline_results)
    numerical_eval = evaluate_numerical_consistency(queries, pipeline_results)
    recommendation_eval = evaluate_recommendations(queries, pipeline_results)
    completeness_eval = _evaluate_response_completeness(pipeline_results)

    # 3. Latency Metrics
    sorted_lats = sorted(latencies) if latencies else [0.0]
    avg_latency = round(statistics.mean(sorted_lats), 2) if sorted_lats else 0.0
    p95_idx = int(0.95 * len(sorted_lats)) if int(0.95 * len(sorted_lats)) < len(sorted_lats) else len(sorted_lats) - 1
    p95_latency = round(sorted_lats[p95_idx], 2) if sorted_lats else 0.0

    # 4. End-to-End Case Pass Criteria
    total_cases = len(queries)
    passed_cases_count = 0
    failures_by_category = {
        "routing_failure": [],
        "missing_evidence": [],
        "numerical_mismatch": [],
        "recommendation_mismatch": [],
        "completeness_failure": [],
        "api_workflow_failure": execution_errors,
    }

    for i in range(total_cases):
        r_res = routing_eval["case_results"][i]
        e_res = evidence_eval["case_results"][i]
        n_res = numerical_eval["case_results"][i]
        rec_res = recommendation_eval["case_results"][i]
        c_res = completeness_eval["case_completeness"][i]

        is_passed = (
            r_res["passed"]
            and e_res["passed"]
            and n_res["passed"]
            and rec_res["passed"]
            and c_res["passed"]
        )

        if is_passed:
            passed_cases_count += 1
        else:
            qid = queries[i]["id"]
            q_text = queries[i]["query"]
            if not r_res["passed"]:
                failures_by_category["routing_failure"].append({
                    "id": qid,
                    "query": q_text,
                    "expected": queries[i]["expected_intent"],
                    "actual": r_res["actual_intent"],
                    "reason": f"Expected intent '{queries[i]['expected_intent']}', got '{r_res['actual_intent']}'",
                })
            if not e_res["passed"]:
                failures_by_category["missing_evidence"].append({
                    "id": qid,
                    "query": q_text,
                    "expected": queries[i].get("required_evidence", []),
                    "actual": e_res["present_evidence"],
                    "reason": f"Missing evidence sections: {e_res['missing_evidence']}",
                })
            if not n_res["passed"]:
                failures_by_category["numerical_mismatch"].append({
                    "id": qid,
                    "query": q_text,
                    "checks": n_res["metric_checks"],
                    "reason": "Numerical values deviated beyond tolerance",
                })
            if not rec_res["passed"]:
                failures_by_category["recommendation_mismatch"].append({
                    "id": qid,
                    "query": q_text,
                    "actions_count": rec_res["actions_count"],
                    "reason": "Recommendations missing, invalid schema, or non-monotonic ranking",
                })
            if not c_res["passed"]:
                failures_by_category["completeness_failure"].append({
                    "id": qid,
                    "query": q_text,
                    "completeness_pct": c_res["completeness_pct"],
                    "sections_found": c_res["sections_found"],
                    "reason": "Response missing required executive sections",
                })

    overall_pass_rate = round((passed_cases_count / total_cases) * 100, 2) if total_cases > 0 else 0.0

    # 5. Output Standardized Report
    print("\n--------------------------------", flush=True)
    print("PAYPILOT EVALUATION REPORT", flush=True)
    print("--------------------------------\n", flush=True)
    print("Dataset:", flush=True)
    print(f"{total_cases} cases\n", flush=True)
    print("Evaluation Provider:", flush=True)
    print(f"{mock_info['active_provider']}\n", flush=True)
    print("Live API Calls:", flush=True)
    print(f"{mock_info['is_live_llm']}\n", flush=True)
    print("Routing Accuracy:", flush=True)
    print(f"{routing_eval['routing_accuracy_pct']}%\n", flush=True)
    print("Agent Recall:", flush=True)
    print(f"{routing_eval['agent_recall_pct']}%\n", flush=True)
    print("Agent Precision:", flush=True)
    print(f"{routing_eval['agent_precision_pct']}%\n", flush=True)
    print("Evidence Coverage:", flush=True)
    print(f"{evidence_eval['evidence_coverage_pct']}%\n", flush=True)
    print("Numerical Consistency:", flush=True)
    print(f"{numerical_eval['numerical_consistency_pct']}%\n", flush=True)
    print("Recommendation Correctness:", flush=True)
    print(f"{recommendation_eval['recommendation_correctness_pct']}%\n", flush=True)
    print("Response Completeness:", flush=True)
    print(f"{completeness_eval['response_completeness_pct']}%\n", flush=True)
    print("Average Latency:", flush=True)
    print(f"{avg_latency} ms\n", flush=True)
    print("P95 Latency:", flush=True)
    print(f"{p95_latency} ms\n", flush=True)
    print("Overall Pass Rate:", flush=True)
    print(f"{overall_pass_rate}%\n", flush=True)

    # 6. Failure Analysis Summary
    total_failures = sum(len(v) for v in failures_by_category.values())
    if total_failures > 0:
        print("==========================================================================================", flush=True)
        print("                                FAILURE ANALYSIS SUMMARY                                  ", flush=True)
        print("==========================================================================================", flush=True)
        for cat, items in failures_by_category.items():
            if items:
                print(f"\n[{cat.upper()}] ({len(items)} cases):", flush=True)
                for it in items:
                    print(f"  • Case: [{it.get('id', 'N/A')}] '{it.get('query', '')[:50]}...'", flush=True)
                    print(f"    Reason: {it.get('reason', it.get('error', ''))}", flush=True)
        print("==========================================================================================\n", flush=True)
    else:
        print("All benchmark cases PASSED with ZERO failures.\n", flush=True)

    # 7. Save JSON Evaluation Report
    report = {
        "benchmark_name": data.get("benchmark_name"),
        "version": data.get("version"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_size": total_cases,
        "metrics": {
            "routing_accuracy_pct": routing_eval["routing_accuracy_pct"],
            "agent_recall_pct": routing_eval["agent_recall_pct"],
            "agent_precision_pct": routing_eval["agent_precision_pct"],
            "unnecessary_agent_rate_pct": routing_eval["unnecessary_agent_rate_pct"],
            "holistic_routing_correctness_pct": routing_eval["holistic_routing_correctness_pct"],
            "evidence_coverage_pct": evidence_eval["evidence_coverage_pct"],
            "numerical_consistency_pct": numerical_eval["numerical_consistency_pct"],
            "recommendation_correctness_pct": recommendation_eval["recommendation_correctness_pct"],
            "response_completeness_pct": completeness_eval["response_completeness_pct"],
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "overall_pass_rate_pct": overall_pass_rate,
        },
        "llm_provider": mock_info["active_provider"],
        "model": mock_info["active_model"],
        "is_live_llm": mock_info["is_live_llm"],
        "failures_by_category": failures_by_category,
    }

    report_path = dataset_path.parent / "evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Detailed evaluation report saved to: {report_path}", flush=True)

    return report


def main():
    parser = argparse.ArgumentParser(description="Run PayPilot Comprehensive Benchmark Evaluation.")
    parser.add_argument("--offline", action="store_true", help="Run benchmark in fast deterministic offline mode.")
    args = parser.parse_args()

    benchmark_file = Path(__file__).resolve().parent / "dataset.json"
    run_full_evaluation(benchmark_file, offline=args.offline)


if __name__ == "__main__":
    main()
