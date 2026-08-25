"""PayPilot Phase 6 Production Hardening & Reliability Tests.

Verifies:
1. Request validation and query length constraints (oversized, empty, whitespace)
2. Safe error responses and standardized schemas (400, 422, 500, 503)
3. Subsystem readiness probe (`GET /ready`) and liveness probe (`GET /health`)
4. Request ID propagation and latency observability headers
5. Strict secret non-exposure across all endpoints and error states
6. LLM timeout and provider connection failure fallback handling
7. Configuration validation helper and concurrency guard robustness
"""

import json
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest

from backend.api.main import app
from backend.config import MAX_QUERY_LENGTH, validate_config


@pytest.fixture
def client():
    """Provides FastAPI test client."""
    return TestClient(app)


def test_oversized_query_rejection_400(client):
    """Verify that queries exceeding MAX_QUERY_LENGTH are rejected with HTTP 400."""
    oversized_query = "Why did my revenue decrease? " * 50  # > 1000 characters
    assert len(oversized_query) > MAX_QUERY_LENGTH

    response = client.post("/api/v1/analyze", json={"query": oversized_query})
    assert response.status_code in (400, 422)
    data = response.json()
    assert "error" in data
    assert "detail" in data
    assert str(MAX_QUERY_LENGTH) in data["detail"] or "maximum" in data["detail"].lower() or "length" in data["detail"].lower()


def test_empty_and_whitespace_query_rejection_400(client):
    """Verify that empty and whitespace queries return HTTP 400."""
    # Empty string
    res_empty = client.post("/api/v1/analyze", json={"query": ""})
    assert res_empty.status_code in (400, 422)
    assert "error" in res_empty.json()

    # Whitespace string
    res_ws = client.post("/api/v1/analyze", json={"query": "    \n\t  "})
    assert res_ws.status_code == 400
    assert "cannot be empty" in res_ws.json()["detail"]


def test_malformed_json_request_422(client):
    """Verify that malformed JSON payloads return HTTP 422 with structured schema."""
    response = client.post(
        "/api/v1/analyze",
        content="{'invalid_json': true}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "VALIDATION_ERROR"
    assert "status_code" in data
    assert data["status_code"] == 422


def test_readiness_endpoint_healthy_200(client):
    """Verify that GET /ready returns HTTP 200 when dataset and analytics are ready."""
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["service"] == "paypilot"
    assert data["checks"]["dataset_accessible"] is True
    assert data["checks"]["analytics_engine_ready"] is True
    assert data["checks"]["llm_provider_initialized"] is True
    assert "total_transactions_loaded" in data["details"]
    assert data["details"]["total_transactions_loaded"] > 0


def test_readiness_endpoint_unready_when_dataset_missing_503(client):
    """Verify that GET /ready returns HTTP 503 when dataset is not accessible."""
    from pathlib import Path
    with patch("backend.api.routes.DATA_PATH", Path("non_existent_dataset_file.csv")):
        response = client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["error"] == "HTTP_503"
        assert "Service unready" in data["detail"]


def test_request_id_and_duration_headers(client):
    """Verify that X-Request-ID is preserved or generated and timing is returned."""
    custom_id = "test-custom-req-id-12345"
    response = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_id
    assert "X-Response-Time-Ms" in response.headers
    assert float(response.headers["X-Response-Time-Ms"]) >= 0.0


def test_secret_non_exposure_in_all_endpoints(client, monkeypatch):
    """Verify that raw secret tokens never appear in any response or error body."""
    secret_key = "nvapi-very-secret-test-token-xyz-987654321"
    monkeypatch.setenv("NVIDIA_API_KEY", secret_key)

    # 1. Health check
    res_health = client.get("/health")
    assert secret_key not in res_health.text

    # 2. Readiness check
    res_ready = client.get("/ready")
    assert secret_key not in res_ready.text

    # 3. Analyze valid query
    res_analyze = client.post(
        "/api/v1/analyze",
        json={"query": "Why did my revenue drop?"},
    )
    assert secret_key not in res_analyze.text

    # 4. Error response
    res_err = client.post(
        "/api/v1/analyze",
        json={"query": ""},
    )
    assert secret_key not in res_err.text


def test_provider_failure_and_timeout_fallback(client, monkeypatch):
    """Verify that LLM timeout or connection failure falls back gracefully to deterministic analysis."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-valid-looking-key")

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("NVIDIA API connection timed out.")

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm), \
         patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):

        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue decrease?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "revenue"
        assert len(data["prioritized_actions"]) > 0
        assert data["final_answer"] is not None
        assert len(data["final_answer"]) > 50


def test_concurrency_guard_execution(client):
    """Verify that multiple successive requests execute reliably through the concurrency limiter."""
    for i in range(5):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"


def test_safe_500_error_response_no_stack_traces(client):
    """Verify that unexpected exceptions return clean 500 without leaking file paths or traces."""
    sensitive_path = "C:\\Users\\InternalAdmin\\SecretKeyStorage\\private.key"

    with patch("backend.api.routes.run_pipeline", side_effect=RuntimeError(f"Internal crash at {sensitive_path}")):
        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue drop?"},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "HTTP_500" or data["error"] == "INTERNAL_SERVER_ERROR"
        assert sensitive_path not in response.text
        assert "Traceback" not in response.text


def test_config_validation_helper():
    """Verify that validate_config provides diagnostic flags without leaking secrets."""
    cfg = validate_config()
    assert "dataset_exists" in cfg
    assert "llm_provider" in cfg
    assert "model" in cfg
    assert "has_api_key" in cfg
    assert "request_timeout_sec" in cfg
    assert "max_query_length" in cfg
    assert "max_concurrency" in cfg
    # Never returns raw API key value
    assert "NVIDIA_API_KEY" not in cfg
    assert "api_key" not in cfg
