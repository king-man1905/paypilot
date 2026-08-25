"""Unit and Integration Test Suite for Phase 21 Traffic Management & Protection.

Covers:
1. Volumetric sliding-window rate limiting on /api/v1/analyze and /api/v1/jobs.
2. HTTP 429 Too Many Requests response structure and Retry-After header.
3. IP vs API-Key tenant separation.
4. Request size protection (empty queries, oversized queries > MAX_QUERY_LENGTH).
5. Observability telemetry: rate limit rejections, error metrics, and audit trail events.
"""

import time
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.jobs import JobQueueFullError, get_job_runner, reset_job_runner
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.security.idempotency import reset_idempotency_store
from backend.security.quotas import reset_quota_manager
from backend.security.rate_limiter import InMemoryRateLimiter, RedisRateLimiter, reset_rate_limiter, set_rate_limiter
from backend.observability.audit import get_audit_store


@pytest.fixture(autouse=True)
def reset_all_state():
    reset_idempotency_store()
    reset_quota_manager()
    reset_rate_limiter()
    reset_job_runner()
    reset_metrics()
    yield
    reset_idempotency_store()
    reset_quota_manager()
    reset_rate_limiter()
    reset_job_runner()
    reset_metrics()


from backend.config import override_settings


class TestRateLimitingAndBackpressure:
    """Tests rate limiter middleware and volumetric throttling."""

    def test_volumetric_rate_limit_exceeded_returns_429(self):
        client = TestClient(app)
        headers = {"X-API-Key": "test-rate-key-1"}

        with override_settings(job_rate_limit_per_minute=2):
            # First request -> OK
            res1 = client.post("/api/v1/jobs", json={"query": "Test query 1", "task_type": "merchant_diagnostic"}, headers=headers)
            assert res1.status_code == 202

            # Second request -> OK
            res2 = client.post("/api/v1/jobs", json={"query": "Test query 2", "task_type": "merchant_diagnostic"}, headers=headers)
            assert res2.status_code == 202

            # Third request -> 429 Rate Limit Exceeded
            res3 = client.post("/api/v1/jobs", json={"query": "Test query 3", "task_type": "merchant_diagnostic"}, headers=headers)
            assert res3.status_code == 429
            assert "Retry-After" in res3.headers
            data = res3.json()
            assert data["error"]["code"] == 429
            assert data["error"]["category"] == "rate_limit_exceeded"

            metrics = get_metrics_snapshot()
            assert metrics["traffic"]["rate_limit_rejections"] >= 1

    def test_rate_limiter_tenant_isolation(self):
        client = TestClient(app)

        with override_settings(job_rate_limit_per_minute=1):
            # Tenant 1 consumes their limit
            res1 = client.post("/api/v1/jobs", json={"query": "Tenant 1 query", "task_type": "merchant_diagnostic"}, headers={"X-API-Key": "tenant-one-key"})
            assert res1.status_code == 202

            # Tenant 1 is now rate limited
            res1_blocked = client.post("/api/v1/jobs", json={"query": "Tenant 1 query again", "task_type": "merchant_diagnostic"}, headers={"X-API-Key": "tenant-one-key"})
            assert res1_blocked.status_code == 429

            # Tenant 2 has a clean limit and is allowed
            res2 = client.post("/api/v1/jobs", json={"query": "Tenant 2 query", "task_type": "merchant_diagnostic"}, headers={"X-API-Key": "tenant-two-key"})
            assert res2.status_code == 202

    def test_exempt_paths_not_rate_limited(self):
        client = TestClient(app)
        limiter = InMemoryRateLimiter(default_limit=0, default_window=60)
        set_rate_limiter(limiter)

        # Health and readiness endpoints should succeed despite 0 limit on general routes
        res = client.get("/health")
        assert res.status_code == 200

        res_ready = client.get("/ready")
        assert res_ready.status_code == 200

        res_docs = client.get("/docs")
        assert res_docs.status_code == 200

        res_openapi = client.get("/openapi.json")
        assert res_openapi.status_code == 200

    def test_sliding_window_eviction_and_boundary_conditions(self):
        """Hardening 5: Sliding window cleanly evicts timestamps outside window without leakage."""
        limiter = InMemoryRateLimiter(default_limit=2, default_window=1)

        allowed1, _ = limiter.is_allowed("tenant_slide", limit=2, window=1)
        assert allowed1 is True

        allowed2, _ = limiter.is_allowed("tenant_slide", limit=2, window=1)
        assert allowed2 is True

        # 3rd is blocked
        allowed3, retry3 = limiter.is_allowed("tenant_slide", limit=2, window=1)
        assert allowed3 is False
        assert retry3 >= 1

        # Wait for 1-second window to expire
        time.sleep(1.05)

        # Should be allowed again after window passes
        allowed_after, _ = limiter.is_allowed("tenant_slide", limit=2, window=1)
        assert allowed_after is True

    def test_observability_traffic_counters_single_increment(self):
        """Hardening 7: Verify each traffic event increments the specific counter exactly once."""
        from backend.observability.metrics import (
            record_rate_limit_rejection,
            record_quota_rejection,
            record_concurrency_rejection,
            record_queue_full_rejection,
            record_idempotency_replay,
            record_idempotency_conflict,
            record_overload_rejection,
        )

        reset_metrics()

        record_rate_limit_rejection()
        record_quota_rejection()
        record_concurrency_rejection()
        record_queue_full_rejection()
        record_idempotency_replay()
        record_idempotency_conflict()
        record_overload_rejection()

        metrics = get_metrics_snapshot()
        t = metrics["traffic"]
        assert t["rate_limit_rejections"] == 1
        assert t["quota_rejections"] == 1
        assert t["concurrency_rejections"] == 1
        assert t["queue_full_rejections"] == 1
        assert t["idempotency_replays"] == 1
        assert t["idempotency_conflicts"] == 1
        assert t["overload_rejections"] == 1


class TestRequestSizeProtection:
    """Tests payload size bounds and rejection."""

    def test_empty_query_rejection_analyze(self):
        client = TestClient(app)
        res = client.post("/api/v1/analyze", json={"query": "   "})
        assert res.status_code in (400, 422)

    def test_oversized_query_rejection_analyze(self):
        client = TestClient(app)
        huge_query = "Why did payments drop? " * 100  # > 1000 characters
        res = client.post("/api/v1/analyze", json={"query": huge_query})
        assert res.status_code in (400, 422)

    def test_empty_query_rejection_jobs(self):
        client = TestClient(app)
        res = client.post("/api/v1/jobs", json={"query": "", "task_type": "merchant_diagnostic"})
        assert res.status_code in (400, 422)

    def test_oversized_query_rejection_jobs(self):
        client = TestClient(app)
        huge_query = "Diagnose failures " * 100
        res = client.post("/api/v1/jobs", json={"query": huge_query, "task_type": "merchant_diagnostic"})
        assert res.status_code in (400, 422)


class TestTrafficSecurityAndFallback:
    """Security and fallback hardening tests for traffic control layer."""

    def test_redis_rate_limiter_fallback_when_unavailable(self):
        """Hardening 3: RedisRateLimiter degrades safely to in-memory on connection drop."""
        redis_limiter = RedisRateLimiter(redis_url="redis://127.0.0.1:9999/0")
        set_rate_limiter(redis_limiter)

        allowed1, _ = redis_limiter.is_allowed("tenant_fallback_test", limit=2, window=60)
        assert allowed1 is True

        allowed2, _ = redis_limiter.is_allowed("tenant_fallback_test", limit=2, window=60)
        assert allowed2 is True

        # 3rd request hits local fallback limit
        allowed3, retry_after = redis_limiter.is_allowed("tenant_fallback_test", limit=2, window=60)
        assert allowed3 is False
        assert retry_after >= 1

    def test_audit_event_sanitization_no_secrets(self):
        """Hardening 7: Verify traffic rejection audit events never expose raw secrets or passwords."""
        from backend.observability.audit import get_audit_store

        client = TestClient(app)
        canary_secret = "nvapi-SECRET-CANARY-TRAFFIC-999"

        # Trigger rate limit rejection with secret header
        with override_settings(job_rate_limit_per_minute=1):
            client.post("/api/v1/jobs", json={"query": "Job 1", "task_type": "merchant_diagnostic"}, headers={"X-API-Key": canary_secret})
            res_blocked = client.post("/api/v1/jobs", json={"query": "Job 2", "task_type": "merchant_diagnostic"}, headers={"X-API-Key": canary_secret})
            assert res_blocked.status_code == 429

        # Inspect audit events
        audit_store = get_audit_store()
        events = audit_store.get_events(limit=50)

        for ev in events:
            # Serialized string representation of audit event
            dumped = str(ev.to_dict())
            assert canary_secret not in dumped

    def test_redis_rate_limiter_runtime_exception_fallback(self):
        """Hardening 3: RedisRateLimiter degrades cleanly when pipeline or redis calls throw ConnectionError."""
        from unittest.mock import MagicMock

        redis_limiter = RedisRateLimiter(redis_url="redis://127.0.0.1:9999/0")
        mock_client = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.side_effect = ConnectionError("Redis host unreachable")
        mock_client.pipeline.return_value = mock_pipe
        redis_limiter._client = mock_client

        # Fast fallback to in-memory limiter without unhandled error
        allowed, retry_after = redis_limiter.is_allowed("runtime_client_fb", limit=2, window=60)
        assert allowed is True

        allowed2, _ = redis_limiter.is_allowed("runtime_client_fb", limit=2, window=60)
        assert allowed2 is True

        allowed3, retry3 = redis_limiter.is_allowed("runtime_client_fb", limit=2, window=60)
        assert allowed3 is False
        assert retry3 >= 1

    def test_concurrent_rate_limiting_thread_safety(self):
        """Hardening 6: 20 concurrent threads against a limit of 8 permit exactly 8 requests."""
        from concurrent.futures import ThreadPoolExecutor

        limiter = InMemoryRateLimiter(default_limit=8, default_window=60)
        results = []

        def _try_allow(idx: int):
            return limiter.is_allowed("concurrent_tenant_1", limit=8, window=60)[0]

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(_try_allow, i) for i in range(20)]
            results = [f.result() for f in futures]

        allowed_count = sum(1 for r in results if r is True)
        rejected_count = sum(1 for r in results if r is False)

        assert allowed_count == 8, f"Expected exactly 8 allowed, got {allowed_count}"
        assert rejected_count == 12, f"Expected exactly 12 rejected, got {rejected_count}"


