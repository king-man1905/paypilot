"""PayPilot Release Engineering, Versioned Migration & Rollback Microbenchmark (Phase 23).

[LOCAL BENCHMARK / SIMULATION — NOT A LIVE CLOUD INFRASTRUCTURE BENCHMARK]

Evaluates:
1. Versioned Migration Forward Application Latency & Ledger Integrity.
2. Migration Rollback & Schema Cleanliness Latency.
3. Candidate Release Pipeline Latency (Healthy Candidate -> Traffic Promotion).
4. Candidate Failure Recovery Latency (Faulty Candidate -> Promotion Block -> Automated Rollback).

Targets:
- 0 corrupted database schemas
- 0 unhandled migration failures
- 0 broken rollbacks
- 0 leaked secrets in reports
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Dict

# Ensure repository root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.storage.connection import create_db_engine
from backend.storage.versioned_migrator import VersionedMigrator
from evaluation.release_pipeline import execute_release_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paypilot.benchmark.release")

REPORT_PATH = Path(__file__).resolve().parent / "release_benchmark_report.json"


def benchmark_forward_migrations() -> Dict[str, Any]:
    """Measures forward migration speed and verifies ledger consistency."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        engine = create_db_engine(db_url=f"sqlite:///{db_path}")
        migrator = VersionedMigrator()

        t0 = time.perf_counter()
        apply_res = migrator.apply_migrations(engine=engine)
        dur_ms = round((time.perf_counter() - t0) * 1000, 3)

        statuses = migrator.get_status(engine=engine)
        all_applied = all(s["status"] == "applied" for s in statuses)
        checksum_valid = all(s["checksum_valid"] for s in statuses)

        return {
            "scenario": "forward_versioned_migrations",
            "migrations_applied": apply_res["migrations_applied_count"],
            "duration_ms": dur_ms,
            "all_applied_in_ledger": all_applied,
            "checksums_valid": checksum_valid,
            "corrupted_schemas": 0 if all_applied and checksum_valid else 1,
        }
    finally:
        Path(db_path).unlink(missing_ok=True)


def benchmark_migration_rollback() -> Dict[str, Any]:
    """Measures migration rollback speed and verifies clean schema revert."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        engine = create_db_engine(db_url=f"sqlite:///{db_path}")
        migrator = VersionedMigrator()

        # Apply all migrations first
        migrator.apply_migrations(engine=engine)

        # Roll back 1 migration step
        t0 = time.perf_counter()
        rollback_res = migrator.rollback(engine=engine, steps=1)
        dur_ms = round((time.perf_counter() - t0) * 1000, 3)

        statuses = migrator.get_status(engine=engine)
        rolled_back_version = rollback_res["rolled_back_migrations"][0]["version"] if rollback_res["rolled_back_migrations"] else ""
        target_status = next((s for s in statuses if s["version"] == rolled_back_version), None)

        is_pending = target_status is not None and target_status["status"] == "pending"

        return {
            "scenario": "versioned_migration_rollback",
            "migrations_rolled_back": rollback_res["migrations_rolled_back_count"],
            "rolled_back_version": rolled_back_version,
            "duration_ms": dur_ms,
            "ledger_updated_to_pending": is_pending,
            "broken_rollbacks": 0 if is_pending else 1,
        }
    finally:
        Path(db_path).unlink(missing_ok=True)


def benchmark_release_pipeline_promotion() -> Dict[str, Any]:
    """Measures release pipeline execution latency on a healthy candidate."""
    t0 = time.perf_counter()
    pipe_res = execute_release_pipeline(
        candidate_version="v1.23.0",
        stable_version="v1.22.0",
        simulate_failure=False,
        fast_mode=True,
    )
    dur_ms = round((time.perf_counter() - t0) * 1000, 3)

    is_promoted = pipe_res["final_action"] == "PROMOTED"
    return {
        "scenario": "healthy_candidate_promotion",
        "action": pipe_res["final_action"],
        "active_version": pipe_res["active_version"],
        "duration_ms": dur_ms,
        "stages_passed": pipe_res["passed_stages"],
        "total_stages": pipe_res["total_stages"],
        "unhandled_failures": 0 if is_promoted else 1,
    }


def benchmark_release_pipeline_automatic_rollback() -> Dict[str, Any]:
    """Measures promotion block and automated rollback latency on a failing candidate."""
    t0 = time.perf_counter()
    pipe_res = execute_release_pipeline(
        candidate_version="v1.23.1-faulty",
        stable_version="v1.23.0",
        simulate_failure=True,
        fast_mode=True,
    )
    dur_ms = round((time.perf_counter() - t0) * 1000, 3)

    is_rolled_back = pipe_res["final_action"] == "ROLLED_BACK"
    active_is_stable = pipe_res["active_version"] == "v1.23.0"

    return {
        "scenario": "faulty_candidate_automatic_rollback",
        "action": pipe_res["final_action"],
        "active_version": pipe_res["active_version"],
        "failed_candidate": "v1.23.1-faulty",
        "duration_ms": dur_ms,
        "rollback_success": is_rolled_back and active_is_stable,
        "unhandled_failures": 0 if is_rolled_back and active_is_stable else 1,
    }


def run_all_release_benchmarks() -> Dict[str, Any]:
    """Executes all Phase 23 release engineering microbenchmarks and writes report."""
    logger.info("Running PayPilot Release Engineering & Migration Benchmarks...")

    fwd_res = benchmark_forward_migrations()
    rollback_res = benchmark_migration_rollback()
    promote_res = benchmark_release_pipeline_promotion()
    fault_res = benchmark_release_pipeline_automatic_rollback()

    total_corrupted = fwd_res["corrupted_schemas"]
    total_broken_rollbacks = rollback_res["broken_rollbacks"]
    total_unhandled = promote_res["unhandled_failures"] + fault_res["unhandled_failures"]

    all_passed = total_corrupted == 0 and total_broken_rollbacks == 0 and total_unhandled == 0

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_environment": "LOCAL BENCHMARK / SIMULATION",
        "overall_status": "PASSED" if all_passed else "FAILED",
        "targets": {
            "target_corrupted_schemas": 0,
            "target_broken_rollbacks": 0,
            "target_unhandled_failures": 0,
        },
        "measured_results": {
            "total_corrupted_schemas": total_corrupted,
            "total_broken_rollbacks": total_broken_rollbacks,
            "total_unhandled_failures": total_unhandled,
        },
        "scenarios": [
            fwd_res,
            rollback_res,
            promote_res,
            fault_res,
        ],
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Release benchmark report successfully written to {REPORT_PATH}")
    return report


if __name__ == "__main__":
    rep = run_all_release_benchmarks()
    print("\n" + "=" * 90)
    print("           PAYPILOT RELEASE ENGINEERING & MIGRATION BENCHMARK REPORT")
    print("=" * 90)
    print(f"Status: {rep['overall_status']}")
    print(f"Total Corrupted Schemas : {rep['measured_results']['total_corrupted_schemas']} (Target: 0)")
    print(f"Total Broken Rollbacks  : {rep['measured_results']['total_broken_rollbacks']} (Target: 0)")
    print(f"Total Unhandled Failures: {rep['measured_results']['total_unhandled_failures']} (Target: 0)")
    print("=" * 90)
    for sc in rep["scenarios"]:
        print(f" - {sc['scenario']}: {sc}")
    print("=" * 90)
    if rep["overall_status"] != "PASSED":
        sys.exit(1)
    sys.exit(0)
