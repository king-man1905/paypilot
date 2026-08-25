"""Unit and Integration Tests for PayPilot FastAPI Endpoints."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.schemas import HealthResponse, AnalyzeResponse, ErrorResponse


@pytest.fixture(autouse=True)
def mock_offline_llm(monkeypatch):
    """Ensures all unit tests execute offline without making external network calls."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    with patch("backend.agents.supervisor.get_llm", return_value=None), \
         patch("backend.agents.recovery_agent.get_llm", return_value=None), \
         patch("backend.agents.llm_factory.get_llm", return_value=None):
        yield


@pytest.fixture
def client():
    """Provides a FastAPI TestClient instance."""
    return TestClient(app)


def test_health_endpoint(client):
    """Verify /health returns 200 and matches HealthResponse schema."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    validated = HealthResponse(**data)
    assert validated.status == "healthy"
    assert validated.service == "paypilot"
    assert validated.llm_provider in ["nvidia", "deterministic_fallback"]
    assert "timestamp" in data
    # Ensure no secret strings are present
    assert "nvapi" not in str(data).lower()


def test_analyze_valid_query(client):
    """Verify /api/v1/analyze processes a valid diagnostic query and returns full schema."""
    payload = {"query": "Why did my revenue decrease and what should I do?"}
    response = client.post("/api/v1/analyze", json=payload)
    assert response.status_code == 200

    data = response.json()
    validated = AnalyzeResponse(**data)

    assert validated.query == payload["query"]
    assert validated.intent in ["revenue", "payment", "checkout", "customer", "what_if"]
    assert len(validated.agents_participated) > 0
    assert len(validated.revenue_leaks) > 0
    assert len(validated.prioritized_actions) > 0
    assert len(validated.executive_recommendation) > 10
    assert len(validated.final_answer) > 20
    assert validated.execution_metadata.success is True
    assert validated.execution_metadata.execution_duration_ms > 0
    assert validated.execution_metadata.request_id is not None

    # Check top action rank
    assert validated.prioritized_actions[0].rank == 1
    assert validated.prioritized_actions[0].priority_score > 0


def test_analyze_empty_query_400(client):
    """Verify empty query returns 400 Bad Request."""
    response = client.post("/api/v1/analyze", json={"query": ""})
    assert response.status_code in [400, 422]
    data = response.json()
    assert "detail" in data


def test_analyze_whitespace_query_400(client):
    """Verify whitespace-only query returns 400 Bad Request."""
    response = client.post("/api/v1/analyze", json={"query": "    "})
    assert response.status_code == 400
    data = response.json()
    assert "cannot be empty" in data["detail"].lower()


def test_analyze_malformed_json_422(client):
    """Verify malformed payload (missing query field) returns 422."""
    response = client.post("/api/v1/analyze", json={"invalid_field": 123})
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "VALIDATION_ERROR"


def test_analyze_workflow_error_handling(client):
    """Verify internal workflow exception returns safe 500 without leaking traceback."""
    with patch("backend.api.routes.run_pipeline", side_effect=RuntimeError("Internal pipeline error")):
        response = client.post("/api/v1/analyze", json={"query": "Diagnose failure"})
        assert response.status_code == 500

        data = response.json()
        assert data["error"] in ["HTTP_500", "INTERNAL_SERVER_ERROR"]
        assert "Internal pipeline error" not in data["detail"]
        assert "internal server error" in data["detail"].lower() or "error occurred" in data["detail"].lower()


def test_observability_response_headers(client):
    """Verify tracking headers X-Request-ID and X-Response-Time-Ms are returned."""
    response = client.get("/health")
    assert "X-Request-ID" in response.headers
    assert "X-Response-Time-Ms" in response.headers

    custom_id = "test-req-custom-999"
    response2 = client.post(
        "/api/v1/analyze",
        json={"query": "Payment failure breakdown"},
        headers={"X-Request-ID": custom_id},
    )
    assert response2.headers.get("X-Request-ID") == custom_id


def test_security_never_exposes_api_keys(client):
    """Verify no response body or headers leak secret keys."""
    r1 = client.get("/health")
    assert "nvapi-" not in r1.text

    r2 = client.post("/api/v1/analyze", json={"query": "Why did revenue drop?"})
    assert "nvapi-" not in r2.text
