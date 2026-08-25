"""Unit and Integration Tests for Production Deployment, Lifecycle & Graceful Shutdown (Phase 22).

Tests:
1. JobRunner lifecycle states (RUNNING -> DRAINING -> STOPPED).
2. JobRunner drain behavior with in-flight tasks and bounded timeout.
3. Readiness probe behavior during shutdown and draining states.
4. Liveness health probe behavior during shutdown.
5. Job API 503 Service Unavailable behavior during runner draining.
6. Database migration idempotency and safe execution.
7. Shutdown timeout and resource configuration validations.
8. Graceful shutdown teardown sequence execution.
"""

import asyncio
import os
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi import status
from fastapi.testclient import TestClient

from backend.api.main import app, execute_graceful_shutdown, is_shutting_down, set_shutting_down
from backend.config import ConfigValidationError, PayPilotSettings, get_shutdown_timeout_seconds
from backend.jobs import (
    JobQueueFullError,
    JobRecord,
    JobRunner,
    JobRunnerDrainingError,
    JobRunnerStoppedError,
    JobStatus,
    RunnerState,
    get_job_runner,
    reset_job_runner,
    set_job_runner,
)
from backend.storage.migrator import run_migrations, seed_database_from_csv


@pytest.fixture(autouse=True)
def cleanup_deployment_state():
    """Ensures clean shutdown and runner state before and after every test."""
    set_shutting_down(False)
    reset_job_runner()
    yield
    set_shutting_down(False)
    reset_job_runner()


class TestJobRunnerLifecycleAndDrain:
    """Validates JobRunner state transitions, draining semantics, and error handling."""

    def test_job_runner_initial_state_is_running(self):
        runner = JobRunner()
        assert runner.state == RunnerState.RUNNING
        assert runner.is_running is True
        assert runner.is_draining is False
        assert runner.is_stopped is False
        assert runner.active_job_count == 0
        runner.stop(wait=False)

    def test_job_runner_drain_transitions_to_stopped(self):
        runner = JobRunner()
        summary = runner.drain(timeout_seconds=0.5)

        assert summary["status"] == RunnerState.STOPPED.value
        assert summary["drained_cleanly"] is True
        assert summary["active_jobs_remaining"] == 0
        assert runner.is_stopped is True
        assert runner.is_running is False

    def test_submit_job_rejected_when_draining(self):
        runner = JobRunner()
        runner._state = RunnerState.DRAINING

        with pytest.raises(JobRunnerDrainingError) as exc_info:
            runner.submit_job(
                task_type="diagnostic_analysis",
                client_id="tenant_alpha",
                role="analyst",
                request_id="req_drain_1",
                parameters={"query": "test query"},
                target_fn=lambda **kwargs: {"ok": True},
            )
        assert "draining" in str(exc_info.value).lower()
        runner.stop(wait=False)

    def test_submit_job_rejected_when_stopped(self):
        runner = JobRunner()
        runner.stop(wait=False)
        assert runner.is_stopped is True

        with pytest.raises(JobRunnerStoppedError) as exc_info:
            runner.submit_job(
                task_type="diagnostic_analysis",
                client_id="tenant_alpha",
                role="analyst",
                request_id="req_stop_1",
                parameters={"query": "test query"},
                target_fn=lambda **kwargs: {"ok": True},
            )
        assert "stopped" in str(exc_info.value).lower()

    def test_job_runner_drain_completes_in_flight_tasks(self):
        runner = JobRunner()

        def slow_task(duration: float = 0.1):
            time.sleep(duration)
            return {"result": "success"}

        job = runner.submit_job(
            task_type="diagnostic_analysis",
            client_id="tenant_alpha",
            role="analyst",
            request_id="req_in_flight_1",
            parameters={"query": "slow task test"},
            target_fn=slow_task,
            duration=0.1,
        )

        assert runner.active_job_count >= 1

        # Drain with sufficient timeout for task to finish
        drain_res = runner.drain(timeout_seconds=2.0)

        assert drain_res["drained_cleanly"] is True
        assert drain_res["active_jobs_remaining"] == 0
        assert runner.is_stopped is True

        fetched_job = runner.get_job(job.job_id)
        assert fetched_job is not None
        assert fetched_job.status == JobStatus.COMPLETED.value
        assert fetched_job.result == {"result": "success"}


class TestProbeLifecycles:
    """Validates /health and /ready probe semantics during application lifecycle and shutdown."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        set_shutting_down(False)
        runner = get_job_runner()
        runner._state = RunnerState.RUNNING
        yield
        set_shutting_down(False)
        runner._state = RunnerState.RUNNING

    def test_health_probe_always_succeeds(self):
        client = TestClient(app)
        # Normal state
        resp = client.get("/health")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "paypilot"

        # During shutdown state, health (liveness) still returns 200
        set_shutting_down(True)
        resp_during_shutdown = client.get("/health")
        assert resp_during_shutdown.status_code == status.HTTP_200_OK

    def test_readiness_probe_returns_200_when_healthy(self):
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["dataset_accessible"] is True
        assert data["checks"]["analytics_engine_ready"] is True
        assert data["checks"]["accepting_traffic"] is True

    def test_readiness_probe_returns_503_when_shutting_down(self):
        client = TestClient(app)
        set_shutting_down(True)

        resp = client.get("/ready")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "shutting down" in resp.text.lower()
        assert resp.headers.get("Retry-After") == "15"

    def test_readiness_probe_returns_503_when_runner_is_draining(self):
        client = TestClient(app)
        runner = get_job_runner()
        runner._state = RunnerState.DRAINING

        resp = client.get("/ready")
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "draining" in resp.text.lower()
        assert resp.headers.get("Retry-After") == "15"


class TestJobAPIGracefulShutdownRejections:
    """Validates HTTP API behavior when submitting jobs to a draining or stopped server."""

    @pytest.fixture(autouse=True)
    def reset_state(self):
        set_shutting_down(False)
        runner = get_job_runner()
        runner._state = RunnerState.RUNNING
        yield
        set_shutting_down(False)
        runner._state = RunnerState.RUNNING

    def test_job_submission_returns_503_when_runner_draining(self):
        client = TestClient(app)
        runner = get_job_runner()
        runner._state = RunnerState.DRAINING

        resp = client.post(
            "/api/v1/jobs",
            json={"query": "Analyze checkout failure spike in US region"},
            headers={"X-Client-Id": "tenant_test_drain"},
        )
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "draining" in resp.text.lower()
        assert resp.headers.get("Retry-After") == "15"

    def test_job_submission_returns_503_when_runner_stopped(self):
        client = TestClient(app)
        runner = get_job_runner()
        runner.stop(wait=False)

        resp = client.post(
            "/api/v1/jobs",
            json={"query": "Analyze checkout failure spike in US region"},
            headers={"X-Client-Id": "tenant_test_stop"},
        )
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "stopped" in resp.text.lower()


class TestDatabaseMigrationLifecycle:
    """Validates safe and idempotent database schema creation and seeding."""

    def test_run_migrations_idempotency(self, tmp_path):
        db_file = tmp_path / "test_migration.db"
        db_url = f"sqlite:///{db_file}"

        # 1st Migration run
        res1 = run_migrations(database_url=db_url, auto_seed=True)
        assert res1["status"] == "success"
        assert res1["transactions_count"] > 0
        assert "merchant_transactions" in res1["schemas_created"]

        # 2nd Migration run (idempotent no-op)
        res2 = run_migrations(database_url=db_url, auto_seed=True)
        assert res2["status"] == "success"
        assert res2["transactions_count"] == res1["transactions_count"]
        assert res2["seeded"] is False  # Already seeded, skipped


class TestDeploymentConfigurationAndTeardown:
    """Validates deployment settings bounds and graceful shutdown execution."""

    def test_shutdown_timeout_validation(self):
        settings = PayPilotSettings()
        settings.shutdown_timeout_seconds = 0.5
        with pytest.raises(ConfigValidationError) as exc:
            settings.validate()
        assert "SHUTDOWN_TIMEOUT_SECONDS" in str(exc.value)

        settings.shutdown_timeout_seconds = 500.0
        with pytest.raises(ConfigValidationError) as exc:
            settings.validate()
        assert "SHUTDOWN_TIMEOUT_SECONDS" in str(exc.value)

        settings.shutdown_timeout_seconds = 20.0
        settings.validate()  # Should succeed

    def test_execute_graceful_shutdown_sequence(self):
        loop = asyncio.new_event_loop()
        try:
            summary = loop.run_until_complete(execute_graceful_shutdown(timeout_seconds=0.2))
            assert summary["is_shutting_down"] is True
            assert summary["duration_ms"] >= 0.0
            assert is_shutting_down() is True
        finally:
            loop.close()
