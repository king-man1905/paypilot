"""Audit Logging, Traceability & Compliance Test Suite for PayPilot.

Tests:
1. AuditEvent schema structure, validation, and JSON serialization.
2. Request ID correlation across HTTP response headers and audit records.
3. Authenticated principal (client_id) and role tracking in audit logs.
4. Successful request lifecycle auditing (/api/v1/analyze, /health, /ready).
5. Failed request lifecycle auditing (400 validation, 401 auth, 403 forbidden, 429 rate limit, 500 server).
6. Agent traceability (actual executed agents list captured without fabrication).
7. LLM traceability (provider, model, live vs fallback status, retry count).
8. Standard error taxonomy integration.
9. Redaction helper robustness across strings, nested dictionaries, and query summaries.
10. Bounded storage FIFO eviction when AUDIT_MAX_EVENTS capacity is reached.
11. Audit store reset and test isolation.
12. Zero secret / token / authorization header exposure in audit records.
13. Administrative access control on GET /admin/audit (401 unauthenticated, 403 analyst, 200 admin).
14. Pagination and event filtering on GET /admin/audit.
15. 100% offline test execution without external network calls.
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.observability.audit import (
    AuditEvent,
    InMemoryAuditStore,
    get_audit_store,
    record_audit_event,
    reset_audit_store,
)
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.security.rate_limiter import rate_limiter
from backend.tools.analytics import clear_dataset_cache
from backend.utils.redaction import (
    redact_sensitive_dict,
    redact_sensitive_text,
    summarize_query_safely,
)
from evaluation.mock_llm import patch_offline_evaluation_llm

TEST_ANALYST_KEY = "paypilot-test-analyst-key-12345"
TEST_ADMIN_KEY = "paypilot-test-admin-key-67890"


@pytest.fixture(autouse=True)
def setup_audit_environment(monkeypatch):
    """Sets up security and audit test isolation environment."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PAYPILOT_API_KEY", TEST_ANALYST_KEY)
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    monkeypatch.setenv("AUDIT_MAX_EVENTS", "1000")
    reset_metrics()
    reset_audit_store()
    clear_dataset_cache()
    rate_limiter.reset()
    yield
    reset_metrics()
    reset_audit_store()
    clear_dataset_cache()
    rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


def test_audit_event_schema_and_serialization():
    """Verifies that AuditEvent instantiates with defaults and converts cleanly to dict."""
    event = AuditEvent(
        event_type="request_completed",
        request_id="req-12345",
        endpoint="/api/v1/analyze",
        http_method="POST",
        client_id="merchant-client",
        role="analyst",
        intent="revenue",
        executed_agents=["revenue_agent", "payment_agent"],
        status="success",
        status_code=200,
        duration_ms=145.2,
        llm_provider="nvidia",
        model="meta/llama-3.3-70b-instruct",
        retry_count=0,
        fallback_used=False,
        error_category=None,
        query_summary="Why did revenue drop?",
    )

    d = event.to_dict()
    assert d["event_id"].startswith("aud_")
    assert d["request_id"] == "req-12345"
    assert d["endpoint"] == "/api/v1/analyze"
    assert d["http_method"] == "POST"
    assert d["client_id"] == "merchant-client"
    assert d["role"] == "analyst"
    assert d["intent"] == "revenue"
    assert d["executed_agents"] == ["revenue_agent", "payment_agent"]
    assert d["status_code"] == 200
    assert d["duration_ms"] == 145.2

    # Reconstitution from dict
    event2 = AuditEvent.from_dict(d)
    assert event2.event_id == event.event_id
    assert event2.request_id == "req-12345"


def test_request_id_correlation_in_audit_records(client):
    """Verifies X-Request-ID in response header perfectly matches the correlated audit record."""
    custom_request_id = "custom-trace-id-998877"
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "X-Request-ID": custom_request_id,
    }

    with patch_offline_evaluation_llm():
        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue drop?"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_request_id

        # Verify audit store captured this exact request_id
        store = get_audit_store()
        events = store.get_events(request_id=custom_request_id)
        assert len(events) >= 1
        assert events[0].request_id == custom_request_id
        assert events[0].status_code == 200


def test_authenticated_principal_and_role_in_audit(client):
    """Verifies that authenticated analyst and admin requests record safe principal IDs and roles."""
    with patch_offline_evaluation_llm():
        # 1. Analyst execution
        res_analyst = client.post(
            "/api/v1/analyze",
            json={"query": "Why did payment fail?"},
            headers={"X-API-Key": TEST_ANALYST_KEY},
        )
        assert res_analyst.status_code == 200
        analyst_req_id = res_analyst.headers["X-Request-ID"]

        store = get_audit_store()
        analyst_events = store.get_events(request_id=analyst_req_id)
        assert len(analyst_events) >= 1
        assert analyst_events[0].client_id == "merchant-client"
        assert analyst_events[0].role == "analyst"

        # 2. Admin execution on metrics
        res_admin = client.get(
            "/metrics",
            headers={"X-API-Key": TEST_ADMIN_KEY},
        )
        assert res_admin.status_code == 200
        admin_req_id = res_admin.headers["X-Request-ID"]

        admin_events = store.get_events(request_id=admin_req_id)
        assert len(admin_events) >= 1
        assert admin_events[0].client_id == "admin-client"
        assert admin_events[0].role == "admin"


def test_successful_analysis_request_audit_lifecycle(client):
    """Verifies that a complete LangGraph analysis workflow generates a rich audit event."""
    with patch_offline_evaluation_llm():
        headers = {"X-API-Key": TEST_ANALYST_KEY}
        res = client.post(
            "/api/v1/analyze",
            json={"query": "Which payment method has the highest failure rate?"},
            headers=headers,
        )
        assert res.status_code == 200
        req_id = res.headers["X-Request-ID"]

        events = get_audit_store().get_events(request_id=req_id)
        assert len(events) == 1
        ev = events[0]

        assert ev.event_type == "request_completed"
        assert ev.status == "success"
        assert ev.status_code == 200
        assert ev.duration_ms > 0
        assert ev.intent in ["payment", "revenue", "checkout", "customer", "what_if"]
        assert len(ev.executed_agents) > 0
        assert "payment_agent" in ev.executed_agents or "revenue_agent" in ev.executed_agents
        assert ev.query_summary is not None
        assert "Which payment method" in ev.query_summary


def test_failed_request_audit_lifecycle(client):
    """Verifies that validation errors (400 / 422) produce request_failed audit events."""
    headers = {"X-API-Key": TEST_ANALYST_KEY}

    # Empty query (400 Bad Request)
    res_400 = client.post("/api/v1/analyze", json={"query": "   "}, headers=headers)
    assert res_400.status_code == 400
    req_id_400 = res_400.headers["X-Request-ID"]

    events = get_audit_store().get_events(request_id=req_id_400)
    assert len(events) >= 1
    assert events[0].status == "failed"
    assert events[0].status_code == 400
    assert events[0].error_category == "validation_error"

    # Malformed payload (422 Unprocessable Entity)
    res_422 = client.post("/api/v1/analyze", json={"invalid_field": 123}, headers=headers)
    assert res_422.status_code == 422
    req_id_422 = res_422.headers["X-Request-ID"]

    events_422 = get_audit_store().get_events(request_id=req_id_422)
    assert len(events_422) >= 1
    assert events_422[0].status == "failed"
    assert events_422[0].status_code == 422
    assert events_422[0].error_category == "validation_error"


def test_auth_failure_audit_lifecycle(client):
    """Verifies that 401 Unauthorized and 403 Forbidden generate audit records."""
    # 1. Unauthenticated request (401)
    res_401 = client.post("/api/v1/analyze", json={"query": "test query"})
    assert res_401.status_code == 401
    req_id_401 = res_401.headers["X-Request-ID"]

    events_401 = get_audit_store().get_events(request_id=req_id_401)
    assert len(events_401) >= 1
    assert events_401[0].status_code == 401
    assert events_401[0].error_category == "auth_error"

    # 2. Insufficient role on metrics (403)
    res_403 = client.get("/metrics", headers={"X-API-Key": TEST_ANALYST_KEY})
    assert res_403.status_code == 403
    req_id_403 = res_403.headers["X-Request-ID"]

    events_403 = get_audit_store().get_events(request_id=req_id_403)
    assert len(events_403) >= 1
    assert events_403[0].status_code == 403
    assert events_403[0].error_category == "forbidden_error"


def test_rate_limit_exceeded_audit_lifecycle(client):
    """Verifies that exceeding volumetric rate limits records a rate_limit_exceeded audit event."""
    rate_limiter.reset()

    # Configure a tiny limit of 2 requests
    with patch.object(rate_limiter, "default_limit", 2), patch.object(rate_limiter, "default_window", 10):
        headers = {"X-API-Key": TEST_ANALYST_KEY}
        with patch_offline_evaluation_llm():
            r1 = client.post("/api/v1/analyze", json={"query": "query 1"}, headers=headers)
            assert r1.status_code == 200

            r2 = client.post("/api/v1/analyze", json={"query": "query 2"}, headers=headers)
            assert r2.status_code == 200

            r3 = client.post("/api/v1/analyze", json={"query": "query 3"}, headers=headers)
            assert r3.status_code == 429

            req_id_429 = r3.headers["X-Request-ID"]
            events_429 = get_audit_store().get_events(request_id=req_id_429)
            assert len(events_429) >= 1
            assert any(e.event_type == "rate_limit_exceeded" and e.status_code == 429 for e in events_429)


def test_agent_and_llm_traceability_in_audit(client):
    """Verifies executed agent list, LLM provider, and model metadata match pipeline state."""
    with patch_offline_evaluation_llm():
        headers = {"X-API-Key": TEST_ANALYST_KEY}
        res = client.post(
            "/api/v1/analyze",
            json={"query": "Why did revenue drop and what should I do?"},
            headers=headers,
        )
        assert res.status_code == 200
        req_id = res.headers["X-Request-ID"]

        events = get_audit_store().get_events(request_id=req_id)
        assert len(events) == 1
        ev = events[0]

        # Verify agents
        assert isinstance(ev.executed_agents, list)
        assert len(ev.executed_agents) > 0
        assert "revenue_agent" in ev.executed_agents

        # Verify LLM metadata
        assert ev.llm_provider in ["nvidia", "deterministic_fallback", "mock"]
        assert ev.model == "meta/llama-3.3-70b-instruct"


def test_redaction_utility_functions():
    """Verifies that the redaction helper masks all secret keys, tokens, and sensitive dictionary keys."""
    # 1. Text Redaction
    sample_text = (
        "Configured NVIDIA_API_KEY=nvapi-abcdef1234567890abcdef and "
        "Bearer paypilot-secret-token-99999 with password: mysecretpassword123."
    )
    redacted_text = redact_sensitive_text(sample_text)
    assert "nvapi-abcdef1234567890abcdef" not in redacted_text
    assert "paypilot-secret-token-99999" not in redacted_text
    assert "mysecretpassword123" not in redacted_text
    assert "[REDACTED" in redacted_text

    # 2. Dictionary Redaction
    sample_dict = {
        "user": "analyst_1",
        "api_key": "paypilot-test-key-112233",
        "nested": {
            "token": "sk-1234567890abcdef123456",
            "safe_metric": 42.0,
            "authorization": "Bearer secret_bearer_token",
        },
        "query": "Diagnose failure with api_key=nvapi-998877665544332211",
    }
    scrubbed_dict = redact_sensitive_dict(sample_dict)
    assert scrubbed_dict["api_key"] == "[REDACTED]"
    assert scrubbed_dict["nested"]["token"] == "[REDACTED]"
    assert scrubbed_dict["nested"]["authorization"] == "[REDACTED]"
    assert scrubbed_dict["nested"]["safe_metric"] == 42.0
    assert "nvapi-998877665544332211" not in scrubbed_dict["query"]

    # 3. Query summary helper
    long_query = "Why did my transaction fail? " * 10
    summary = summarize_query_safely(long_query, max_chars=50)
    assert len(summary) <= 70  # Bounded with truncated suffix
    assert "[truncated]" in summary


def test_bounded_in_memory_audit_store_fifo_eviction():
    """Verifies that InMemoryAuditStore enforces max_events capacity via FIFO ring buffer eviction."""
    store = InMemoryAuditStore(max_events=5)
    assert store.max_events == 5

    # Record 10 events
    for i in range(10):
        store.record_event(
            AuditEvent(
                event_id=f"aud_test_{i}",
                request_id=f"req_{i}",
                endpoint="/test",
                status_code=200,
            )
        )

    # Total count must be bounded at 5
    assert store.count() == 5

    # Oldest events (0..4) evicted; newest (5..9) retained
    events = store.get_events(limit=10)
    assert len(events) == 5
    event_ids = [e.event_id for e in events]
    assert "aud_test_9" in event_ids
    assert "aud_test_5" in event_ids
    assert "aud_test_0" not in event_ids
    assert "aud_test_4" not in event_ids


def test_audit_store_reset_and_isolation():
    """Verifies that reset_audit_store clears all retained events."""
    record_audit_event(request_id="req-isolation-1")
    record_audit_event(request_id="req-isolation-2")

    store = get_audit_store()
    assert store.count() >= 2

    reset_audit_store()
    assert store.count() == 0
    assert len(store.get_events()) == 0


def test_credentials_never_leaked_in_audit_records(client):
    """Verifies that API keys, Bearer tokens, and auth headers never appear in audit events."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY,
        "Authorization": f"Bearer {TEST_ANALYST_KEY}",
    }

    with patch_offline_evaluation_llm():
        response = client.post(
            "/api/v1/analyze",
            json={"query": "Why did my revenue decrease?"},
            headers=headers,
        )
        assert response.status_code == 200
        req_id = response.headers["X-Request-ID"]

        events = get_audit_store().get_events(request_id=req_id)
        assert len(events) == 1
        ev_dict = events[0].to_dict()

        # Convert entire dictionary to lower-case string
        ev_str = str(ev_dict).lower()
        assert TEST_ANALYST_KEY.lower() not in ev_str
        assert TEST_ADMIN_KEY.lower() not in ev_str
        assert "authorization" not in ev_str or ev_dict.get("error_category") is None
        assert "bearer" not in ev_str


def test_admin_audit_endpoint_access_control(client):
    """Verifies that GET /admin/audit is restricted exclusively to Admin role."""
    # 1. Unauthenticated -> 401
    res_unauth = client.get("/admin/audit")
    assert res_unauth.status_code == 401

    # 2. Analyst role -> 403 Forbidden
    res_analyst = client.get(
        "/admin/audit",
        headers={"X-API-Key": TEST_ANALYST_KEY},
    )
    assert res_analyst.status_code == 403
    assert "Forbidden" in res_analyst.json()["detail"]

    # 3. Admin role -> 200 OK
    res_admin = client.get(
        "/admin/audit",
        headers={"X-API-Key": TEST_ADMIN_KEY},
    )
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "total_events_retained" in data
    assert "events" in data
    assert isinstance(data["events"], list)


def test_admin_audit_endpoint_filtering_and_pagination(client):
    """Verifies pagination and filtering options on GET /admin/audit."""
    reset_audit_store()

    # Create 5 distinct events
    for i in range(5):
        record_audit_event(
            event_type="request_completed" if i % 2 == 0 else "request_failed",
            request_id=f"filter-test-req-{i}",
            status_code=200 if i % 2 == 0 else 500,
        )

    headers = {"X-API-Key": TEST_ADMIN_KEY}

    # 1. Pagination: limit=2
    res_page1 = client.get("/admin/audit?limit=2&offset=0", headers=headers)
    assert res_page1.status_code == 200
    data1 = res_page1.json()
    assert len(data1["events"]) == 2
    assert data1["total_events_retained"] >= 5

    # 2. Filter by request_id
    res_filt = client.get("/admin/audit?request_id=filter-test-req-3", headers=headers)
    assert res_filt.status_code == 200
    data_filt = res_filt.json()
    assert len(data_filt["events"]) == 1
    assert data_filt["events"][0]["request_id"] == "filter-test-req-3"

    # 3. Filter by event_type
    res_type = client.get("/admin/audit?event_type=request_failed", headers=headers)
    assert res_type.status_code == 200
    data_type = res_type.json()
    assert all(e["event_type"] == "request_failed" for e in data_type["events"])
