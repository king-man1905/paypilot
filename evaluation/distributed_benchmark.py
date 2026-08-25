"""PayPilot Distributed Multi-Worker Simulation & Scalability Benchmark.

Measures competing worker contention, atomic claiming latency, job execution latency
(Mean, P50, P95, P99), worker crash recovery, and duplicate prevention across varying
worker counts and job batch sizes.

NOTE: This benchmark is a LOCAL MULTI-WORKER SIMULATION using MockChatNVIDIA.
Throughput numbers reflect in-memory SQLite and local CPU execution, NOT a production
capacity guarantee.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Tuple

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.jobs import JobRecord, JobStatus, run_async_analysis_task
from backend.jobs.store import SQLJobStore
from evaluation.mock_llm import patch_offline_evaluation_llm

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("paypilot.benchmark.distributed")

SAMPLE_QUERIES = [
    "Why did revenue drop in month 3?",
    "Which payment method has the highest failure rate?",
    "Compare mobile checkout conversion with desktop.",
    "Which product category has the highest refund rate?",
    "What if payment success rate improves by 2%?",
]


def _calculate_percentiles(latencies: List[float]) -> Dict[str, float]:
    """Calculates Mean, P50, P95, and P99 from a list of latencies in ms."""
    if not latencies:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    sorted_lats = sorted(latencies)
    n = len(sorted_lats)

    mean = round(sum(sorted_lats) / n, 2)
    p50 = round(sorted_lats[int(n * 0.50)], 2)
    p95 = round(sorted_lats[min(int(n * 0.95), n - 1)], 2)
    p99 = round(sorted_lats[min(int(n * 0.99), n - 1)], 2)

    return {"mean": mean, "p50": p50, "p95": p95, "p99": p99}


def execute_workload_run(
    num_workers: int,
    num_jobs: int,
    shared_store: SQLJobStore,
    include_crash_scenario: bool = False,
) -> Dict[str, Any]:
    """Executes a single multi-worker simulation run for a given worker count and job count."""
    shared_store.reset()

    # 1. Seed jobs into shared SQL store
    job_ids = []
    for i in range(num_jobs):
        q = SAMPLE_QUERIES[i % len(SAMPLE_QUERIES)]
        job = JobRecord(
            job_id=f"job_w{num_workers}_j{num_jobs}_{i:03d}",
            task_type="async_analysis",
            client_id="benchmark_tenant",
            role="analyst",
            request_id=f"req-w{num_workers}-j{num_jobs}-{i:03d}",
            status=JobStatus.QUEUED.value,
            parameters={"query": q},
        )
        shared_store.save_job(job)
        job_ids.append(job.job_id)

    # 2. Track execution metrics
    worker_claims: Dict[str, List[str]] = {f"worker_{w}": [] for w in range(num_workers)}
    execution_latencies: List[float] = []
    claim_latencies: List[float] = []
    recoveries_count = 0
    failed_count = 0

    # If crash scenario requested: simulate an orphaned job with expired lease from a crashed worker
    if include_crash_scenario and num_jobs > 0:
        crashed_job_id = job_ids[0]
        # Simulate worker that claimed the job in the past and crashed (expired started_at timestamp)
        stale_time_iso = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
        crashed_job = shared_store.get_job(crashed_job_id)
        if crashed_job:
            crashed_job.status = JobStatus.RUNNING.value
            crashed_job.worker_id = "crashed_worker_node"
            crashed_job.started_at = stale_time_iso
            shared_store.update_job(crashed_job)

    t_start = time.perf_counter()

    with patch_offline_evaluation_llm():
        def _worker_loop(worker_id: str):
            nonlocal recoveries_count, failed_count
            for jid in job_ids:
                t_claim_start = time.perf_counter()
                claimed = shared_store.claim_job(jid, worker_id=worker_id, lease_timeout_seconds=300)
                t_claim_dur = (time.perf_counter() - t_claim_start) * 1000
                claim_latencies.append(t_claim_dur)

                if claimed:
                    worker_claims[worker_id].append(jid)
                    j = shared_store.get_job(jid)
                    if j is None:
                        continue

                    # Check if this was a recovery of the crashed job
                    if include_crash_scenario and jid == job_ids[0] and j.worker_id == worker_id:
                        recoveries_count += 1

                    q = j.parameters.get("query", "")
                    t0 = time.perf_counter()
                    try:
                        res = run_async_analysis_task(query=q, request_id=j.request_id)
                        dur = round((time.perf_counter() - t0) * 1000, 2)
                        execution_latencies.append(dur)

                        j.status = JobStatus.COMPLETED.value
                        j.duration_ms = dur
                        j.result = res
                        shared_store.update_job(j)
                    except Exception as exc:
                        failed_count += 1
                        j.status = JobStatus.FAILED.value
                        j.error = {"message": str(exc)}
                        shared_store.update_job(j)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(_worker_loop, f"worker_{w}") for w in range(num_workers)]
            for f in as_completed(futures):
                f.result()

    total_time_s = round(time.perf_counter() - t_start, 2)
    throughput = round(num_jobs / total_time_s, 2) if total_time_s > 0 else 0.0

    # 3. Duplicate and correctness verification
    all_claimed = []
    for claims in worker_claims.values():
        all_claimed.extend(claims)

    unique_claimed = len(set(all_claimed))
    duplicates = len(all_claimed) - unique_claimed
    percentiles = _calculate_percentiles(execution_latencies)
    claim_percentiles = _calculate_percentiles(claim_latencies)

    return {
        "workers": num_workers,
        "jobs": num_jobs,
        "completed": unique_claimed,
        "failed": failed_count,
        "duplicates": duplicates,
        "recoveries": recoveries_count,
        "total_time_s": total_time_s,
        "throughput_jobs_per_s": throughput,
        "mean_latency_ms": percentiles["mean"],
        "p50_latency_ms": percentiles["p50"],
        "p95_latency_ms": percentiles["p95"],
        "p99_latency_ms": percentiles["p99"],
        "avg_claim_latency_ms": claim_percentiles["mean"],
        "worker_distribution": {k: len(v) for k, v in worker_claims.items()},
    }


def run_distributed_benchmark() -> Dict[str, Any]:
    """Executes multi-workload distributed simulation suite across 1, 2, and 4 workers."""
    print("=" * 110)
    print("      PAYPILOT DISTRIBUTED MULTI-WORKER SIMULATION BENCHMARK (PHASE 17)      ")
    print("   [LOCAL MULTI-WORKER SIMULATION — NOT A PRODUCTION CAPACITY GUARANTEE]   ")
    print("=" * 110)

    shared_store = SQLJobStore()

    # Matrix configurations: (workers, jobs, crash_scenario)
    configurations = [
        (1, 20, False),
        (2, 50, False),
        (4, 20, False),
        (4, 50, False),
        (4, 100, False),
        (4, 20, True),  # Failure & recovery test
    ]

    results: List[Dict[str, Any]] = []

    print(f"{'Workers':<8} | {'Jobs':<6} | {'Completed':<10} | {'Failed':<7} | {'Duplicates':<11} | {'Recoveries':<11} | {'Mean (ms)':<10} | {'P95 (ms)':<9} | {'Throughput'}")
    print("-" * 110)

    for workers, jobs, crash in configurations:
        label_jobs = f"{jobs}*" if crash else str(jobs)
        res = execute_workload_run(
            num_workers=workers,
            num_jobs=jobs,
            shared_store=shared_store,
            include_crash_scenario=crash,
        )
        results.append(res)

        print(
            f"{res['workers']:<8} | "
            f"{label_jobs:<6} | "
            f"{res['completed']:<10} | "
            f"{res['failed']:<7} | "
            f"{res['duplicates']:<11} | "
            f"{res['recoveries']:<11} | "
            f"{res['mean_latency_ms']:<10.2f} | "
            f"{res['p95_latency_ms']:<9.2f} | "
            f"{res['throughput_jobs_per_s']:>6.2f} jobs/s"
        )

    print("=" * 110)
    print("(*) Indicates worker crash & lease expiration recovery scenario included.")
    print("=" * 110)

    # Validate zero duplicates guarantee across all runs
    total_duplicates = sum(r["duplicates"] for r in results)
    assert total_duplicates == 0, f"Critical failure: Detected {total_duplicates} duplicate executions across runs!"

    # Save benchmark report
    report = {
        "benchmark_type": "LOCAL_MULTI_WORKER_SIMULATION",
        "disclaimer": (
            "Throughput and latency values were measured using MockChatNVIDIA in a local "
            "thread-simulated environment. These values demonstrate correct concurrency, "
            "atomic lease locking, and recovery under contention, but do NOT represent "
            "real-world network I/O or production capacity guarantees."
        ),
        "total_runs": len(results),
        "results": results,
    }

    report_path = ROOT_DIR / "evaluation" / "distributed_benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Distributed benchmark report successfully saved to: {report_path}")
    print("=" * 110)

    return report


if __name__ == "__main__":
    run_distributed_benchmark()
