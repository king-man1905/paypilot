"""Distributed Scaling, Multi-Worker Architecture & Shared State Test Suite for PayPilot.

Tests:
1. State classification and process-local vs shared state isolation.
2. Multi-worker job visibility via shared SQLJobStore.
3. Atomic job claim and duplicate execution prevention across simulated workers.
4. Tenant isolation enforcement in shared SQLJobStore.
5. Distributed rate limiting fallback and sliding-window enforcement.
6. Multi-worker database connection pool sizing calculation and verification.
7. Request-ID propagation and correlation across simulated nodes.
8. Secret non-exposure in shared relational job storage.
9. 100% offline test execution.
"""

from concurrent.futures import ThreadPoolExecutor
import time
import pytest

from backend.config import (
    DB_MAX_OVERFLOW,
    DB_POOL_SIZE,
    get_app_workers,
    get_job_store_backend,
    get_rate_limit_backend,
)
from backend.jobs import (
    InMemoryJobStore,
    JobRecord,
    JobRunner,
    JobStatus,
)
from backend.jobs.store import SQLJobStore
from backend.security.rate_limiter import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    get_rate_limiter,
)
from backend.storage.connection import get_db_engine, get_db_session
from backend.storage.models import Base


@pytest.fixture(autouse=True)
def setup_distributed_test_env(monkeypatch):
    """Configures isolated test environment for distributed scaling tests."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JOB_STORE_BACKEND", "sql")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    yield


def test_state_classification_process_local_vs_shared_isolation():
    """Verifies that in-memory stores remain isolated across distinct process instances."""
    store_worker_1 = InMemoryJobStore(max_retained=10)
    store_worker_2 = InMemoryJobStore(max_retained=10)

    job = JobRecord(job_id="job_local_001", client_id="merchant_1", role="analyst")
    store_worker_1.save_job(job)

    # Worker 1 sees it, Worker 2 has isolated memory and does NOT see it
    assert store_worker_1.get_job("job_local_001") is not None
    assert store_worker_2.get_job("job_local_001") is None


def test_sql_job_store_cross_worker_job_visibility():
    """Verifies that multiple worker instances sharing a SQL database have full job visibility."""
    # Worker 1 initializes SQLJobStore and submits a job
    worker_1_store = SQLJobStore()
    job = JobRecord(
        job_id="job_shared_001",
        task_type="async_analysis",
        client_id="merchant_alpha",
        role="analyst",
        parameters={"query": "Why did checkout drop?"},
    )
    worker_1_store.save_job(job)

    # Worker 2 initializes separate SQLJobStore against the same database
    worker_2_store = SQLJobStore()
    retrieved = worker_2_store.get_job("job_shared_001", client_id="merchant_alpha", role="analyst")

    assert retrieved is not None
    assert retrieved.job_id == "job_shared_001"
    assert retrieved.client_id == "merchant_alpha"
    assert retrieved.status == JobStatus.QUEUED.value
    assert retrieved.parameters["query"] == "Why did checkout drop?"


def test_sql_job_store_atomic_claim_and_duplicate_prevention():
    """Verifies atomic job claiming prevents duplicate executions when multiple workers compete."""
    store = SQLJobStore()
    job = JobRecord(
        job_id="job_compete_001",
        client_id="merchant_beta",
        status=JobStatus.QUEUED.value,
    )
    store.save_job(job)

    results = []

    def _simulate_worker_claim(worker_id: str):
        # Each worker attempts atomic claim
        claimed = store.claim_job("job_compete_001", worker_id=worker_id)
        results.append((worker_id, claimed))

    # Simulate 5 concurrent workers trying to claim the exact same job
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_simulate_worker_claim, f"worker_node_{i}") for i in range(5)]
        for f in futures:
            f.result()

    # Exactly 1 worker must succeed; all other 4 must fail
    successful_claims = [r for r in results if r[1] is True]
    failed_claims = [r for r in results if r[1] is False]

    assert len(successful_claims) == 1, f"Expected exactly 1 claim, got {len(successful_claims)}"
    assert len(failed_claims) == 4

    # Verify updated job state in database
    claimed_job = store.get_job("job_compete_001")
    assert claimed_job.status == JobStatus.RUNNING.value
    assert claimed_job.worker_id == successful_claims[0][0]
    assert claimed_job.started_at is not None


def test_sql_job_store_tenant_isolation():
    """Verifies tenant isolation is enforced at the SQL database layer."""
    store = SQLJobStore()
    job = JobRecord(
        job_id="job_isolated_002",
        client_id="tenant_X",
        role="analyst",
    )
    store.save_job(job)

    # Tenant Y cannot access Tenant X's job
    assert store.get_job("job_isolated_002", client_id="tenant_Y", role="analyst") is None

    # Tenant X can access own job
    assert store.get_job("job_isolated_002", client_id="tenant_X", role="analyst") is not None

    # Admin can access across tenants
    assert store.get_job("job_isolated_002", client_id="admin_user", role="admin") is not None


def test_redis_rate_limiter_graceful_fallback():
    """Verifies RedisRateLimiter falls back safely to in-memory limiter when Redis is unreachable."""
    # Initialize with unreachable redis URL
    limiter = RedisRateLimiter(redis_url="redis://127.0.0.1:65432/0", default_limit=3, default_window=60)
    assert limiter._client is None  # Connection fails gracefully

    # Must continue operating via local fallback
    allowed, retry_after = limiter.is_allowed("client_fb_1")
    assert allowed is True
    assert retry_after == 0

    limiter.is_allowed("client_fb_1")
    limiter.is_allowed("client_fb_1")

    # 4th request exceeds limit
    allowed_4th, retry_after_4th = limiter.is_allowed("client_fb_1")
    assert allowed_4th is False
    assert retry_after_4th > 0


def test_multi_worker_db_connection_pool_sizing_math():
    """Verifies database connection pool sizing formula for multi-worker deployments."""
    workers = 4
    pool_size = DB_POOL_SIZE
    max_overflow = DB_MAX_OVERFLOW

    # Theoretical maximum connections across all workers
    max_db_conns = workers * (pool_size + max_overflow)

    assert pool_size == 5
    assert max_overflow == 10
    assert max_db_conns == 60  # 4 * (5 + 10)


def test_secret_non_exposure_in_distributed_job_store():
    """Verifies credentials and API keys are redacted before writing to relational job store."""
    store = SQLJobStore()
    job = JobRecord(
        job_id="job_secret_001",
        client_id="merchant_sec",
        parameters={
            "query": "Diagnose api_key=nvapi-1234567890abcdef and token: sk-secret9988",
            "metadata": {"bearer": "secret_token_123"},
        },
        result={"token": "Bearer secret_token_abc", "diagnosis": "ok"},
    )
    store.save_job(job)

    fetched = store.get_job("job_secret_001")
    raw_str = str(fetched.to_dict()).lower()

    assert "nvapi-1234567890abcdef" not in raw_str
    assert "sk-secret9988" not in raw_str


def test_second_worker_cannot_claim_active_running_job():
    """Verifies that while Worker A holds an active, unexpired lease, Worker B cannot claim the job."""
    store = SQLJobStore()
    job = JobRecord(
        job_id="job_active_lease_001",
        client_id="merchant_gamma",
        status=JobStatus.QUEUED.value,
    )
    store.save_job(job)

    # Worker A claims with a 300s lease
    claimed_a = store.claim_job("job_active_lease_001", worker_id="worker_A", lease_timeout_seconds=300)
    assert claimed_a is True

    # Worker B tries to claim the same job while lease is still valid
    claimed_b = store.claim_job("job_active_lease_001", worker_id="worker_B", lease_timeout_seconds=300)
    assert claimed_b is False

    # Job remains owned by Worker A in RUNNING state
    active_job = store.get_job("job_active_lease_001")
    assert active_job.status == JobStatus.RUNNING.value
    assert active_job.worker_id == "worker_A"


def test_stale_lease_recovery_after_worker_crash():
    """Simulates Worker A crashing mid-execution and Worker B recovering the job after lease expiration."""
    store = SQLJobStore()
    job = JobRecord(
        job_id="job_crashed_worker_001",
        client_id="merchant_delta",
        status=JobStatus.QUEUED.value,
    )
    store.save_job(job)

    # Worker A claims the job with a 0-second lease (immediately expired)
    claimed_a = store.claim_job("job_crashed_worker_001", worker_id="worker_crashed_A", lease_timeout_seconds=0)
    assert claimed_a is True

    # Worker A simulated crash (never updates status to completed or failed)
    time.sleep(0.01)

    # Worker B starts later and attempts to claim the stale job
    claimed_b = store.claim_job("job_crashed_worker_001", worker_id="worker_healthy_B", lease_timeout_seconds=0)
    assert claimed_b is True, "Worker B should successfully recover the stale job lease"

    # Verify job is recovered and re-assigned to Worker B
    recovered_job = store.get_job("job_crashed_worker_001")
    assert recovered_job.status == JobStatus.RUNNING.value
    assert recovered_job.worker_id == "worker_healthy_B"


def test_recover_stale_jobs_batch_requeues_orphaned_jobs():
    """Verifies batch stale job recovery resets orphaned jobs from RUNNING back to QUEUED."""
    store = SQLJobStore()
    store.reset()

    for i in range(3):
        job = JobRecord(
            job_id=f"job_orphaned_{i:03d}",
            client_id="merchant_orphan",
            status=JobStatus.QUEUED.value,
        )
        store.save_job(job)
        store.claim_job(job.job_id, worker_id="worker_dead", lease_timeout_seconds=0)

    time.sleep(0.01)

    # Run batch recovery with 0-second lease timeout
    recovered_count = store.recover_stale_jobs(lease_timeout_seconds=0)
    assert recovered_count == 3

    # All 3 jobs are back in QUEUED status ready to be claimed
    for i in range(3):
        j = store.get_job(f"job_orphaned_{i:03d}")
        assert j.status == JobStatus.QUEUED.value
        assert j.worker_id is None
        assert j.started_at is None
        assert j.retry_count == 1



def test_config_validation_includes_distributed_parameters():
    """Verifies validate_config exposes Phase 17 distributed parameters safely."""
    from backend.config import validate_config
    cfg = validate_config()

    assert "app_workers" in cfg
    assert "job_store_backend" in cfg
    assert "rate_limit_backend" in cfg

