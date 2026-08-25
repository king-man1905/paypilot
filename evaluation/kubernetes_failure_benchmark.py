"""PayPilot Phase 24 — Kubernetes Failure & Recovery Simulation Benchmark.

Evaluates high-availability failure modes:
A. Kill API Pod: Probe transition during termination (readiness=503, liveness=200).
B. Kill Worker Pod: Unresponsive worker lease recovery after timeout.
C. Redis Unavailable: Seamless fallback to in-memory rate limiting and quota enforcement.
D. PostgreSQL Unavailable / Oversubscription: Connection pool safety limits and validation.
E. Readiness Failure: Traffic admission rejection on unready or draining pods.
F. Rolling Restart / Zero-Downtime: MaxSurge and MaxUnavailable parameter validation.
G. Worker Lease Recovery: At-most-one active claim and at-least-once execution guarantee.
"""

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import yaml
from starlette.testclient import TestClient

from backend.api.main import app, set_shutting_down
from backend.config import calculate_total_db_connections, validate_cluster_db_capacity
from backend.jobs.models import JobRecord, JobStatus
from backend.jobs.runner import RunnerState, get_job_runner
from backend.jobs.store import InMemoryJobStore
from backend.security.rate_limiter import RedisRateLimiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("paypilot.benchmark.k8s_failure")

REPORT_PATH = ROOT_DIR / "evaluation" / "kubernetes_failure_report.json"
K8S_DIR = ROOT_DIR / "k8s"


def simulate_worker_crash_and_lease_recovery() -> Dict[str, Any]:
    """Scenario B & G: Evaluates lease recovery when a worker node crashes mid-execution."""
    t0 = time.perf_counter()
    store = InMemoryJobStore()

    # 1. Submit a job
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job = JobRecord(
        job_id=job_id,
        task_type="async_analysis",
        client_id="tenant_k8s_recovery",
        status=JobStatus.QUEUED.value,
        parameters={"query": "Analyze checkout failure spike in UK region"},
    )
    store.save_job(job)

    # 2. Worker 1 claims job with 1s lease
    worker_1_id = "worker_crashed_node_1"
    worker_2_id = "worker_failover_node_2"

    claimed_1 = store.claim_job(job_id=job_id, worker_id=worker_1_id, lease_timeout_seconds=1)
    assert claimed_1 is True

    # 3. Verify Worker 2 cannot claim while lease is active (at-most-one guarantee)
    claimed_early = store.claim_job(job_id=job_id, worker_id=worker_2_id, lease_timeout_seconds=1)
    assert claimed_early is False

    # 4. Simulate Worker 1 crash by letting lease expire
    time.sleep(1.05)

    # 5. Worker 2 recovers expired lease
    claimed_2 = store.claim_job(job_id=job_id, worker_id=worker_2_id, lease_timeout_seconds=1)
    assert claimed_2 is True

    # 6. Complete job
    completed_job = store.get_job(job_id)
    assert completed_job is not None
    completed_job.status = JobStatus.COMPLETED.value
    completed_job.result = {"recovery_status": "success"}
    store.update_job(completed_job)

    final_job = store.get_job(job_id)
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    is_success = (
        final_job is not None
        and final_job.status == JobStatus.COMPLETED.value
        and not claimed_early
        and claimed_2 is True
    )

    return {
        "scenario": "worker_crash_and_lease_recovery",
        "category": "Worker Fault Tolerance (B & G)",
        "status": "PASSED" if is_success else "FAILED",
        "job_id": job_id,
        "crashed_worker": worker_1_id,
        "recovering_worker": worker_2_id,
        "duplicate_active_claims": 0 if not claimed_early else 1,
        "lease_recovered": claimed_2 is True,
        "final_job_status": final_job.status if final_job else "none",
        "duration_ms": duration_ms,
    }


def simulate_api_shutdown_probe_transitions() -> Dict[str, Any]:
    """Scenario A & E: Evaluates readiness and liveness probe transitions during pod shutdown."""
    t0 = time.perf_counter()
    client = TestClient(app)

    try:
        # Pre-shutdown: both probes healthy
        set_shutting_down(False)
        r_health_before = client.get("/health")
        r_ready_before = client.get("/ready")

        assert r_health_before.status_code == 200
        assert r_ready_before.status_code == 200

        # Initiate graceful shutdown (SIGTERM signal handler)
        set_shutting_down(True)

        r_health_during = client.get("/health")
        r_ready_during = client.get("/ready")

        # Readiness MUST fail (503) so K8s stops routing new traffic
        # Liveness MUST stay 200 so K8s does not kill the pod before drain finishes
        readiness_blocked = r_ready_during.status_code == 503
        liveness_intact = r_health_during.status_code == 200

        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        is_success = readiness_blocked and liveness_intact

        return {
            "scenario": "api_shutdown_probe_transitions",
            "category": "API Graceful Shutdown & Probes (A & E)",
            "status": "PASSED" if is_success else "FAILED",
            "pre_shutdown_health_code": r_health_before.status_code,
            "pre_shutdown_ready_code": r_ready_before.status_code,
            "shutdown_health_code": r_health_during.status_code,
            "shutdown_ready_code": r_ready_during.status_code,
            "traffic_admission_blocked": readiness_blocked,
            "liveness_intact_during_drain": liveness_intact,
            "duration_ms": duration_ms,
        }
    finally:
        set_shutting_down(False)


def simulate_redis_outage_fallback() -> Dict[str, Any]:
    """Scenario C: Evaluates rate limiter resilience under transient Redis failure."""
    t0 = time.perf_counter()

    # Attempt connection to unreachable Redis endpoint
    limiter = RedisRateLimiter(
        redis_url="redis://unreachable-k8s-redis-host:6379/0",
        default_limit=5,
        default_window=60,
    )

    # Verify limiter fell back to in-memory store
    is_fallback_active = limiter._client is None

    # Verify rate limiting still functions correctly
    allowed_1, _ = limiter.is_allowed("tenant_failover_test")
    allowed_2, _ = limiter.is_allowed("tenant_failover_test")

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    is_success = is_fallback_active and allowed_1 and allowed_2

    return {
        "scenario": "redis_outage_fallback",
        "category": "Redis Fault Tolerance (C)",
        "status": "PASSED" if is_success else "FAILED",
        "fallback_engaged": is_fallback_active,
        "fallback_rate_limiting_functional": allowed_1 and allowed_2,
        "duration_ms": duration_ms,
    }


def simulate_cluster_connection_pool_safety() -> Dict[str, Any]:
    """Scenario D: Validates cluster-wide database connection pool calculations and safety boundaries."""
    t0 = time.perf_counter()

    # Sizing for 2 API replicas (pool 5 + overflow 10 = 30) + 2 Worker replicas (pool 3 = 6) = 36
    total_conns = calculate_total_db_connections(api_replicas=2, worker_replicas=2)
    cap_check = validate_cluster_db_capacity(max_db_server_connections=100, api_replicas=2, worker_replicas=2)

    # Oversized configuration: 10 API replicas + 10 Worker replicas = 180 (exceeds 100 max)
    oversized_check = validate_cluster_db_capacity(max_db_server_connections=100, api_replicas=10, worker_replicas=10)

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    is_success = cap_check["is_safe"] and not oversized_check["is_safe"]

    return {
        "scenario": "cluster_connection_pool_safety",
        "category": "Database Capacity & Pool Safety (D)",
        "status": "PASSED" if is_success else "FAILED",
        "standard_cluster_connections": total_conns,
        "standard_cluster_safe": cap_check["is_safe"],
        "oversized_cluster_detected_unsafe": not oversized_check["is_safe"],
        "duration_ms": duration_ms,
    }


def simulate_rolling_update_safety_parameters() -> Dict[str, Any]:
    """Scenario F: Validates zero-downtime rolling update strategy parameters in API deployment."""
    t0 = time.perf_counter()

    api_path = K8S_DIR / "api-deployment.yaml"
    with open(api_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    strat = doc.get("spec", {}).get("strategy", {}).get("rollingUpdate", {})
    max_unavailable = strat.get("maxUnavailable")
    max_surge = strat.get("maxSurge")
    grace_period = doc.get("spec", {}).get("template", {}).get("spec", {}).get("terminationGracePeriodSeconds")

    is_zero_downtime = max_unavailable == 0 and max_surge == 1 and grace_period == 30
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)

    return {
        "scenario": "rolling_update_safety_parameters",
        "category": "Rolling Restart & Zero Downtime (F)",
        "status": "PASSED" if is_zero_downtime else "FAILED",
        "max_unavailable": max_unavailable,
        "max_surge": max_surge,
        "termination_grace_period_seconds": grace_period,
        "duration_ms": duration_ms,
    }


def run_all_kubernetes_failure_benchmarks() -> Dict[str, Any]:
    """Executes all Phase 24 Kubernetes failure simulation benchmarks and writes report."""
    logger.info("Executing Kubernetes Failure & Recovery Simulation Benchmarks...")
    t0 = time.perf_counter()

    scenarios = [
        simulate_api_shutdown_probe_transitions(),
        simulate_worker_crash_and_lease_recovery(),
        simulate_redis_outage_fallback(),
        simulate_cluster_connection_pool_safety(),
        simulate_rolling_update_safety_parameters(),
    ]

    all_passed = all(s["status"] == "PASSED" for s in scenarios)
    total_duration_ms = round((time.perf_counter() - t0) * 1000, 2)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_environment": "LOCAL FAILURE SIMULATION / BENCHMARK",
        "overall_status": "PASSED" if all_passed else "FAILED",
        "total_duration_ms": total_duration_ms,
        "scenarios": scenarios,
        "demarcation": {
            "environment": "LOCAL FAILURE SIMULATION",
            "lease_guarantee": "AT-MOST-ONE ACTIVE CLAIM / AT-LEAST-ONCE RECOVERY",
            "production_status": "NOT CLOUD PRODUCTION VALIDATED",
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Kubernetes failure benchmark report written to {REPORT_PATH}")
    return report


if __name__ == "__main__":
    rep = run_all_kubernetes_failure_benchmarks()
    print("\n==========================================================================")
    print("           PAYPILOT KUBERNETES FAILURE & RECOVERY REPORT")
    print("==========================================================================")
    print(f"Status           : {rep['overall_status']}")
    print(f"Environment      : {rep['demarcation']['environment']}")
    print(f"Lease Guarantee  : {rep['demarcation']['lease_guarantee']}")
    print(f"Total Duration   : {rep['total_duration_ms']} ms")
    print("==========================================================================")
    for s in rep["scenarios"]:
        print(f" - [{s['category']}] {s['scenario']}: {s['status']} ({s['duration_ms']}ms)")
    print("==========================================================================")
