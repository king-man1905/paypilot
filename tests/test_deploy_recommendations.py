"""Unit and Integration Tests for Recommendation Deployment API (/api/v1/recommendations/deploy).

Tests:
1. Deploy Recommendation success (HTTP 202 Accepted).
2. Validation error on invalid action rank / missing title (HTTP 400 / 422).
3. Unauthorized request rejection (HTTP 401 / 403).
4. Idempotency replay and conflict handling (HTTP 202 / 409).
5. Queue full / quota exhaustion handling (HTTP 429).
6. Structured audit trail event emission.
7. 100% offline test execution.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.schemas import DeployRecommendationResponse
from backend.jobs import get_job_runner, reset_job_runner
from backend.observability.audit import get_audit_store, reset_audit_store
from backend.observability.metrics import reset_metrics
from backend.security.idempotency import reset_idempotency_store
from backend.security.quotas import reset_quota_manager

TEST_ANALYST_KEY = "paypilot-test-analyst-key"
TEST_ADMIN_KEY = "paypilot-test-admin-key"
TEST_CLIENT_ID = "merchant_enterprise_01"


@pytest.fixture(autouse=True)
def setup_deploy_test_env(monkeypatch):
    """Sets up clean isolated test environment."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PAYPILOT_API_KEY", TEST_ANALYST_KEY)
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("JOB_MAX_WORKERS", "2")
    monkeypatch.setenv("JOB_MAX_QUEUE_SIZE", "10")
    reset_metrics()
    reset_audit_store()
    reset_job_runner()
    reset_idempotency_store()
    reset_quota_manager()
    yield
    reset_metrics()
    reset_audit_store()
    reset_job_runner()
    reset_idempotency_store()
    reset_quota_manager()


@pytest.fixture
def client():
    return TestClient(app)


def test_deploy_recommendation_success(client):
    """Verifies valid recommendation rollout request returns HTTP 202 with DeployRecommendationResponse."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
    }
    payload = {
        "action_rank": 1,
        "action_title": "Implement Dynamic Multi-Gateway Failover Routing for UPI",
        "affected_area": "Core Payment Processing",
        "estimated_revenue_impact_inr": 4820000.0,
        "parameters": {"auto_retry_count": 3},
    }

    response = client.post("/api/v1/recommendations/deploy", json=payload, headers=headers)
    assert response.status_code == 202

    data = response.json()
    validated = DeployRecommendationResponse(**data)
    assert validated.action_rank == 1
    assert validated.action_title == payload["action_title"]
    assert validated.status == "enqueued"
    assert validated.client_id in [TEST_CLIENT_ID, "merchant-client"]
    assert validated.estimated_revenue_impact_inr == 4820000.0
    assert validated.deployment_id.startswith("dep_")
    assert validated.job_id.startswith("job_")
    assert "successfully enqueued" in validated.message

    # Verify job runner queued the task
    runner = get_job_runner()
    job = runner.get_job(validated.job_id, client_id=validated.client_id)
    assert job is not None
    assert job.task_type == "action_deployment"
    assert job.parameters["action_rank"] == 1

    # Verify audit event emitted
    audit_store = get_audit_store()
    events = audit_store.get_events(event_type="recommendation_deployed")
    assert len(events) > 0
    assert events[0].event_type == "recommendation_deployed"
    assert events[0].client_id == validated.client_id


def test_deploy_recommendation_invalid_rank_400(client):
    """Verifies invalid action rank returns 400/422 Bad Request."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
    }

    # Rank 0 (out of bounds)
    r1 = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 0, "action_title": "Invalid rank action"},
        headers=headers,
    )
    assert r1.status_code in [400, 422]

    # Rank 50 (exceeds bounds)
    r2 = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 50, "action_title": "Invalid rank action"},
        headers=headers,
    )
    assert r2.status_code in [400, 422]


def test_deploy_recommendation_empty_title_400(client):
    """Verifies empty or whitespace title returns 400/422 Bad Request."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
    }

    r1 = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 1, "action_title": "   "},
        headers=headers,
    )
    assert r1.status_code in [400, 422]


def test_deploy_recommendation_unauthorized_401(client):
    """Verifies missing or invalid API credentials return HTTP 401 Unauthorized."""
    # No auth header
    r1 = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 1, "action_title": "Deploy action"},
    )
    assert r1.status_code == 401

    # Wrong key
    r2 = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 1, "action_title": "Deploy action"},
        headers={"X-API-Key": "invalid-wrong-key", "X-Client-ID": TEST_CLIENT_ID},
    )
    assert r2.status_code == 401


def test_deploy_recommendation_idempotency_replay_and_conflict(client):
    """Verifies idempotency guarantees for recommendation deployment."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
        "Idempotency-Key": "idemp_rec_deploy_test_001",
    }
    payload1 = {
        "action_rank": 2,
        "action_title": "Activate 1-Click Saved UPI Checkout",
        "affected_area": "Checkout Funnel",
        "estimated_revenue_impact_inr": 3200000.0,
    }

    # First attempt: 202 Accepted
    r1 = client.post("/api/v1/recommendations/deploy", json=payload1, headers=headers)
    assert r1.status_code == 202
    d1 = r1.json()

    # Replay with identical payload and key: returns cached 202 with identical job_id
    r2 = client.post("/api/v1/recommendations/deploy", json=payload1, headers=headers)
    assert r2.status_code == 202
    d2 = r2.json()
    assert d2["deployment_id"] == d1["deployment_id"]
    assert d2["job_id"] == d1["job_id"]

    # Conflict with same key but different payload: returns 409 Conflict
    payload_conflict = {
        "action_rank": 3,
        "action_title": "Different Action",
        "affected_area": "Refunds",
    }
    r3 = client.post("/api/v1/recommendations/deploy", json=payload_conflict, headers=headers)
    assert r3.status_code == 409
    assert "conflict" in r3.json()["detail"].lower()


def test_deploy_recommendation_quota_exhaustion_429(client):
    """Verifies quota exhaustion returns HTTP 429 Too Many Requests."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": "merchant_exhausted_quota",
    }

    with patch("backend.security.quotas.InMemoryQuotaManager.check_and_consume_job_quota", return_value=(False, 10, 10)):
        response = client.post(
            "/api/v1/recommendations/deploy",
            json={"action_rank": 1, "action_title": "Test quota limit"},
            headers=headers,
        )
        assert response.status_code == 429
        assert "quota exceeded" in response.json()["detail"].lower()


def test_deploy_recommendation_concurrency_limit_429(client):
    """Verifies tenant concurrent job limit returns HTTP 429."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
    }

    with patch("backend.security.quotas.InMemoryQuotaManager.check_concurrent_job_limit", return_value=(False, 5, 5)):
        response = client.post(
            "/api/v1/recommendations/deploy",
            json={"action_rank": 1, "action_title": "Test concurrency limit"},
            headers=headers,
        )
        assert response.status_code == 429
        assert "concurrent" in response.json()["detail"].lower()


def test_deploy_recommendation_service_draining_503(client):
    """Verifies service unavailable when job runner is draining or stopped."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
    }

    from backend.jobs import JobRunnerDrainingError
    with patch("backend.jobs.JobRunner.submit_job", side_effect=JobRunnerDrainingError("Runner is draining")):
        response = client.post(
            "/api/v1/recommendations/deploy",
            json={"action_rank": 1, "action_title": "Test draining"},
            headers=headers,
        )
        assert response.status_code == 503
        assert "draining" in response.json()["detail"].lower()


def test_deploy_recommendation_invalid_idempotency_key_400(client):
    """Verifies malformed idempotency key returns HTTP 400."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Client-ID": TEST_CLIENT_ID,
        "Idempotency-Key": "invalid key with spaces & symbols !@#$",
    }
    response = client.post(
        "/api/v1/recommendations/deploy",
        json={"action_rank": 1, "action_title": "Valid title here"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "idempotency-key" in response.json()["detail"].lower()


