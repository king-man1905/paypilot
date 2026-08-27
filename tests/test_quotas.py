"""Unit and Integration Test Suite for Phase 21 Tenant Quotas & Concurrency Limits.

Covers:
1. InMemoryQuotaManager analyze and job daily quota consumption.
2. Active concurrent job tracking and bounds checking.
3. Multi-tenant quota isolation (Tenant A quota does not affect Tenant B).
4. HTTP 429 response handling on POST /api/v1/analyze and POST /api/v1/jobs.
5. Observability telemetry and audit events for quota rejections.
"""

import time
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.jobs import get_job_runner, reset_job_runner
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.security.idempotency import reset_idempotency_store
from backend.security.quotas import (
    InMemoryQuotaManager,
    RedisQuotaManager,
    get_quota_manager,
    reset_quota_manager,
    set_quota_manager,
)


@pytest.fixture(autouse=True)
def reset_all_state():
    reset_idempotency_store()
    reset_quota_manager()
    reset_job_runner()
    reset_metrics()
    yield
    reset_idempotency_store()
    reset_quota_manager()
    reset_job_runner()
    reset_metrics()


class TestInMemoryQuotaManager:
    """Tests unit logic of InMemoryQuotaManager."""

    def test_analyze_quota_consumption_and_exhaustion(self):
        mgr = InMemoryQuotaManager()
        tenant = "merchant_corp"

        # Limit to 3 requests
        ok1, cur1, max1 = mgr.check_and_consume_analyze_quota(tenant, limit=3)
        assert ok1 is True
        assert cur1 == 1
        assert max1 == 3

        ok2, cur2, max2 = mgr.check_and_consume_analyze_quota(tenant, limit=3)
        assert ok2 is True
        assert cur2 == 2

        ok3, cur3, max3 = mgr.check_and_consume_analyze_quota(tenant, limit=3)
        assert ok3 is True
        assert cur3 == 3

        # 4th request must be rejected
        ok4, cur4, max4 = mgr.check_and_consume_analyze_quota(tenant, limit=3)
        assert ok4 is False
        assert cur4 == 3
        assert max4 == 3

    def test_job_quota_consumption(self):
        mgr = InMemoryQuotaManager()
        tenant = "fintech_client"

        ok1, cur1, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok1 is True
        assert cur1 == 1

        ok2, cur2, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok2 is True
        assert cur2 == 2

        ok3, _, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok3 is False

    def test_active_concurrent_job_tracking(self):
        mgr = InMemoryQuotaManager()
        tenant = "concurrent_client"

        # Check limit with max 2 concurrent
        assert mgr.check_concurrent_job_limit(tenant, max_concurrent=2)[0] is True

        mgr.record_job_started(tenant)
        assert mgr.check_concurrent_job_limit(tenant, max_concurrent=2)[0] is True

        mgr.record_job_started(tenant)
        # Now 2 active jobs -> 3rd cannot start
        ok3, active, max_act = mgr.check_concurrent_job_limit(tenant, max_concurrent=2)
        assert ok3 is False
        assert active == 2
        assert max_act == 2

        # Finish one job
        mgr.record_job_finished(tenant)
        ok_after, active_after, _ = mgr.check_concurrent_job_limit(tenant, max_concurrent=2)
        assert ok_after is True
        assert active_after == 1

    def test_multi_tenant_isolation(self):
        mgr = InMemoryQuotaManager()
        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        # Exhaust tenant A
        for _ in range(2):
            mgr.check_and_consume_analyze_quota(tenant_a, limit=2)
        assert mgr.check_and_consume_analyze_quota(tenant_a, limit=2)[0] is False

        # Tenant B should be completely unaffected
        ok_b, cur_b, _ = mgr.check_and_consume_analyze_quota(tenant_b, limit=2)
        assert ok_b is True
        assert cur_b == 1


from backend.config import override_settings


class TestQuotaApiIntegration:
    """Tests API endpoints under quota constraints."""

    def test_analyze_quota_exceeded_returns_429(self):
        client = TestClient(app)
        with override_settings(tenant_daily_analyze_quota=2):
            # 1st request -> 200 OK
            r1 = client.post("/api/v1/analyze", json={"query": "Analyze query 1"})
            assert r1.status_code == 200

            # 2nd request -> 200 OK
            r2 = client.post("/api/v1/analyze", json={"query": "Analyze query 2"})
            assert r2.status_code == 200

            # 3rd request -> 429 Quota Exceeded
            r3 = client.post("/api/v1/analyze", json={"query": "Analyze query 3"})
            assert r3.status_code == 429
            assert "Retry-After" in r3.headers
            data = r3.json()
            assert "quota exceeded" in data["detail"].lower()

            metrics = get_metrics_snapshot()
            assert metrics["traffic"]["quota_rejections"] >= 1

    def test_job_quota_exceeded_returns_429(self):
        client = TestClient(app)
        with override_settings(tenant_daily_job_quota=1):
            # 1st job -> 202 Accepted
            r1 = client.post(
                "/api/v1/jobs",
                json={"query": "Diagnostic job 1", "task_type": "merchant_diagnostic"},
            )
            assert r1.status_code == 202

            # 2nd job -> 429 Quota Exceeded
            r2 = client.post(
                "/api/v1/jobs",
                json={"query": "Diagnostic job 2", "task_type": "merchant_diagnostic"},
            )
            assert r2.status_code == 429
            assert "Retry-After" in r2.headers
            assert "quota exceeded" in r2.json()["detail"].lower()

            metrics = get_metrics_snapshot()
            assert metrics["traffic"]["quota_rejections"] >= 1

    def test_concurrent_job_limit_returns_429(self):
        client = TestClient(app)
        mgr = InMemoryQuotaManager()
        set_quota_manager(mgr)

        # Simulate 5 active concurrent jobs
        for _ in range(5):
            mgr.record_job_started("anonymous-dev")

        res = client.post(
            "/api/v1/jobs",
            json={"query": "Another diagnostic job", "task_type": "merchant_diagnostic"},
        )
        assert res.status_code == 429
        assert "active job limit reached" in res.json()["detail"].lower()
        metrics = get_metrics_snapshot()
        assert metrics["traffic"]["concurrency_rejections"] >= 1

    def test_idempotency_replay_does_not_consume_job_quota(self):
        """Hardening 4A: Exact idempotency replays must NOT consume daily job quota."""
        client = TestClient(app)
        headers = {"Idempotency-Key": "quota-safe-replay-key"}
        body = {"query": "Diagnose payment latency", "task_type": "merchant_diagnostic"}

        with override_settings(tenant_daily_job_quota=1):
            # 1st request consumes 1/1 quota
            r1 = client.post("/api/v1/jobs", json=body, headers=headers)
            assert r1.status_code == 202

            # 2nd request is an exact replay -> succeeds (202) instead of getting 429 quota rejection
            r2 = client.post("/api/v1/jobs", json=body, headers=headers)
            assert r2.status_code == 202
            assert r2.json()["job_id"] == r1.json()["job_id"]

    def test_rate_limited_request_does_not_consume_quota(self):
        """Hardening 4B: Requests rejected by rate limiter do not consume daily quota."""
        client = TestClient(app)
        headers = {"X-API-Key": "rate-quota-tenant"}

        with override_settings(job_rate_limit_per_minute=1, tenant_daily_job_quota=10):
            # 1st request -> OK
            r1 = client.post("/api/v1/jobs", json={"query": "Job 1", "task_type": "merchant_diagnostic"}, headers=headers)
            assert r1.status_code == 202

            # 2nd request -> 429 Rate Limit
            r2 = client.post("/api/v1/jobs", json={"query": "Job 2", "task_type": "merchant_diagnostic"}, headers=headers)
            assert r2.status_code == 429
            assert r2.json()["error"]["category"] == "rate_limit_exceeded"

            # Check quota manager counts: only 1 job was counted in total
            mgr = get_quota_manager()
            assert isinstance(mgr, InMemoryQuotaManager)
            total_jobs_consumed = sum(mgr._job_counts.values())
            assert total_jobs_consumed == 1

    def test_daily_quota_rollover_with_day_provider(self):
        """Hardening 4F: Verified daily quota reset occurs across calendar days."""
        current_day = "2026-08-25"
        mgr = InMemoryQuotaManager(day_provider=lambda: current_day)
        tenant = "rollover-tenant"

        # Day 1: Consume all 2 quota
        ok1, cur1, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok1 is True and cur1 == 1

        ok2, cur2, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok2 is True and cur2 == 2

        # Quota full on Day 1
        ok3, cur3, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok3 is False and cur3 == 2

        # Day 2 arrives
        current_day = "2026-08-26"

        # Quota is fresh on Day 2
        ok_new, cur_new, _ = mgr.check_and_consume_job_quota(tenant, limit=2)
        assert ok_new is True and cur_new == 1

    def test_queue_full_rejection_does_not_consume_job_quota(self):
        """Hardening 4C: Jobs rejected due to full queue do not consume daily job quota."""
        from unittest.mock import patch
        from backend.jobs import JobQueueFullError

        client = TestClient(app)
        mgr = InMemoryQuotaManager()
        set_quota_manager(mgr)

        with override_settings(tenant_daily_job_quota=5):
            runner = get_job_runner()
            with patch.object(runner, "submit_job", side_effect=JobQueueFullError("Queue capacity saturated (max 100).")):
                res = client.post(
                    "/api/v1/jobs",
                    json={"query": "Check queue drop", "task_type": "merchant_diagnostic"},
                )
                assert res.status_code == 429
                assert "queue capacity saturated" in res.json()["detail"].lower()

                # Quota should be 0 consumed
                assert mgr._job_counts.get("anonymous-dev", 0) == 0
                assert mgr._active_jobs.get("anonymous-dev", 0) == 0

    def test_failed_job_maintains_daily_quota_and_releases_concurrency(self):
        """Hardening 4E: Failed job execution retains consumed daily submission quota but releases active concurrency."""
        client = TestClient(app)
        mgr = InMemoryQuotaManager()
        set_quota_manager(mgr)

        with override_settings(tenant_daily_job_quota=5, tenant_max_concurrent_jobs=2):
            res = client.post(
                "/api/v1/jobs",
                json={"query": "Test failing job", "task_type": "merchant_diagnostic"},
            )
            assert res.status_code == 202
            job_id = res.json()["job_id"]

            # Quota is 1, active is 1
            assert mgr._job_counts.get("anonymous-dev", 0) == 1
            assert mgr._active_jobs.get("anonymous-dev", 0) == 1

            # Simulate worker completing with failure
            mgr.record_job_finished("anonymous-dev")
            runner = get_job_runner()
            job = runner.store.get_job(job_id)
            assert job is not None
            job.status = "failed"
            job.error = {"message": "Execution timeout"}
            runner.store.update_job(job)

            # Daily quota remains 1 consumed (capacity used)
            assert mgr._job_counts.get("anonymous-dev", 0) == 1
            # Active concurrent jobs released to 0
            assert mgr._active_jobs.get("anonymous-dev", 0) == 0

    def test_redis_quota_manager_fallback(self):
        """Hardening 3: RedisQuotaManager with unavailable Redis falls back cleanly to in-memory."""
        from backend.security.quotas import RedisQuotaManager
        rq = RedisQuotaManager(redis_url="redis://127.0.0.1:9999/0")

        # Analyze quota
        ok, cur, max_q = rq.check_and_consume_analyze_quota("tenant_redis_fb", limit=5)
        assert ok is True and cur == 1

        # Job quota
        ok_j, cur_j, max_j = rq.check_and_consume_job_quota("tenant_redis_fb", limit=3)
        assert ok_j is True and cur_j == 1

        # Concurrent job limits
        ok_c, active, max_c = rq.check_concurrent_job_limit("tenant_redis_fb", max_concurrent=2)
        assert ok_c is True and active == 0

    def test_redis_quota_manager_runtime_exception_fallback(self):
        """Hardening 3: RedisQuotaManager degrades gracefully when Redis client throws runtime exceptions."""
        from unittest.mock import MagicMock
        from backend.security.quotas import RedisQuotaManager

        rq = RedisQuotaManager(redis_url="redis://127.0.0.1:9999/0")
        mock_client = MagicMock()
        mock_client.incr.side_effect = ConnectionError("Redis cluster timeout")
        mock_client.decr.side_effect = ConnectionError("Redis cluster timeout")
        mock_client.get.side_effect = ConnectionError("Redis cluster timeout")
        rq._client = mock_client

        # Should fall back cleanly without raising
        ok, cur, max_q = rq.check_and_consume_analyze_quota("tenant_runtime_exc", limit=3)
        assert ok is True and cur == 1

        ok_j, cur_j, _ = rq.check_and_consume_job_quota("tenant_runtime_exc", limit=3)
        assert ok_j is True and cur_j == 1

        rq.rollback_job_quota("tenant_runtime_exc")
        rq.record_job_started("tenant_runtime_exc")
        rq.record_job_finished("tenant_runtime_exc")

    def test_cross_tenant_quota_api_isolation(self):
        """Hardening 2 & 4: Exhausting Tenant A's analyze quota has zero effect on Tenant B."""
        from backend.security.auth import AuthenticatedUser, require_analyst
        from evaluation.mock_llm import patch_offline_evaluation_llm

        client = TestClient(app)
        user_a = AuthenticatedUser(client_id="quota_tenant_a", role="analyst")
        user_b = AuthenticatedUser(client_id="quota_tenant_b", role="analyst")

        with patch_offline_evaluation_llm(), override_settings(tenant_daily_analyze_quota=1):
            # Tenant A uses their 1 quota
            app.dependency_overrides[require_analyst] = lambda: user_a
            r_a1 = client.post("/api/v1/analyze", json={"query": "Tenant A query 1"})
            assert r_a1.status_code == 200

            # Tenant A is now blocked
            r_a2 = client.post("/api/v1/analyze", json={"query": "Tenant A query 2"})
            assert r_a2.status_code == 429
            assert "quota exceeded" in r_a2.json()["detail"].lower()

            # Tenant B is completely fresh and allowed
            app.dependency_overrides[require_analyst] = lambda: user_b
            r_b1 = client.post("/api/v1/analyze", json={"query": "Tenant B query 1"})
            assert r_b1.status_code == 200

            app.dependency_overrides.clear()


