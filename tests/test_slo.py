"""Comprehensive Unit and Integration Tests for Service Level Objectives (SLO) Engine (Phase 19).

Tests percentile calculations, SLO metrics generation, deterministic breach detection,
alert cooldown deduplication, and administrative SLO query endpoints.
"""

import os
import time
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.observability.metrics import (
    record_agent_execution,
    record_error,
    record_job_completed,
    record_job_failed,
    record_job_submitted,
    record_llm_call,
    record_request,
    reset_metrics,
)
from backend.observability.slo import (
    SLOBreachEvent,
    SLOCooldownManager,
    _percentile,
    calculate_slo_metrics,
    evaluate_slo_breaches,
)


@pytest.fixture(autouse=True)
def clean_metrics_state():
    """Resets metrics registry and stores before and after each test."""
    reset_metrics()
    yield
    reset_metrics()


def test_percentile_calculation_accuracy():
    """Tests that percentile computation calculates exact p50, p95, and p99 values."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert _percentile(data, 50.0) == 55.0
    assert _percentile(data, 90.0) == 91.0
    assert _percentile(data, 100.0) == 100.0
    assert _percentile([], 95.0) == 0.0


def test_calculate_slo_metrics_aggregation():
    """Tests that calculate_slo_metrics aggregates operational counters into structured metrics."""
    # Seed sample requests
    record_request("/api/v1/analyze", 200, 120.0, intent="payment")
    record_request("/api/v1/analyze", 200, 150.0, intent="payment")
    record_request("/api/v1/analyze", 500, 300.0, intent="payment")
    record_llm_call(150.0, success=True, is_timeout=False, is_fallback=False)
    record_llm_call(200.0, success=False, is_timeout=True, is_fallback=True)
    record_agent_execution("payment_agent", 45.0, success=True)
    record_job_submitted()
    record_job_completed(duration_ms=250.0)

    report = calculate_slo_metrics()

    reqs = report["requests"]
    assert reqs["total_requests"] == 3
    assert reqs["successful_requests"] == 2
    assert reqs["error_requests"] == 1
    assert reqs["error_rate_pct"] == 33.33
    assert reqs["p50_latency_ms"] == 150.0

    llm = report["llm"]
    assert llm["total_calls"] == 2
    assert llm["fallbacks"] == 1
    assert llm["fallback_rate_pct"] == 50.0

    jobs = report["jobs"]
    assert jobs["total_submitted"] == 1
    assert jobs["completed"] == 1
    assert jobs["success_rate_pct"] == 100.0


def test_evaluate_slo_breaches_healthy_system():
    """Tests that a well-performing system produces HEALTHY status with zero active breaches."""
    # Record 10 healthy fast requests
    for _ in range(10):
        record_request("/api/v1/analyze", 200, 100.0)
        record_llm_call(50.0, success=True, is_fallback=False)

    eval_result = evaluate_slo_breaches()
    assert eval_result["overall_status"] == "HEALTHY"
    assert eval_result["active_breaches_count"] == 0
    assert len(eval_result["active_breaches"]) == 0


def test_evaluate_slo_breaches_p95_latency_breach():
    """Tests detection of P95 latency breach when observed latency exceeds target threshold."""
    # Record requests with high latency exceeding 1500ms target
    for _ in range(10):
        record_request("/api/v1/analyze", 200, 2200.0)

    eval_result = evaluate_slo_breaches()
    assert eval_result["overall_status"] == "BREACHED"
    assert eval_result["active_breaches_count"] >= 1

    breached_names = [b["slo_name"] for b in eval_result["active_breaches"]]
    assert "analyze_p95_latency" in breached_names


def test_evaluate_slo_breaches_error_rate_breach():
    """Tests detection of error rate breach when observed error rate exceeds target threshold."""
    # Record 4 errors out of 10 requests (40% error rate > 1% target)
    for _ in range(6):
        record_request("/api/v1/analyze", 200, 100.0)
    for _ in range(4):
        record_request("/api/v1/analyze", 500, 100.0)

    eval_result = evaluate_slo_breaches()
    assert eval_result["overall_status"] == "BREACHED"
    breached_names = [b["slo_name"] for b in eval_result["active_breaches"]]
    assert "api_error_rate" in breached_names


def test_slo_alert_cooldown_deduplication():
    """Tests that repeated breaches do not spam duplicate alert emissions within cooldown window."""
    cooldown_mgr = SLOCooldownManager()

    # Breached metrics dataset
    breached_metrics = {
        "requests": {
            "total_requests": 10,
            "error_requests": 5,
            "error_rate_pct": 50.0,
            "p95_latency_ms": 100.0,
        },
        "jobs": {"total_submitted": 0, "completed": 0, "failed": 0, "success_rate_pct": 100.0},
        "llm": {"total_calls": 0, "retries": 0, "fallbacks": 0, "fallback_rate_pct": 0.0},
        "agents": {},
    }

    # First evaluation -> should emit alert
    res1 = evaluate_slo_breaches(metrics=breached_metrics, cooldown_manager=cooldown_mgr)
    assert res1["active_breaches_count"] == 1
    assert res1["new_alerts_emitted_count"] == 1

    # Immediate second evaluation -> breach active, but alert suppressed by cooldown
    res2 = evaluate_slo_breaches(metrics=breached_metrics, cooldown_manager=cooldown_mgr)
    assert res2["active_breaches_count"] == 1
    assert res2["new_alerts_emitted_count"] == 0  # Deduplicated!


def test_admin_slo_endpoint_security_and_response():
    """Tests GET /admin/slo authorization and schema structure."""
    client = TestClient(app)
    os.environ["PAYPILOT_ADMIN_KEY"] = "admin-secret-key"
    os.environ["PAYPILOT_API_KEY"] = "analyst-key"

    try:
        # 1. Unauthenticated request -> 401
        res = client.get("/admin/slo")
        assert res.status_code == 401

        # 2. Analyst role -> 403
        res = client.get("/admin/slo", headers={"X-API-Key": "analyst-key"})
        assert res.status_code == 403

        # 3. Admin role -> 200 with complete evaluation report
        res = client.get("/admin/slo", headers={"X-API-Key": "admin-secret-key"})
        assert res.status_code == 200
        data = res.json()
        assert "overall_status" in data
        assert "total_slos_evaluated" in data
        assert "evaluated_slos" in data
        assert "metrics_evaluated" in data
    finally:
        os.environ.pop("PAYPILOT_ADMIN_KEY", None)
        os.environ.pop("PAYPILOT_API_KEY", None)

