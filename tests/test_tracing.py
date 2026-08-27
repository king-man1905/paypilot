"""Comprehensive Unit and Integration Tests for Distributed Tracing (Phase 19).

Tests trace context propagation, hierarchical span tracking, background job correlation,
bounded in-memory retention, error span closure, secret redaction, and admin trace endpoints.
"""

import os
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.jobs import get_job_runner, reset_job_runner
from backend.observability.tracing import (
    InMemoryTraceStore,
    SpanRecord,
    TraceContext,
    get_current_trace_context,
    get_trace_store,
    reset_trace_context,
    reset_trace_store,
    set_current_trace_context,
    trace_span,
)


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Resets global trace store and job runner before and after each test."""
    reset_trace_store()
    reset_job_runner()
    yield
    reset_trace_store()
    reset_job_runner()


def test_trace_context_and_child_derivation():
    """Tests TraceContext instantiation and hierarchical child span derivation."""
    parent = TraceContext(trace_id="tr_parent_123", request_id="req_456")
    assert parent.trace_id == "tr_parent_123"
    assert parent.request_id == "req_456"
    assert parent.parent_span_id is None
    assert parent.span_id.startswith("sp_")

    child = parent.create_child()
    assert child.trace_id == "tr_parent_123"
    assert child.request_id == "req_456"
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id


def test_trace_context_propagation_via_contextvars():
    """Tests async/thread-safe trace context setting, retrieval, and resetting."""
    assert get_current_trace_context() is None

    ctx = TraceContext(trace_id="tr_custom_test", request_id="req_test")
    token = set_current_trace_context(ctx)

    active = get_current_trace_context()
    assert active is not None
    assert active.trace_id == "tr_custom_test"

    reset_trace_context(token)
    assert get_current_trace_context() is None


def test_trace_span_lifecycle_and_timing():
    """Tests that trace_span context manager correctly captures timings and persists to store."""
    store = get_trace_store()

    with trace_span("test.operation", component="test_comp", metadata={"key": "value"}) as span:
        assert span.operation_name == "test.operation"
        assert span.component == "test_comp"
        assert span.status == "OK"
        time.sleep(0.01)

    assert span.duration_ms is not None
    assert span.duration_ms >= 5.0
    assert span.end_time is not None

    recorded = store.get_trace(span.trace_id)
    assert recorded is not None
    assert len(recorded) == 1
    assert recorded[0].span_id == span.span_id
    assert recorded[0].metadata["key"] == "value"


def test_trace_span_error_closure_and_no_orphans():
    """Tests that trace_span reliably closes with ERROR status when an exception occurs."""
    store = get_trace_store()

    with pytest.raises(ValueError, match="simulated failure"):
        with trace_span("faulty.operation", component="error_comp"):
            raise ValueError("simulated failure")

    # Verify span was closed and recorded despite exception
    traces = store.list_traces()
    assert len(traces) == 1
    assert traces[0]["status"] == "ERROR"

    recorded = store.get_trace(traces[0]["trace_id"])
    assert recorded is not None
    assert len(recorded) == 1
    assert recorded[0].status == "ERROR"
    assert recorded[0].error_category == "ValueError"
    assert recorded[0].error_message is not None
    assert "simulated failure" in recorded[0].error_message


def test_parent_child_span_hierarchy_across_graph():
    """Tests that nested trace_span calls maintain consistent trace_id and correct parent_span_ids."""
    store = get_trace_store()

    with trace_span("root.http", component="http") as root_span:
        with trace_span("agent.supervisor", component="supervisor") as sup_span:
            with trace_span("agent.payment", component="payment_agent") as pay_span:
                with trace_span("llm.generate", component="llm") as llm_span:
                    pass

    spans = store.get_trace(root_span.trace_id)
    assert spans is not None
    assert len(spans) == 4

    # All spans must share identical trace_id
    assert all(s.trace_id == root_span.trace_id for s in spans)

    # Validate parent-child chain
    span_map = {s.operation_name: s for s in spans}
    assert span_map["root.http"].parent_span_id is None
    assert span_map["agent.supervisor"].parent_span_id == span_map["root.http"].span_id
    assert span_map["agent.payment"].parent_span_id == span_map["agent.supervisor"].span_id
    assert span_map["llm.generate"].parent_span_id == span_map["agent.payment"].span_id


def test_in_memory_trace_store_bounded_retention_and_eviction():
    """Tests that InMemoryTraceStore respects max_events and max_traces bounds with FIFO eviction."""
    bounded_store = InMemoryTraceStore(max_events=10, max_traces=3)

    # Insert 3 traces with 2 spans each (6 spans total)
    for i in range(3):
        t_id = f"tr_batch_{i}"
        for j in range(2):
            bounded_store.record_span(
                SpanRecord(
                    trace_id=t_id,
                    span_id=f"sp_{i}_{j}",
                    operation_name=f"op_{j}",
                    component="test",
                )
            )

    assert bounded_store.get_total_spans_count() == 6
    assert len(bounded_store.list_traces()) == 3

    # Insert 4th trace -> oldest trace (tr_batch_0) should be evicted
    bounded_store.record_span(
        SpanRecord(
            trace_id="tr_batch_3",
            span_id="sp_3_0",
            operation_name="op_0",
            component="test",
        )
    )

    assert bounded_store.get_trace("tr_batch_0") is None  # Evicted
    assert bounded_store.get_trace("tr_batch_1") is not None
    assert bounded_store.get_trace("tr_batch_3") is not None


def test_secret_redaction_in_span_records():
    """Tests that API keys, passwords, and tokens are scrubbed from span metadata and errors."""
    store = get_trace_store()

    sensitive_meta = {
        "api_key": "nvapi-secret-123456789",
        "nested": {"token": "Bearer secret_jwt_token_999"},
        "clean_field": "public_data",
    }

    target_trace_id = None
    with pytest.raises(RuntimeError):
        with trace_span("auth.check", component="security", metadata=sensitive_meta) as s:
            target_trace_id = s.trace_id
            raise RuntimeError("Database connection to postgresql://user:my_secret_pw@host/db failed")

    assert target_trace_id is not None
    spans = store.get_trace(target_trace_id)
    assert spans is not None

    dict_repr = spans[0].to_dict()
    serialized = str(dict_repr)
    assert "nvapi-secret" not in serialized
    assert "my_secret_pw" not in serialized
    assert "secret_jwt_token" not in serialized
    assert "[REDACTED_API_KEY]" in serialized or "[REDACTED]" in serialized or "user:***@" in serialized


def test_http_middleware_trace_headers_and_propagation():
    """Tests that HTTP requests return X-Trace-ID and X-Request-ID headers and record root span."""
    client = TestClient(app)
    custom_trace_id = "tr_client_provided_123"

    res = client.get("/health", headers={"X-Trace-ID": custom_trace_id, "X-Request-ID": "req_cust_999"})
    assert res.status_code == 200
    assert res.headers.get("X-Trace-ID") == custom_trace_id
    assert res.headers.get("X-Request-ID") == "req_cust_999"

    store = get_trace_store()
    spans = store.get_trace(custom_trace_id)
    assert spans is not None
    assert len(spans) >= 1
    assert spans[0].operation_name == "http.request"
    assert spans[0].metadata.get("endpoint") == "/health"


def test_analyze_pipeline_end_to_end_trace_tree():
    """Tests that POST /api/v1/analyze generates a complete span hierarchy with matching trace_id."""
    client = TestClient(app)
    headers = {"X-API-Key": "test-analyst-key", "X-Trace-ID": "tr_e2e_analyze_test"}

    os.environ["PAYPILOT_API_KEY"] = "test-analyst-key"
    try:
        res = client.post(
            "/api/v1/analyze",
            json={"query": "Which payment method has the highest failure rate?"},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["execution_metadata"]["trace_id"] == "tr_e2e_analyze_test"

        store = get_trace_store()
        spans = store.get_trace("tr_e2e_analyze_test")
        assert spans is not None
        assert len(spans) >= 3  # http.request, supervisor, payment, aggregator, recovery

        op_names = [s.operation_name for s in spans]
        assert "http.request" in op_names
        assert "agent.supervisor" in op_names
        assert "agent.payment" in op_names
        assert "agent.recovery" in op_names
    finally:
        os.environ.pop("PAYPILOT_API_KEY", None)


def test_background_job_trace_propagation():
    """Tests that asynchronous background jobs preserve trace_id and record child spans."""
    client = TestClient(app)
    headers = {"X-API-Key": "test-analyst-key", "X-Trace-ID": "tr_job_e2e_test"}
    os.environ["PAYPILOT_API_KEY"] = "test-analyst-key"

    try:
        res = client.post(
            "/api/v1/jobs",
            json={"query": "Why did revenue drop?"},
            headers=headers,
        )
        assert res.status_code == 202
        job_data = res.json()
        assert job_data["trace_id"] == "tr_job_e2e_test"

        # Wait for worker execution
        runner = get_job_runner()
        for _ in range(50):
            j = runner.get_job(job_data["job_id"])
            if j and j.status in ("completed", "failed"):
                break
            time.sleep(0.05)

        store = get_trace_store()
        spans = store.get_trace("tr_job_e2e_test")
        assert spans is not None
        op_names = [s.operation_name for s in spans]
        assert "http.request" in op_names
        assert "job.execute" in op_names
    finally:
        os.environ.pop("PAYPILOT_API_KEY", None)


def test_admin_traces_endpoint_security_and_retrieval():
    """Tests GET /admin/traces/{trace_id} authorization and structured response."""
    client = TestClient(app)
    os.environ["PAYPILOT_ADMIN_KEY"] = "admin-secret-key"
    os.environ["PAYPILOT_API_KEY"] = "analyst-key"

    try:
        # 1. Unauthenticated request -> 401
        res = client.get("/admin/traces/tr_sample_123")
        assert res.status_code == 401

        # 2. Analyst role request -> 403
        res = client.get("/admin/traces/tr_sample_123", headers={"X-API-Key": "analyst-key"})
        assert res.status_code == 403

        # 3. Admin request for non-existent trace -> 404
        res = client.get("/admin/traces/tr_non_existent", headers={"X-API-Key": "admin-secret-key"})
        assert res.status_code == 404

        # 4. Admin request for existing trace -> 200 with span tree
        store = get_trace_store()
        with trace_span("admin.test.root", component="test") as span:
            with trace_span("admin.test.child", component="test"):
                pass

        res = client.get(f"/admin/traces/{span.trace_id}", headers={"X-API-Key": "admin-secret-key"})
        assert res.status_code == 200
        data = res.json()
        assert data["trace_id"] == span.trace_id
        assert data["span_count"] == 2
        assert len(data["spans"]) == 2
        assert data["status"] == "OK"
    finally:
        os.environ.pop("PAYPILOT_ADMIN_KEY", None)
        os.environ.pop("PAYPILOT_API_KEY", None)

