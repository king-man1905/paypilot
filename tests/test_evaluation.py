"""Tests for the Phase 7 Evaluation & Benchmarking System."""

import json
from pathlib import Path
from typing import Any, Dict, List
import pytest

from evaluation.routing_evaluator import evaluate_routing
from evaluation.evidence_evaluator import evaluate_evidence
from evaluation.numerical_evaluator import evaluate_numerical_consistency, _is_within_tolerance
from evaluation.recommendation_evaluator import evaluate_recommendations
from evaluation.benchmark import _compute_stats
from evaluation.run_evaluation import _evaluate_response_completeness


@pytest.fixture
def sample_dataset() -> List[Dict[str, Any]]:
    """Fixture providing a deterministic sample evaluation dataset."""
    return [
        {
            "id": "TEST-01",
            "category": "revenue",
            "query": "Why did my revenue drop?",
            "expected_intent": "revenue",
            "expected_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
            "required_evidence": ["revenue", "payment", "checkout", "customer"],
            "expected_metrics": {
                "total_realized_revenue_inr": 50092576.66,
                "payment_success_rate_pct": 81.71,
            },
            "min_actions_expected": 2,
        },
        {
            "id": "TEST-02",
            "category": "payment",
            "query": "Which payment method failed most?",
            "expected_intent": "payment",
            "expected_agents": ["payment_agent"],
            "required_evidence": ["payment"],
            "expected_metrics": {
                "netbanking_failure_rate_pct": 21.57,
            },
            "min_actions_expected": 1,
        },
        {
            "id": "TEST-03",
            "category": "checkout",
            "query": "Why is mobile converting less?",
            "expected_intent": "checkout",
            "expected_agents": ["checkout_agent"],
            "required_evidence": ["checkout"],
            "expected_metrics": {
                "mobile_android_conversion_pct": 80.66,
            },
            "min_actions_expected": 1,
        },
    ]


@pytest.fixture
def mock_pipeline_results() -> List[Dict[str, Any]]:
    """Fixture providing mock results matching sample_dataset."""
    return [
        {
            "intent": "revenue",
            "executed_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent", "recovery_agent"],
            "evidence": {
                "revenue": {"business_health": {"total_realized_revenue_inr": 50092576.66, "recoverable_opportunity_inr": 3488251.64}},
                "payment": {"overall_success_rate_pct": 81.71, "gross_failed_value_inr": 12654909.17},
                "checkout": {"mobile_conversion_rate_pct": 80.78, "desktop_conversion_rate_pct": 85.11},
                "customer": {"overall_refund_rate_pct": 8.24},
            },
            "priority_actions": [
                {
                    "action": "Streamline Mobile Checkout UX",
                    "problem": "Mobile checkout drops",
                    "estimated_revenue_impact_inr": 2589659.65,
                    "observed_loss_inr": 7332603.49,
                    "confidence": 0.9,
                    "effort": "Medium",
                    "urgency": "High",
                    "priority_score": 92.5,
                },
                {
                    "action": "Fix Bank Timeouts",
                    "problem": "Server timeouts",
                    "estimated_revenue_impact_inr": 1500000.0,
                    "observed_loss_inr": 3104914.53,
                    "confidence": 0.85,
                    "effort": "Low",
                    "urgency": "High",
                    "priority_score": 88.0,
                },
            ],
            "executive_summary": "Top recommendation P1 is Streamline Mobile Checkout UX",
            "final_answer": "Executive Diagnosis: Realized revenue is INR 50,092,576.66 with leakages in mobile checkout. Key Evidence shows 18.29% failed transactions. Prioritized Actions: P1 Streamline Mobile Checkout UX with recoverable upside of INR 2,589,659.65. Executive Recommendation: Execute P1 immediately.",
        },
        {
            "intent": "payment",
            "executed_agents": ["payment_agent"],
            "evidence": {
                "payment": {
                    "overall_success_rate_pct": 81.71,
                    "gross_failed_value_inr": 12654909.17,
                    "failure_rate_by_method": {"Netbanking": 21.57, "UPI": 18.83},
                },
            },
            "priority_actions": [
                {
                    "action": "Upgrade Netbanking Gateway Integration",
                    "problem": "Netbanking failure rate is 21.57%",
                    "estimated_revenue_impact_inr": 500000.0,
                    "observed_loss_inr": 1200000.0,
                    "confidence": 0.8,
                    "effort": "Low",
                    "urgency": "High",
                    "priority_score": 85.0,
                },
            ],
            "executive_summary": "P1 Netbanking Upgrade",
            "final_answer": "Payment Health Diagnosis: Netbanking failure rate is 21.57% with INR 12,654,909.17 gross failed value. Key Evidence highlights bank timeout leaks. Prioritized Actions: P1 Upgrade Netbanking Gateway with potential revenue uplift of INR 500,000. Executive Recommendation: Prioritize gateway upgrade.",
        },
        {
            "intent": "checkout",
            "executed_agents": ["checkout_agent"],
            "evidence": {
                "checkout": {
                    "mobile_conversion_rate_pct": 80.78,
                    "desktop_conversion_rate_pct": 85.11,
                    "device_performance": {
                        "Mobile_Android": {"conversion_rate_pct": 80.66, "lost_failed_value": 7332603.49},
                    },
                },
            },
            "priority_actions": [
                {
                    "action": "Android 1-Click Checkout",
                    "problem": "Android conversion lags desktop",
                    "estimated_revenue_impact_inr": 1800000.0,
                    "observed_loss_inr": 7332603.49,
                    "confidence": 0.9,
                    "effort": "Medium",
                    "urgency": "High",
                    "priority_score": 90.0,
                },
            ],
            "executive_summary": "P1 Android 1-Click Checkout",
            "final_answer": "Checkout Funnel Diagnosis: Mobile conversion is 80.66% lagging desktop by 4.45%. Key Evidence demonstrates cart drop-off leaks on Android. Prioritized Actions: P1 Android 1-Click Checkout with recoverable opportunity of INR 1,800,000. Executive Recommendation: Execute checkout redesign.",
        },
    ]


def test_dataset_schema_and_integrity():
    """Verifies that dataset.json exists, contains >= 30 valid cases, and adheres to schema."""
    dataset_file = Path(__file__).resolve().parent.parent / "evaluation" / "dataset.json"
    assert dataset_file.exists(), "evaluation/dataset.json must exist"

    with open(dataset_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    queries = data.get("queries", [])
    assert len(queries) >= 30, f"Expected at least 30 benchmark queries, got {len(queries)}"

    categories = set()
    for q in queries:
        assert "id" in q, f"Missing id in query {q}"
        assert "query" in q and len(q["query"].strip()) > 0
        assert "expected_intent" in q
        assert "expected_agents" in q and len(q["expected_agents"]) > 0
        assert "required_evidence" in q
        assert "expected_metrics" in q
        categories.add(q.get("category"))

    expected_categories = {"revenue", "payment", "checkout", "customer", "what_if", "holistic"}
    assert expected_categories.issubset(categories), f"Missing categories: {expected_categories - categories}"


def test_routing_evaluator_metrics(sample_dataset, mock_pipeline_results):
    """Tests routing accuracy, recall, precision, and unnecessary rate calculations."""
    results = evaluate_routing(sample_dataset, mock_pipeline_results)

    assert results["routing_accuracy_pct"] == 100.0
    assert results["agent_recall_pct"] == 100.0
    assert results["agent_precision_pct"] == 100.0
    assert results["unnecessary_agent_rate_pct"] == 0.0
    assert results["total_cases"] == 3
    assert all(c["passed"] for c in results["case_results"])


def test_routing_evaluator_mismatch():
    """Tests routing evaluator failure detection on wrong intent or missing agent."""
    dataset = [
        {"id": "T1", "query": "Q1", "expected_intent": "payment", "expected_agents": ["payment_agent"]},
    ]
    bad_results = [
        {"intent": "revenue", "executed_agents": ["revenue_agent"]},
    ]
    results = evaluate_routing(dataset, bad_results)

    assert results["routing_accuracy_pct"] == 0.0
    assert results["case_results"][0]["passed"] is False
    assert results["case_results"][0]["intent_match"] is False


def test_evidence_evaluator_metrics(sample_dataset, mock_pipeline_results):
    """Tests evidence section and field coverage evaluation."""
    results = evaluate_evidence(sample_dataset, mock_pipeline_results)

    assert results["evidence_coverage_pct"] == 100.0
    assert results["case_pass_rate_pct"] == 100.0
    assert results["cases_passed"] == 3


def test_evidence_evaluator_missing_section(sample_dataset, mock_pipeline_results):
    """Tests evidence evaluator detection of missing required sections."""
    bad_results = [dict(r) for r in mock_pipeline_results]
    bad_results[0]["evidence"] = {"revenue": {}}  # missing payment, checkout, customer

    results = evaluate_evidence(sample_dataset, bad_results)
    assert results["evidence_coverage_pct"] < 100.0
    assert results["case_results"][0]["passed"] is False


def test_numerical_evaluator_tolerance_helper():
    """Tests the numerical tolerance verification helper."""
    assert _is_within_tolerance(100.0, 100.0) is True
    assert _is_within_tolerance(100.4, 100.0, abs_tol=0.5) is True
    assert _is_within_tolerance(100.8, 100.0, rel_tol=0.01) is True
    assert _is_within_tolerance(110.0, 100.0, rel_tol=0.01, abs_tol=0.5) is False


def test_numerical_evaluator_consistency(sample_dataset, mock_pipeline_results):
    """Tests numerical evaluator consistency matching against ground truth."""
    results = evaluate_numerical_consistency(sample_dataset, mock_pipeline_results)

    assert results["numerical_consistency_pct"] == 100.0
    assert results["cases_passed"] == 3


def test_recommendation_evaluator_correctness(sample_dataset, mock_pipeline_results):
    """Tests recommendation ranking, fields validity, and executive reference checks."""
    results = evaluate_recommendations(sample_dataset, mock_pipeline_results)

    assert results["recommendation_correctness_pct"] == 100.0
    assert results["case_pass_rate_pct"] == 100.0
    assert results["cases_passed"] == 3


def test_recommendation_evaluator_non_monotonic():
    """Tests detection of non-monotonic priority score order in recommendations."""
    dataset = [{"id": "T1", "min_actions_expected": 2}]
    bad_actions_results = [
        {
            "priority_actions": [
                {
                    "action": "A1",
                    "problem": "P1",
                    "estimated_revenue_impact_inr": 100,
                    "observed_loss_inr": 200,
                    "confidence": 0.8,
                    "effort": "Low",
                    "urgency": "High",
                    "priority_score": 70.0,  # Lower than A2
                },
                {
                    "action": "A2",
                    "problem": "P2",
                    "estimated_revenue_impact_inr": 200,
                    "observed_loss_inr": 300,
                    "confidence": 0.9,
                    "effort": "Low",
                    "urgency": "High",
                    "priority_score": 90.0,  # Higher than A1 -> Violation!
                },
            ],
            "executive_summary": "P1 A1",
            "final_answer": "P1 A1",
        }
    ]
    results = evaluate_recommendations(dataset, bad_actions_results)
    assert results["case_results"][0]["monotonic_ranking"] is False
    assert results["case_results"][0]["passed"] is False


def test_response_completeness_evaluator(mock_pipeline_results):
    """Tests response completeness checking diagnosis, evidence, actions, and recommendation."""
    results = _evaluate_response_completeness(mock_pipeline_results)
    assert results["response_completeness_pct"] >= 80.0


def test_benchmark_statistics_computation():
    """Tests computation of min, avg, median, p95, and max latency statistics."""
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    stats = _compute_stats(latencies)

    assert stats["min_ms"] == 10.0
    assert stats["max_ms"] == 100.0
    assert stats["avg_ms"] == 55.0
    assert stats["median_ms"] == 55.0
    assert stats["p95_ms"] == 100.0
    assert stats["sample_count"] == 10


def test_benchmark_empty_latencies():
    """Tests handling of empty latency list."""
    stats = _compute_stats([])
    assert stats["min_ms"] == 0.0
    assert stats["avg_ms"] == 0.0


def test_evaluation_never_invokes_real_nvidia_provider(monkeypatch):
    """Proves that evaluation executes 100% offline and never invokes real NVIDIA."""
    from unittest.mock import patch
    from evaluation.run_evaluation import run_full_evaluation

    # Define a trap that throws if real ChatNVIDIA or ChatOpenAI is initialized
    def forbidden_real_llm(*args, **kwargs):
        raise RuntimeError("CRITICAL ERROR: Real NVIDIA LLM was invoked during evaluation!")

    dataset_file = Path(__file__).resolve().parent.parent / "evaluation" / "dataset.json"

    with patch("langchain_nvidia_ai_endpoints.ChatNVIDIA.__init__", side_effect=forbidden_real_llm), \
         patch("langchain_openai.ChatOpenAI.__init__", side_effect=forbidden_real_llm):
        report = run_full_evaluation(dataset_file)
        assert report["metrics"]["overall_pass_rate_pct"] == 100.0
        assert report["is_live_llm"] is False
        assert report["llm_provider"] == "MOCK/OFFLINE"

