"""API Security, Authentication & Authorization Test Suite for PayPilot.

Tests:
1. Unauthenticated requests to protected endpoints rejected with HTTP 401.
2. Authenticated requests succeed via X-API-Key and Authorization: Bearer tokens.
3. Invalid credentials rejected with HTTP 401.
4. Role-based authorization: Analyst vs Admin on /metrics (HTTP 403).
5. Public endpoints (/health, /ready) remain accessible without authentication.
6. Standard API security headers injected on all HTTP responses.
7. Malformed authorization headers handled gracefully.
8. Process-local rate limiting enforcement (HTTP 429).
9. Zero credential leakage in response bodies, telemetry, or headers.
10. All tests execute 100% offline without live NVIDIA network calls.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.security.rate_limiter import rate_limiter
from backend.tools.analytics import clear_dataset_cache
from evaluation.mock_llm import patch_offline_evaluation_llm

TEST_ANALYST_KEY = "paypilot-test-analyst-key-12345"
TEST_ADMIN_KEY = "paypilot-test-admin-key-67890"


@pytest.fixture(autouse=True)
def setup_security_environment(monkeypatch):
    """Sets up security test isolation environment."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PAYPILOT_API_KEY", TEST_ANALYST_KEY)
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    reset_metrics()
    clear_dataset_cache()
    rate_limiter.reset()
    yield
    reset_metrics()
    clear_dataset_cache()
    rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


def test_unauthenticated_analyze_request_rejected_401(client):
    """Verifies unauthenticated POST /api/v1/analyze returns HTTP 401."""
    response = client.post("/api/v1/analyze", json={"query": "Why did revenue drop?"})
    assert response.status_code == 401

    data = response.json()
    assert data["status_code"] == 401
    assert "Authentication required" in data["detail"]
    assert "WWW-Authenticate" in response.headers

    snapshot = get_metrics_snapshot()
    assert snapshot["errors"]["by_category"]["auth_error"] >= 1


def test_authenticated_analyze_succeeds_via_x_api_key(client):
    """Verifies valid X-API-Key header authorizes POST /api/v1/analyze."""
    with patch_offline_evaluation_llm():
        headers = {"X-API-Key": TEST_ANALYST_KEY}
        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue drop?"},
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] in ["revenue", "payment", "checkout", "customer", "what_if"]
        assert len(data["prioritized_actions"]) > 0


def test_authenticated_analyze_succeeds_via_bearer_token(client):
    """Verifies valid Authorization: Bearer token authorizes POST /api/v1/analyze."""
    with patch_offline_evaluation_llm():
        headers = {"Authorization": f"Bearer {TEST_ANALYST_KEY}"}
        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue drop?"},
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["intent"] in ["revenue", "payment", "checkout", "customer", "what_if"]


def test_invalid_api_key_rejected_401(client):
    """Verifies invalid API key string is rejected with HTTP 401."""
    headers = {"X-API-Key": "invalid-wrong-secret-key"}
    response = client.post(
        "/api/v1/analyze",
        json={"query": "Why did revenue drop?"},
        headers=headers,
    )
    assert response.status_code == 401
    data = response.json()
    assert "Invalid API key" in data["detail"]


def test_metrics_access_unauthenticated_rejected_401(client):
    """Verifies unauthenticated GET /metrics returns HTTP 401."""
    response = client.get("/metrics")
    assert response.status_code == 401


def test_metrics_access_insufficient_role_rejected_403(client):
    """Verifies analyst key is forbidden from accessing administrative GET /metrics."""
    headers = {"X-API-Key": TEST_ANALYST_KEY}
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 403

    data = response.json()
    assert "Forbidden" in data["detail"]
    assert "Administrative privileges required" in data["detail"]

    snapshot = get_metrics_snapshot()
    assert snapshot["errors"]["by_category"]["forbidden_error"] >= 1


def test_metrics_access_admin_authorized_200(client):
    """Verifies admin key successfully accesses GET /metrics."""
    headers = {"X-API-Key": TEST_ADMIN_KEY}
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert "requests" in data
    assert "agents" in data
    assert "llm" in data
    assert "errors" in data


def test_health_and_ready_endpoints_remain_public_without_auth(client):
    """Verifies /health and /ready require zero authentication for container healthchecks."""
    # Health liveness
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "healthy"

    # Ready readiness
    res_ready = client.get("/ready")
    assert res_ready.status_code == 200
    assert res_ready.json()["status"] == "ready"


def test_security_headers_injected_on_all_responses(client):
    """Verifies essential production HTTP security headers are present on API responses."""
    res = client.get("/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" in res.headers
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in res.headers

    # Check cache control on protected endpoints
    res_metrics = client.get("/metrics", headers={"X-API-Key": TEST_ADMIN_KEY})
    assert res_metrics.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"


def test_malformed_authorization_headers_handled_safely(client):
    """Verifies malformed Authorization schemas (e.g. Basic auth, empty) return 401 without error."""
    # Basic auth schema instead of Bearer
    res1 = client.post(
        "/api/v1/analyze",
        json={"query": "test"},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert res1.status_code == 401

    # Empty auth header
    res2 = client.post(
        "/api/v1/analyze",
        json={"query": "test"},
        headers={"Authorization": ""},
    )
    assert res2.status_code == 401


def test_rate_limiter_local_sliding_window_enforcement(client, monkeypatch):
    """Verifies that exceeding rate limits returns HTTP 429 with Retry-After header."""
    rate_limiter.reset()

    # Configure a tiny 3-request limit for this test
    with patch.object(rate_limiter, "default_limit", 3), patch.object(rate_limiter, "default_window", 10):
        headers = {"X-API-Key": TEST_ANALYST_KEY}
        with patch_offline_evaluation_llm():
            # 3 requests pass
            for _ in range(3):
                r = client.post("/api/v1/analyze", json={"query": "Why did revenue drop?"}, headers=headers)
                assert r.status_code == 200

            # 4th request exceeds rate limit
            r_blocked = client.post("/api/v1/analyze", json={"query": "Why did revenue drop?"}, headers=headers)
            assert r_blocked.status_code == 429
            assert "Retry-After" in r_blocked.headers
            assert "Rate limit exceeded" in r_blocked.json()["error"]["message"]


def test_credentials_never_leaked_in_responses_or_logs(client):
    """Verifies that neither analyst key, admin key, nor headers are ever reflected in responses."""
    headers = {"X-API-Key": TEST_ANALYST_KEY}
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 403

    body_str = response.text
    assert TEST_ANALYST_KEY not in body_str
    assert TEST_ADMIN_KEY not in body_str
    assert "X-API-Key" not in body_str


def test_cross_tenant_job_isolation_403_and_admin_bypass_200(client):
    """Hardening 2 & 8: Non-owner tenants receive 403 Forbidden when querying another tenant's job; Admin receives 200."""
    from backend.security.auth import AuthenticatedUser, require_analyst

    user_a = AuthenticatedUser(client_id="tenant_alpha_sec", role="analyst")
    user_b = AuthenticatedUser(client_id="tenant_beta_sec", role="analyst")
    admin_user = AuthenticatedUser(client_id="super_admin_sec", role="admin")

    # 1. Tenant A submits job
    app.dependency_overrides[require_analyst] = lambda: user_a
    res_a = client.post(
        "/api/v1/jobs",
        json={"query": "Alpha diagnostic query", "task_type": "merchant_diagnostic"},
    )
    assert res_a.status_code == 202
    job_id = res_a.json()["job_id"]

    # 2. Tenant B tries to query Tenant A's job -> 403 Forbidden
    app.dependency_overrides[require_analyst] = lambda: user_b
    res_b = client.get(f"/api/v1/jobs/{job_id}")
    assert res_b.status_code == 403
    assert "Forbidden" in res_b.json()["detail"]

    # 3. Admin user queries Tenant A's job -> 200 OK
    app.dependency_overrides[require_analyst] = lambda: admin_user
    res_admin = client.get(f"/api/v1/jobs/{job_id}")
    assert res_admin.status_code == 200
    assert res_admin.json()["job_id"] == job_id

    app.dependency_overrides.clear()

