"""Unit and Integration Test Suite for Phase 21 Request Idempotency.

Covers:
1. Idempotency-Key syntax and bounds validation.
2. SHA-256 deterministic payload hashing.
3. InMemoryIdempotencyStore & RedisIdempotencyStore reservation semantics (RESERVED, REPLAY, CONFLICT).
4. Multi-tenant namespace isolation (Tenant A vs Tenant B).
5. Bounded capacity and TTL eviction.
6. API endpoint integration on POST /api/v1/jobs with exact replays, conflicts, and error propagation.
"""

import time
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.jobs import get_job_runner, reset_job_runner
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.security.idempotency import (
    IdempotencyReservationStatus,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    compute_payload_hash,
    get_idempotency_store,
    reset_idempotency_store,
    set_idempotency_store,
    validate_idempotency_key,
)
from backend.security.auth import AuthenticatedUser
from backend.security.quotas import reset_quota_manager


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


class TestIdempotencyValidationAndHashing:
    """Tests syntax validation and deterministic hashing."""

    def test_valid_keys(self):
        valid_keys = [
            "idem-key-12345",
            "req_uuid_abc_def_123",
            "client:tenant-42:batch-9",
            "a",
            "a" * 128,
            "IDEMPOTENCY-KEY.123",
        ]
        for k in valid_keys:
            is_valid, err = validate_idempotency_key(k)
            assert is_valid is True, f"Expected key '{k}' to be valid, got err: {err}"
            assert err is None

    def test_invalid_keys(self):
        invalid_keys = [
            ("", "cannot be empty"),
            ("   ", "cannot be empty"),
            (None, "cannot be empty"),
            ("a" * 129, "exceeds maximum limit of 128"),
            ("invalid key with spaces", "contains invalid characters"),
            ("invalid/key/slashes", "contains invalid characters"),
            ("invalid$key", "contains invalid characters"),
            ("invalid@key!", "contains invalid characters"),
        ]
        for k, expected_msg in invalid_keys:
            is_valid, err = validate_idempotency_key(k)
            assert is_valid is False
            assert expected_msg in err

    def test_payload_hashing_determinism(self):
        payload_1 = {"query": "Check drops in UPI", "task_type": "merchant_diagnostic", "metadata": {"source": "web"}}
        payload_2 = {"metadata": {"source": "web"}, "task_type": "merchant_diagnostic", "query": "Check drops in UPI"}
        payload_3 = {"query": "Check drops in NetBanking", "task_type": "merchant_diagnostic", "metadata": {"source": "web"}}

        hash_1 = compute_payload_hash(payload_1)
        hash_2 = compute_payload_hash(payload_2)
        hash_3 = compute_payload_hash(payload_3)

        assert hash_1 == hash_2, "Hashes must be identical regardless of JSON key order."
        assert hash_1 != hash_3, "Hashes must differ for different payload queries."
        assert len(hash_1) == 64


class TestInMemoryIdempotencyStore:
    """Tests in-memory store semantics, state transitions, and eviction."""

    def test_reservation_and_replay_lifecycle(self):
        store = InMemoryIdempotencyStore(max_records=100)
        tenant_id = "tenant-alpha"
        key = "job-sub-001"
        payload_hash = compute_payload_hash({"query": "UPI drop diagnosis"})

        # 1. Initial reservation
        status, record = store.reserve(tenant_id, key, payload_hash, ttl_seconds=60)
        assert status == IdempotencyReservationStatus.RESERVED
        assert record is not None
        assert record.status == "reserved"
        assert record.tenant_id == tenant_id
        assert record.key == key

        # 2. Complete the record
        store.complete(
            tenant_id=tenant_id,
            key=key,
            job_id="job-12345",
            response_payload={"job_id": "job-12345", "status": "completed"},
        )

        # 3. Exact replay
        replay_status, replay_record = store.reserve(tenant_id, key, payload_hash, ttl_seconds=60)
        assert replay_status == IdempotencyReservationStatus.REPLAY
        assert replay_record is not None
        assert replay_record.job_id == "job-12345"
        assert replay_record.response_payload is not None
        assert replay_record.response_payload["job_id"] == "job-12345"

        # 4. Conflict detection (same key, different payload)
        diff_payload_hash = compute_payload_hash({"query": "Completely different query"})
        conflict_status, conflict_record = store.reserve(tenant_id, key, diff_payload_hash, ttl_seconds=60)
        assert conflict_status == IdempotencyReservationStatus.CONFLICT
        assert conflict_record is not None
        assert conflict_record.job_id == "job-12345"

    def test_multi_tenant_isolation(self):
        store = InMemoryIdempotencyStore()
        key = "shared-idempotency-key"
        hash_a = compute_payload_hash({"tenant": "A"})
        hash_b = compute_payload_hash({"tenant": "B"})

        # Tenant A reserves
        stat_a, rec_a = store.reserve("tenant-a", key, hash_a)
        assert stat_a == IdempotencyReservationStatus.RESERVED
        assert rec_a is not None

        # Tenant B reserves same key name with different payload
        stat_b, rec_b = store.reserve("tenant-b", key, hash_b)
        assert stat_b == IdempotencyReservationStatus.RESERVED
        assert rec_b is not None
        assert rec_a.tenant_id == "tenant-a"
        assert rec_b.tenant_id == "tenant-b"

    def test_ttl_expiration(self):
        store = InMemoryIdempotencyStore()
        tenant = "tenant-expiry"
        key = "exp-key"
        payload_hash = compute_payload_hash({"q": "test"})

        # Reserve with 1s TTL
        stat, rec = store.reserve(tenant, key, payload_hash, ttl_seconds=1)
        assert stat == IdempotencyReservationStatus.RESERVED

        time.sleep(1.05)

        # Should be expired and re-reservable as new
        stat_after, rec_after = store.reserve(tenant, key, payload_hash, ttl_seconds=60)
        assert stat_after == IdempotencyReservationStatus.RESERVED

    def test_bounded_capacity_fifo_eviction(self):
        store = InMemoryIdempotencyStore(max_records=3)
        tenant = "tenant-overflow"

        store.reserve(tenant, "key-1", compute_payload_hash("1"))
        time.sleep(0.01)
        store.reserve(tenant, "key-2", compute_payload_hash("2"))
        time.sleep(0.01)
        store.reserve(tenant, "key-3", compute_payload_hash("3"))
        time.sleep(0.01)

        # 4th reservation causes eviction of key-1
        store.reserve(tenant, "key-4", compute_payload_hash("4"))

        assert store.get_record(tenant, "key-1") is None
        assert store.get_record(tenant, "key-4") is not None


class TestIdempotencyApiIntegration:
    """Tests API endpoint behavior on POST /api/v1/jobs."""

    def test_submit_job_without_idempotency_key(self):
        client = TestClient(app)
        res = client.post(
            "/api/v1/jobs",
            json={"query": "Check refund failure anomalies", "task_type": "merchant_diagnostic"},
        )
        assert res.status_code == 202
        data = res.json()
        assert "job_id" in data
        assert data["status"] in ("queued", "running", "completed")

    def test_submit_job_with_exact_idempotent_replay(self):
        client = TestClient(app)
        headers = {"Idempotency-Key": "idemp-req-7788"}
        body = {"query": "Analyze checkout dropoff spikes", "task_type": "merchant_diagnostic"}

        # First request
        res1 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res1.status_code == 202
        data1 = res1.json()
        job_id_1 = data1["job_id"]

        # Duplicate replay with exact same key and payload
        res2 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res2.status_code == 202
        data2 = res2.json()
        assert data2["job_id"] == job_id_1, "Replay must return the identical job record without spawning new job."

        # Verify metrics recorded replay
        metrics = get_metrics_snapshot()
        assert metrics["traffic"]["idempotency_replays"] >= 1

    def test_submit_job_with_idempotency_conflict_returns_409(self):
        client = TestClient(app)
        headers = {"Idempotency-Key": "idemp-req-conflict-99"}

        # First request
        res1 = client.post(
            "/api/v1/jobs",
            json={"query": "Original query payload A", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert res1.status_code == 202

        # Second request with SAME key but DIFFERENT payload
        res2 = client.post(
            "/api/v1/jobs",
            json={"query": "Modified query payload B", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert res2.status_code == 409
        err = res2.json()
        assert "detail" in err
        assert "Idempotency conflict" in err["detail"]

        # Verify metrics recorded conflict
        metrics = get_metrics_snapshot()
        assert metrics["traffic"]["idempotency_conflicts"] >= 1

    def test_submit_job_with_invalid_idempotency_key_returns_400(self):
        client = TestClient(app)
        headers = {"Idempotency-Key": "invalid key with spaces!"}

        res = client.post(
            "/api/v1/jobs",
            json={"query": "Valid diagnostic query", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "Invalid Idempotency-Key header" in res.json()["detail"]

    def test_submit_job_with_oversized_idempotency_key_returns_400(self):
        client = TestClient(app)
        headers = {"Idempotency-Key": "a" * 129}

        res = client.post(
            "/api/v1/jobs",
            json={"query": "Valid diagnostic query", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert res.status_code == 400
        assert "exceeds maximum limit" in res.json()["detail"]

    def test_concurrent_idempotency_race_50_threads_exactly_one_job_created(self):
        """Hardening 1: 50 concurrent requests with identical Idempotency-Key create exactly 1 job."""
        from concurrent.futures import ThreadPoolExecutor
        from backend.config import override_settings

        client = TestClient(app)
        headers = {"Idempotency-Key": "race-key-50-threads-deterministic"}
        body = {"query": "Diagnose concurrent refund dropoffs", "task_type": "merchant_diagnostic"}

        def _send():
            return client.post("/api/v1/jobs", json=body, headers=headers)

        num_threads = 50
        with override_settings(job_rate_limit_per_minute=1000, tenant_daily_job_quota=1000):
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(_send) for _ in range(num_threads)]
                responses = [f.result() for f in futures]

        # Verify all return 202 Accepted
        for r in responses:
            assert r.status_code == 202

        # Verify all responses received the EXACT same job_id
        job_ids = [r.json()["job_id"] for r in responses]
        unique_job_ids = set(job_ids)
        assert len(unique_job_ids) == 1, f"Expected exactly 1 created job, but found: {unique_job_ids}"

        # Verify the runner store contains exactly ONE job record
        runner = get_job_runner()
        stored_jobs = runner.store.list_jobs(client_id="anonymous-dev")
        assert len(stored_jobs) == 1
        assert stored_jobs[0].job_id == job_ids[0]

        # Verify metrics recorded 49 replays
        metrics = get_metrics_snapshot()
        assert metrics["traffic"]["idempotency_replays"] == num_threads - 1

    def test_replay_after_job_completion_returns_completed_status(self):
        """Hardening 8: Replay after job finishes execution returns completed status and result."""
        client = TestClient(app)
        headers = {"Idempotency-Key": "job-completion-replay-key"}
        body = {"query": "Diagnose completed job flow", "task_type": "merchant_diagnostic"}

        # 1. Initial submission
        res1 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res1.status_code == 202
        job_id = res1.json()["job_id"]

        # 2. Simulate worker completing the job
        runner = get_job_runner()
        job = runner.store.get_job(job_id)
        assert job is not None
        job.status = "completed"
        job.result = {"analysis": "Mock analysis complete", "recovery_inr": 50000.0}
        runner.store.update_job(job)

        # 3. Replay with identical key
        res2 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res2.status_code == 202
        data2 = res2.json()
        assert data2["job_id"] == job_id
        assert data2["status"] == "completed"
        assert data2["result"]["recovery_inr"] == 50000.0

    def test_replay_after_job_failure_returns_failed_status(self):
        """Hardening 8: Replay after job encounters an error returns failed status and error details."""
        client = TestClient(app)
        headers = {"Idempotency-Key": "job-failure-replay-key"}
        body = {"query": "Diagnose failed job flow", "task_type": "merchant_diagnostic"}

        # 1. Initial submission
        res1 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res1.status_code == 202
        job_id = res1.json()["job_id"]

        # 2. Simulate worker failing the job
        runner = get_job_runner()
        job = runner.store.get_job(job_id)
        assert job is not None
        job.status = "failed"
        job.error = {"message": "Upstream database connection timeout", "category": "provider_error"}
        runner.store.update_job(job)

        # 3. Replay with identical key
        res2 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert res2.status_code == 202
        data2 = res2.json()
        assert data2["job_id"] == job_id
        assert data2["status"] == "failed"
        assert "Upstream database connection timeout" in data2["error"]["message"]

    def test_reservation_cancellation_on_admission_drop(self):
        """Hardening 4 & 6: Reservation is cleanly cancelled if request is dropped before completion."""
        from backend.config import override_settings

        client = TestClient(app)
        key = "drop-cancel-key-01"
        headers = {"Idempotency-Key": key}
        body = {"query": "Diagnose quota drop", "task_type": "merchant_diagnostic"}

        # Exhaust quota
        with override_settings(tenant_daily_job_quota=0):
            res_drop = client.post("/api/v1/jobs", json=body, headers=headers)
            assert res_drop.status_code == 429

        # Verify idempotency store does not have an active dangling reservation
        store = get_idempotency_store()
        rec = store.get_record("anonymous-dev", key)
        assert rec is None, "Failed reservation must be cleanly cancelled"

    def test_cross_tenant_idempotency_isolation(self):
        """Hardening 2: Tenant A and Tenant B use the exact same Idempotency-Key without collision."""
        client = TestClient(app)
        shared_key = "shared-idemp-token-404"
        body = {"query": "Analyze checkout drops", "task_type": "merchant_diagnostic"}

        # Custom authenticated users
        user_a = AuthenticatedUser(client_id="merchant_a", role="analyst")
        user_b = AuthenticatedUser(client_id="merchant_b", role="analyst")
        admin_user = AuthenticatedUser(client_id="admin_sec", role="admin")

        from backend.security.auth import require_analyst
        
        # 1. Tenant A submits
        app.dependency_overrides[require_analyst] = lambda: user_a
        res_a = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": shared_key})
        assert res_a.status_code == 202
        job_a_id = res_a.json()["job_id"]

        # 2. Tenant B submits with same key
        app.dependency_overrides[require_analyst] = lambda: user_b
        res_b = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": shared_key})
        assert res_b.status_code == 202
        job_b_id = res_b.json()["job_id"]

        # Must have created two distinct jobs
        assert job_a_id != job_b_id, "Different tenants with same key must create separate jobs."

        # 3. Tenant A replay gets Job A (not Job B)
        app.dependency_overrides[require_analyst] = lambda: user_a
        res_a_replay = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": shared_key})
        assert res_a_replay.status_code == 202
        assert res_a_replay.json()["job_id"] == job_a_id

        # 4. Tenant B replay gets Job B (not Job A)
        app.dependency_overrides[require_analyst] = lambda: user_b
        res_b_replay = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": shared_key})
        assert res_b_replay.status_code == 202
        assert res_b_replay.json()["job_id"] == job_b_id

        # 5. Tenant A tries to access Tenant B's job -> 403 Forbidden
        app.dependency_overrides[require_analyst] = lambda: user_a
        res_cross = client.get(f"/api/v1/jobs/{job_b_id}")
        assert res_cross.status_code == 403

        # 6. Admin can access both jobs
        app.dependency_overrides[require_analyst] = lambda: admin_user
        res_admin_a = client.get(f"/api/v1/jobs/{job_a_id}")
        res_admin_b = client.get(f"/api/v1/jobs/{job_b_id}")
        assert res_admin_a.status_code == 200
        assert res_admin_b.status_code == 200

        # Clean overrides
        app.dependency_overrides.clear()

    def test_redis_idempotency_store_fallback(self):
        """Hardening 3: RedisIdempotencyStore with unreachable Redis degrades gracefully to local in-memory."""
        redis_store = RedisIdempotencyStore(redis_url="redis://127.0.0.1:9999/0")
        set_idempotency_store(redis_store)

        status, rec = redis_store.reserve("tenant_fb", "key_fb_1", compute_payload_hash("data"))
        assert status == IdempotencyReservationStatus.RESERVED

        # Exact replay
        replay_status, replay_rec = redis_store.reserve("tenant_fb", "key_fb_1", compute_payload_hash("data"))
        assert replay_status == IdempotencyReservationStatus.REPLAY

        # Cancellation
        redis_store.cancel_reservation("tenant_fb", "key_fb_1")
        # Should be gone or completed
        assert redis_store.get_record("tenant_fb", "key_fb_1") is None

    def test_redis_idempotency_store_runtime_exception_fallback(self):
        """Hardening 3: RedisIdempotencyStore degrades gracefully when Redis client throws runtime exceptions."""
        from unittest.mock import MagicMock

        redis_store = RedisIdempotencyStore(redis_url="redis://127.0.0.1:9999/0")
        mock_client = MagicMock()
        mock_client.set.side_effect = ConnectionError("Redis network partition")
        mock_client.get.side_effect = ConnectionError("Redis network partition")
        mock_client.delete.side_effect = ConnectionError("Redis network partition")
        redis_store._client = mock_client
        set_idempotency_store(redis_store)

        status, rec = redis_store.reserve("tenant_exc", "key_exc_1", compute_payload_hash("payload_exc"))
        assert status == IdempotencyReservationStatus.RESERVED

        # Complete and replay fallback
        redis_store.complete("tenant_exc", "key_exc_1", "job_exc_1", {"job_id": "job_exc_1"})
        status2, rec2 = redis_store.reserve("tenant_exc", "key_exc_1", compute_payload_hash("payload_exc"))
        assert status2 == IdempotencyReservationStatus.REPLAY

    def test_idempotency_audit_fingerprint_never_exposes_raw_key(self):
        """Hardening 7: Verify Idempotency audit events store safe hash fingerprints rather than raw key."""
        from backend.observability.audit import get_audit_store
        from backend.security.idempotency import fingerprint_idempotency_key

        client = TestClient(app)
        raw_key = "secret-idemp-token-xyz-12345"
        headers = {"Idempotency-Key": raw_key}
        body = {"query": "Diagnose audit key safety", "task_type": "merchant_diagnostic"}

        # First request (creates job)
        r1 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert r1.status_code == 202

        # Second request (replay)
        r2 = client.post("/api/v1/jobs", json=body, headers=headers)
        assert r2.status_code == 202

        # Check audit trail
        audit_events = get_audit_store().get_events(limit=50)
        replays = [e for e in audit_events if e.event_type == "idempotency_replay"]
        assert len(replays) >= 1

        expected_fp = fingerprint_idempotency_key(raw_key)
        assert replays[0].query_summary is not None
        assert expected_fp in replays[0].query_summary
        assert raw_key not in replays[0].query_summary

    def test_payload_conflict_after_job_completion_returns_409(self):
        """Hardening 8: Attempting to reuse an existing completed key with a modified payload yields 409 Conflict."""
        client = TestClient(app)
        headers = {"Idempotency-Key": "completed-key-conflict-check"}

        r1 = client.post(
            "/api/v1/jobs",
            json={"query": "Original query content", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert r1.status_code == 202
        job_id = r1.json()["job_id"]

        # Simulate completion
        runner = get_job_runner()
        job = runner.store.get_job(job_id)
        assert job is not None
        job.status = "completed"
        runner.store.update_job(job)

        # Conflict request with different payload
        r2 = client.post(
            "/api/v1/jobs",
            json={"query": "Altered query content", "task_type": "merchant_diagnostic"},
            headers=headers,
        )
        assert r2.status_code == 409
        assert "Idempotency conflict" in r2.json()["detail"]

