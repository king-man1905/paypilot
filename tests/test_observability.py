"""Unit and Integration Tests for PayPilot Observability & Metrics Registry.

Verifies request accounting, agent performance metrics, LLM telemetry,
error taxonomy categorization, request correlation, and safe metric isolation.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.observability.metrics import (
    metrics_registry,
    record_request,
    record_agent_execution,
    record_llm_call,
    record_error,
    get_metrics_snapshot,
    reset_metrics,
)


@pytest.fixture(autouse=True)
def clean_metrics_environment(monkeypatch):
    """Ensures offline safety and isolates metrics before and after each test."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    reset_metrics()
    with patch("backend.agents.supervisor.get_llm", return_value=None), \
         patch("backend.agents.recovery_agent.get_llm", return_value=None), \
         patch("backend.agents.aggregator.get_llm", return_value=None), \
         patch("backend.agents.llm_factory.get_llm", return_value=None):
        yield
    reset_metrics()


@pytest.fixture
def client():
    """Provides a FastAPI TestClient instance."""
    return TestClient(app)


def test_metrics_endpoint_structure_and_types(client):
    """Verifies that GET /metrics returns HTTP 200 and matches the expected telemetry schema."""
    response = client.get("/metrics")
    assert response.status_code == 200

    data = response.json()
    assert "requests" in data
    assert "agents" in data
    assert "llm" in data
    assert "errors" in data
    assert "uptime_seconds" in data
    assert "timestamp" in data

    # Verify request metric fields
    reqs = data["requests"]
    assert "total" in reqs
    assert "successful" in reqs
    assert "failed" in reqs
    assert "total_duration_ms" in reqs
    assert "average_duration_ms" in reqs
    assert "by_endpoint" in reqs
    assert "by_status" in reqs
    assert "by_intent" in reqs

    # Verify agent metric fields
    agents = data["agents"]
    for agent_name in ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent", "recovery_agent"]:
        assert agent_name in agents
        assert "executions" in agents[agent_name]
        assert "failures" in agents[agent_name]
        assert "total_duration_ms" in agents[agent_name]
        assert "average_duration_ms" in agents[agent_name]

    # Verify LLM metric fields
    llm = data["llm"]
    assert "provider" in llm
    assert "model" in llm
    assert "total_calls" in llm
    assert "successful_calls" in llm
    assert "failed_calls" in llm
    assert "timeouts" in llm
    assert "fallbacks" in llm
    assert "average_latency_ms" in llm

    # Verify error metrics
    errors = data["errors"]
    assert "total" in errors
    assert "by_category" in errors
    for cat in ["validation_error", "timeout", "provider_error", "routing_error", "analytics_error", "internal_error"]:
        assert cat in errors["by_category"]


def test_request_metrics_increment_on_api_calls(client):
    """Verifies that requests to various endpoints increment counters and track status codes."""
    # 1. Successful /health call
    res1 = client.get("/health")
    assert res1.status_code == 200

    # 2. Successful /ready call
    res2 = client.get("/ready")
    assert res2.status_code == 200

    # 3. Bad request (400 - whitespace query)
    res3 = client.post("/api/v1/analyze", json={"query": "   \n\t  "})
    assert res3.status_code == 400

    # 4. Validation error (422 - empty or wrong schema)
    res4 = client.post("/api/v1/analyze", json={"wrong_field": 123})
    assert res4.status_code == 422

    # Check metrics
    metrics = client.get("/metrics").json()
    reqs = metrics["requests"]

    # 4 previous requests + 1 metrics request = 5 total
    assert reqs["total"] >= 4
    assert reqs["successful"] >= 2  # /health and /ready
    assert reqs["failed"] >= 2      # 400 and 422
    assert reqs["by_endpoint"].get("/health") == 1
    assert reqs["by_endpoint"].get("/ready") == 1
    assert reqs["by_status"].get("200") >= 2
    assert reqs["by_status"].get("400") == 1
    assert reqs["by_status"].get("422") == 1



def test_agent_metrics_tracking_and_averages():
    """Verifies agent execution counting and average latency computation."""
    record_agent_execution("revenue_agent", duration_ms=100.0, success=True)
    record_agent_execution("revenue_agent", duration_ms=200.0, success=True)
    record_agent_execution("revenue_agent", duration_ms=300.0, success=False)

    record_agent_execution("payment_agent", duration_ms=50.0, success=True)

    snapshot = get_metrics_snapshot()
    rev = snapshot["agents"]["revenue_agent"]
    assert rev["executions"] == 3
    assert rev["failures"] == 1
    assert rev["total_duration_ms"] == 600.0
    assert rev["average_duration_ms"] == 200.0

    pay = snapshot["agents"]["payment_agent"]
    assert pay["executions"] == 1
    assert pay["failures"] == 0
    assert pay["average_duration_ms"] == 50.0


def test_llm_metrics_and_fallback_tracking():
    """Verifies LLM latency, timeout, and fallback metrics."""
    record_llm_call(duration_ms=450.0, success=True, is_timeout=False, is_fallback=False)
    record_llm_call(duration_ms=1200.0, success=False, is_timeout=True, is_fallback=True)
    record_llm_call(duration_ms=150.0, success=False, is_timeout=False, is_fallback=True)

    snapshot = get_metrics_snapshot()
    llm = snapshot["llm"]
    assert llm["total_calls"] == 3
    assert llm["successful_calls"] == 1
    assert llm["failed_calls"] == 2
    assert llm["timeouts"] == 1
    assert llm["fallbacks"] == 2
    assert llm["total_latency_ms"] == 1800.0
    assert llm["average_latency_ms"] == 600.0


def test_error_taxonomy_categorization():
    """Verifies error counting categorized by standard error taxonomy."""
    record_error("validation_error")
    record_error("validation_error")
    record_error("timeout")
    record_error("provider_error")
    record_error("routing_error")
    record_error("analytics_error")
    record_error("internal_error")
    record_error("unknown_custom_error")  # Should map to internal_error fallback

    snapshot = get_metrics_snapshot()
    errs = snapshot["errors"]
    assert errs["total"] == 8
    assert errs["by_category"]["validation_error"] == 2
    assert errs["by_category"]["timeout"] == 1
    assert errs["by_category"]["provider_error"] == 1
    assert errs["by_category"]["routing_error"] == 1
    assert errs["by_category"]["analytics_error"] == 1
    assert errs["by_category"]["internal_error"] == 2


def test_request_id_correlation_and_headers(client):
    """Verifies that X-Request-ID is preserved across request lifecycle and returned in response."""
    custom_id = "custom-test-req-id-12345"
    response = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_id
    assert "X-Response-Time-Ms" in response.headers


def test_analyze_workflow_execution_updates_telemetry(client):
    """Verifies that executing /api/v1/analyze correctly updates request, intent, and agent telemetry."""
    query = "Why did my revenue decrease and what should I do?"
    response = client.post("/api/v1/analyze", json={"query": query})
    assert response.status_code == 200

    metrics = client.get("/metrics").json()
    # Check intent tracking
    assert metrics["requests"]["by_intent"].get("revenue", 0) >= 1

    # Check agent executions
    assert metrics["agents"]["revenue_agent"]["executions"] >= 1
    assert metrics["agents"]["payment_agent"]["executions"] >= 1
    assert metrics["agents"]["checkout_agent"]["executions"] >= 1
    assert metrics["agents"]["customer_agent"]["executions"] >= 1
    assert metrics["agents"]["recovery_agent"]["executions"] >= 1


def test_secret_non_exposure_in_metrics(client):
    """Verifies that /metrics endpoint never leaks API keys, authorization headers, or sensitive secrets."""
    response = client.get("/metrics")
    assert response.status_code == 200
    text_content = response.text.lower()

    # Verify no secret keywords or real keys
    assert "nvapi" not in text_content
    assert "sk-" not in text_content
    assert "api_key" not in text_content
    assert "authorization" not in text_content
    assert "bearer" not in text_content


def test_metrics_reset_isolation():
    """Verifies that reset_metrics clears all state completely."""
    record_request("/test", 200, 50.0, "test_intent")
    record_agent_execution("revenue_agent", 120.0, True)
    record_llm_call(300.0, True)
    record_error("timeout")

    before = get_metrics_snapshot()
    assert before["requests"]["total"] == 1
    assert before["agents"]["revenue_agent"]["executions"] == 1
    assert before["llm"]["total_calls"] == 1
    assert before["errors"]["total"] == 1

    reset_metrics()

    after = get_metrics_snapshot()
    assert after["requests"]["total"] == 0
    assert after["requests"]["successful"] == 0
    assert after["agents"]["revenue_agent"]["executions"] == 0
    assert after["llm"]["total_calls"] == 0
    assert after["errors"]["total"] == 0
