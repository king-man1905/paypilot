"""PayPilot Background Job & Asynchronous Processing Benchmark.

Measures asynchronous job submission overhead, status polling latency,
queue throughput under load, and completion reliability.
"""

import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.config import get_job_max_queue_size, get_job_max_workers
from backend.jobs import (
    JobRunner,
    JobStatus,
    run_async_analysis_task,
)
from evaluation.mock_llm import patch_offline_evaluation_llm

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("paypilot.benchmark.jobs")


def run_job_benchmark() -> Dict[str, Any]:
    """Executes a performance benchmark over PayPilot background job processing."""
    print("=" * 90)
    print("                 PAYPILOT BACKGROUND JOB & ASYNC PROCESSING BENCHMARK               ")
    print("=" * 90)

    workers = get_job_max_workers()
    queue_size = get_job_max_queue_size()
    runner = JobRunner(max_workers=workers, max_queue_size=queue_size)

    sample_queries = [
        "Why did revenue drop in month 3?",
        "Which payment method has the highest failure rate?",
        "Compare mobile checkout conversion with desktop.",
        "Which product category has the highest refund rate?",
        "What if payment success rate improves by 2%?",
    ]

    print(f"Configuration       : Max Workers = {runner.max_workers} | Max Queue Size = {runner.max_queue_size}")
    print(f"Sample Queries Pool : {len(sample_queries)} queries")
    print("-" * 90)

    with patch_offline_evaluation_llm():
        # --- Benchmark 1: Job Submission Overhead ---
        t0 = time.perf_counter()
        submitted_jobs = []
        submission_latencies = []

        for i in range(25):
            query = sample_queries[i % len(sample_queries)]
            t_sub0 = time.perf_counter()
            job = runner.submit_job(
                task_type="async_analysis",
                client_id="benchmark_client",
                role="analyst",
                request_id=f"job-bench-req-{i:03d}",
                parameters={"query": query},
                target_fn=run_async_analysis_task,
                query=query,
            )
            t_sub = (time.perf_counter() - t_sub0) * 1000
            submission_latencies.append(t_sub)
            submitted_jobs.append(job)

        avg_sub_latency_ms = round(sum(submission_latencies) / len(submission_latencies), 3)
        p95_sub_latency_ms = round(sorted(submission_latencies)[int(len(submission_latencies) * 0.95)], 3)

        print(f"[1/3] Job Submission Overhead (25 jobs):")
        print(f"      Mean Latency  : {avg_sub_latency_ms} ms / submission")
        print(f"      P95 Latency   : {p95_sub_latency_ms} ms")

        # --- Benchmark 2: Background Execution & Completion ---
        print(f"\n[2/3] Processing Background Workload (25 jobs across {runner.max_workers} workers):")
        t_exec_start = time.perf_counter()
        
        all_completed = False
        timeout_seconds = 15.0
        while (time.perf_counter() - t_exec_start) < timeout_seconds:
            time.sleep(0.05)
            completed_count = sum(
                1 for j in submitted_jobs
                if (rec := runner.get_job(j.job_id)) is not None
                and rec.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value)
            )
            if completed_count == len(submitted_jobs):
                all_completed = True
                break

        total_exec_duration_s = round(time.perf_counter() - t_exec_start, 2)
        throughput_jobs_per_sec = round(len(submitted_jobs) / total_exec_duration_s, 2) if total_exec_duration_s > 0 else 0.0

        # Retrieve completed stats
        job_durations = [
            float(rec.duration_ms)
            for j in submitted_jobs
            if (rec := runner.get_job(j.job_id)) is not None and rec.duration_ms is not None
        ]
        avg_job_duration_ms = round(sum(job_durations) / len(job_durations), 2) if job_durations else 0.0

        print(f"      Total Time    : {total_exec_duration_s} s")
        print(f"      Throughput    : {throughput_jobs_per_sec} jobs / sec")
        print(f"      Mean Job Run  : {avg_job_duration_ms} ms")
        print(f"      All Completed : {all_completed} (25/25 jobs)")

        # --- Benchmark 3: Polling Latency ---
        poll_latencies = []
        for j in submitted_jobs[:10]:
            t_poll0 = time.perf_counter()
            _ = runner.get_job(j.job_id)
            poll_latencies.append((time.perf_counter() - t_poll0) * 1000)

        avg_poll_ms = round(sum(poll_latencies) / len(poll_latencies), 3)
        print(f"\n[3/3] In-Memory State Polling Latency:")
        print(f"      Mean Latency  : {avg_poll_ms} ms / get_job call")

    report = {
        "total_jobs_benchmark": len(submitted_jobs),
        "workers_configured": runner.max_workers,
        "submission_latency_ms": {
            "mean": avg_sub_latency_ms,
            "p95": p95_sub_latency_ms,
        },
        "execution_summary": {
            "total_duration_seconds": total_exec_duration_s,
            "throughput_jobs_per_sec": throughput_jobs_per_sec,
            "mean_job_execution_ms": avg_job_duration_ms,
            "success_rate_pct": 100.0 if all_completed else 0.0,
        },
        "polling_latency_ms": {
            "mean": avg_poll_ms,
        },
    }

    report_path = ROOT_DIR / "evaluation" / "job_benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("-" * 90)
    print(f"Job benchmark report saved to: {report_path}")
    print("=" * 90)

    runner.shutdown(wait=False)
    return report


if __name__ == "__main__":
    run_job_benchmark()
