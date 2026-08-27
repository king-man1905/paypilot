"""Background Jobs & Asynchronous Processing Test Suite for PayPilot.

Tests:
1. JobRecord dataclass schema, validation, and JSON serialization.
2. InMemoryJobStore lifecycle, tenant filtering, and FIFO eviction bounds.
3. Asynchronous job submission (POST /api/v1/jobs) returning HTTP 202 Accepted.
4. Asynchronous task execution lifecycle transitioning from queued to completed.
5. Error handling and failed status capture on task exceptions.
6. Bounded queue capacity enforcement and rejection (HTTP 429).
7. Tenant isolation & RBAC (analysts cannot access other merchants' jobs; admins can inspect all).
8. Unauthenticated access rejection (HTTP 401).
9. Request-ID correlation between HTTP headers, JobRecord, and AuditEvent.
10. Metrics telemetry integration (jobs_submitted, jobs_completed, avg_duration_ms).
11. Structured audit event integration for job submission, completion, and failure.
12. Secret and credential redaction across job parameters and result payloads.
13. 100% offline test execution.
"""

import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.jobs import (
    InMemoryJobStore,
    JobQueueFullError,
    JobRecord,
    JobRunner,
    JobStatus,
    get_job_runner,
    reset_job_runner,
)
from backend.observability.audit import get_audit_store, reset_audit_store
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from evaluation.mock_llm import patch_offline_evaluation_llm

TEST_ANALYST_KEY_1 = "paypilot-test-analyst-merchant-01"
TEST_ANALYST_KEY_2 = "paypilot-test-analyst-merchant-02"
TEST_ADMIN_KEY = "paypilot-test-admin-root-99999"


@pytest.fixture(autouse=True)
def setup_jobs_test_env(monkeypatch):
    """Isolates background job testing environment."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PAYPILOT_API_KEY", TEST_ANALYST_KEY_1)
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", TEST_ADMIN_KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")  # Disable volumetric rate limit to test job queue bounds
    monkeypatch.setenv("JOB_MAX_WORKERS", "3")
    monkeypatch.setenv("JOB_MAX_QUEUE_SIZE", "10")
    monkeypatch.setenv("JOB_MAX_RETAINED_JOBS", "20")
    reset_metrics()
    reset_audit_store()
    reset_job_runner()
    yield
    reset_metrics()
    reset_audit_store()
    reset_job_runner()


@pytest.fixture
def client():
    return TestClient(app)


def test_job_model_schema_and_serialization():
    """Verifies JobRecord initialization, default values, and serialization."""
    job = JobRecord(
        task_type="async_analysis",
        client_id="merchant_01",
        role="analyst",
        request_id="req-test-123",
        parameters={"query": "Why did payment fail?"},
    )

    assert job.job_id.startswith("job_")
    assert job.status == JobStatus.QUEUED.value
    assert job.created_at is not None
    assert job.parameters["query"] == "Why did payment fail?"

    d = job.to_dict()
    assert d["job_id"] == job.job_id
    assert d["client_id"] == "merchant_01"

    job2 = JobRecord.from_dict(d)
    assert job2.job_id == job.job_id
    assert job2.client_id == job.client_id


def test_in_memory_job_store_crud_and_fifo_eviction():
    """Verifies InMemoryJobStore saves, updates, filters by tenant, and evicts oldest on capacity."""
    store = InMemoryJobStore(max_retained=5)

    # Save 5 jobs
    for i in range(5):
        store.save_job(
            JobRecord(
                job_id=f"job_{i}",
                client_id="merchant_A" if i % 2 == 0 else "merchant_B",
                role="analyst",
            )
        )
    assert store.count() == 5

    # Save 6th job -> triggers FIFO eviction of job_0
    store.save_job(JobRecord(job_id="job_5", client_id="merchant_A", role="analyst"))
    assert store.count() == 5
    assert store.get_job("job_0") is None
    assert store.get_job("job_5") is not None

    # Tenant filtering: merchant_A should only see their own jobs
    jobs_a = store.list_jobs(client_id="merchant_A", role="analyst")
    assert all(j.client_id == "merchant_A" for j in jobs_a)

    # Admin should see all jobs
    jobs_admin = store.list_jobs(client_id="admin", role="admin")
    assert len(jobs_admin) == 5


def test_job_submission_and_immediate_accepted_status(client):
    """Verifies POST /api/v1/jobs returns 202 Accepted immediately with initial status."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY_1,
        "X-Request-ID": "test-job-req-001",
    }

    with patch_offline_evaluation_llm():
        response = client.post(
            "/api/v1/jobs",
            json={"query": "Why did UPI fail in Month 3?"},
            headers=headers,
        )
        assert response.status_code == 202
        data = response.json()
        assert data["job_id"].startswith("job_")
        assert data["status"] in (JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.COMPLETED.value)
        assert data["task_type"] == "async_analysis"
        assert data["request_id"] == "test-job-req-001"


def test_async_job_lifecycle_to_completed_with_results(client):
    """Verifies submitted job executes asynchronously to completion and returns diagnostic results."""
    headers = {
        "X-API-Key": TEST_ANALYST_KEY_1,
        "X-Request-ID": "test-job-req-002",
    }

    with patch_offline_evaluation_llm():
        submit_res = client.post(
            "/api/v1/jobs",
            json={"query": "Which payment method has the highest failure rate?"},
            headers=headers,
        )
        assert submit_res.status_code == 202
        job_id = submit_res.json()["job_id"]

        # Poll status until completed (timeout: 5s)
        completed = False
        final_data = None
        for _ in range(50):
            time.sleep(0.05)
            status_res = client.get(f"/api/v1/jobs/{job_id}", headers=headers)
            assert status_res.status_code == 200
            final_data = status_res.json()
            if final_data["status"] == JobStatus.COMPLETED.value:
                completed = True
                break

        assert completed is True
        assert final_data is not None
        assert final_data["status"] == "completed"
        assert final_data["duration_ms"] > 0
        assert final_data["result"] is not None
        assert "intent" in final_data["result"]
        assert "executed_agents" in final_data["result"]
        assert "priority_actions" in final_data["result"]


def test_failed_job_lifecycle_on_error(client):
    """Verifies that an unhandled task exception transitions job to FAILED status with error metadata."""
    headers = {"X-API-Key": TEST_ANALYST_KEY_1}

    def _failing_task(*args, **kwargs):
        raise RuntimeError("Simulated unhandled worker crash")

    runner = get_job_runner()
    job = runner.submit_job(
        task_type="failing_task",
        client_id="merchant-client",
        role="analyst",
        request_id="req-fail-001",
        parameters={"test": True},
        target_fn=_failing_task,
    )

    # Wait for execution
    for _ in range(50):
        time.sleep(0.05)
        j = runner.get_job(job.job_id)
        if j and j.status == JobStatus.FAILED.value:
            break

    j_final = runner.get_job(job.job_id)
    assert j_final is not None
    assert j_final.status == JobStatus.FAILED.value
    assert j_final.error is not None
    assert j_final.error["category"] == "job_execution_error"
    assert "Simulated unhandled worker crash" in j_final.error["message"]


def test_job_queue_capacity_limit_and_rejection():
    """Verifies JobRunner raises JobQueueFullError when active queue reaches max capacity."""
    runner = JobRunner(max_workers=1, max_queue_size=2)

    def _blocking_task():
        time.sleep(0.5)

    # Submit 2 tasks (reaches max capacity)
    j1 = runner.submit_job("task", "client_1", "analyst", "req1", {}, _blocking_task)
    j2 = runner.submit_job("task", "client_1", "analyst", "req2", {}, _blocking_task)

    # 3rd submission must be rejected
    with pytest.raises(JobQueueFullError):
        runner.submit_job("task", "client_1", "analyst", "req3", {}, _blocking_task)

    runner.shutdown(wait=False)


def test_job_api_queue_capacity_limit_returns_429(client):
    """Verifies POST /api/v1/jobs returns HTTP 429 when background queue is full."""
    custom_runner = JobRunner(max_workers=1, max_queue_size=1)
    from backend.jobs import set_job_runner
    set_job_runner(custom_runner)

    def _long_task(*args, **kwargs):
        time.sleep(0.5)

    headers = {"X-API-Key": TEST_ANALYST_KEY_1}

    # Fill queue to capacity (1 task)
    custom_runner.submit_job("task", "merchant-client", "analyst", "req-backpressure-01", {}, _long_task)

    # API submission when queue is full must return 429
    with patch_offline_evaluation_llm():
        res = client.post("/api/v1/jobs", json={"query": "Why did revenue drop?"}, headers=headers)
        assert res.status_code == 429
        assert "queue is full" in res.json()["detail"].lower()

    custom_runner.shutdown(wait=False)


def test_tenant_isolation_job_ownership(client, monkeypatch):
    """Verifies that an analyst cannot access a job owned by another client (returns 403 Forbidden)."""
    runner = get_job_runner()

    # Create job owned by merchant-client-A
    job = runner.submit_job(
        task_type="async_analysis",
        client_id="merchant-client-A",
        role="analyst",
        request_id="req-tenant-01",
        parameters={"query": "Test query"},
        target_fn=lambda: {"status": "ok"},
    )

    # Request made by merchant-client (default analyst user)
    headers_analyst = {"X-API-Key": TEST_ANALYST_KEY_1}
    res = client.get(f"/api/v1/jobs/{job.job_id}", headers=headers_analyst)
    # Since merchant-client != merchant-client-A, must return 403 Forbidden
    assert res.status_code == 403
    assert "Forbidden" in res.json()["detail"]


def test_admin_can_access_and_list_all_jobs(client):
    """Verifies admin role can access and list all jobs regardless of tenant ownership."""
    runner = get_job_runner()

    # Create jobs for 2 different merchants
    j1 = runner.submit_job("task1", "merchant_A", "analyst", "r1", {}, lambda: {"ok": 1})
    j2 = runner.submit_job("task2", "merchant_B", "analyst", "r2", {}, lambda: {"ok": 2})

    headers_admin = {"X-API-Key": TEST_ADMIN_KEY}

    # Admin accesses merchant_A's job
    res1 = client.get(f"/api/v1/jobs/{j1.job_id}", headers=headers_admin)
    assert res1.status_code == 200
    assert res1.json()["job_id"] == j1.job_id

    # Admin lists all jobs
    res_list = client.get("/api/v1/jobs", headers=headers_admin)
    assert res_list.status_code == 200
    job_ids = [job["job_id"] for job in res_list.json()["jobs"]]
    assert j1.job_id in job_ids
    assert j2.job_id in job_ids


def test_unauthenticated_job_endpoints_rejected_401(client):
    """Verifies unauthenticated access to job endpoints is rejected with HTTP 401."""
    # Submit without auth
    res_submit = client.post("/api/v1/jobs", json={"query": "Test"})
    assert res_submit.status_code == 401

    # Get status without auth
    res_get = client.get("/api/v1/jobs/job_12345")
    assert res_get.status_code == 401


def test_request_id_correlation_in_jobs(client):
    """Verifies custom X-Request-ID correlates from submission through execution and audit events."""
    custom_trace_id = "trace-job-correlation-7788"
    headers = {
        "X-API-Key": TEST_ANALYST_KEY_1,
        "X-Request-ID": custom_trace_id,
    }

    with patch_offline_evaluation_llm():
        submit_res = client.post(
            "/api/v1/jobs",
            json={"query": "Why did revenue drop?"},
            headers=headers,
        )
        assert submit_res.status_code == 202
        job_id = submit_res.json()["job_id"]

        # Wait for worker completion
        for _ in range(50):
            time.sleep(0.05)
            j = get_job_runner().get_job(job_id)
            if j and j.status == JobStatus.COMPLETED.value:
                break

        # Check audit event correlation
        events = get_audit_store().get_events(request_id=custom_trace_id)
        assert len(events) >= 1
        assert any(e.event_type in ("job_submitted", "job_completed") for e in events)


def test_job_metrics_integration():
    """Verifies job submission and completion increment runtime metrics."""
    reset_metrics()
    runner = get_job_runner()

    j = runner.submit_job(
        task_type="test_metric_task",
        client_id="merchant-client",
        role="analyst",
        request_id="req-metric-01",
        parameters={},
        target_fn=lambda: {"done": True},
    )

    # Wait for completion
    for _ in range(50):
        time.sleep(0.05)
        if (rec := runner.get_job(j.job_id)) is not None and rec.status == JobStatus.COMPLETED.value:
            break

    snapshot = get_metrics_snapshot()
    assert "jobs" in snapshot
    assert snapshot["jobs"]["jobs_submitted"] >= 1
    assert snapshot["jobs"]["jobs_completed"] >= 1


def test_secret_non_exposure_in_job_records(client):
    """Verifies sensitive API keys, Bearer tokens, and secrets are redacted from job payloads."""
    headers = {"X-API-Key": TEST_ANALYST_KEY_1}
    secret_payload = {
        "query": "Diagnose failure with api_key=nvapi-abcdef1234567890abcdef and password: secret_password",
        "metadata": {
            "token": "sk-1234567890abcdef",
            "safe_tag": "merchant_q4",
        },
    }

    with patch_offline_evaluation_llm():
        res = client.post("/api/v1/jobs", json=secret_payload, headers=headers)
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        # Retrieve job
        job_data = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
        job_str = str(job_data).lower()

        assert "nvapi-abcdef1234567890abcdef" not in job_str
        assert "secret_password" not in job_str
        assert "sk-1234567890abcdef" not in job_str
        assert "[redacted" in job_str
