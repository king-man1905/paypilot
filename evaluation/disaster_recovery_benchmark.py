"""PayPilot Disaster Recovery & Data Resilience Benchmark (Phase 18).

Measures backup snapshot creation latency, SHA-256 cryptographic verification speed,
disaster recovery restoration duration, and 100.0% post-restore financial metrics parity.

NOTE: This benchmark is a LOCAL DISASTER RECOVERY SIMULATION using local SQLite/CSV
storage and MockChatNVIDIA. It tests algorithmic and cryptographic resilience, NOT a
production cloud disaster recovery guarantee.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.storage.backup import (
    create_database_backup,
    list_backups,
    verify_backup_integrity,
)
from backend.storage.connection import dispose_engine as reset_db_engine
from backend.storage.migrator import seed_database_from_csv

from backend.storage.repository import SQLTransactionRepository
from backend.storage.restore import (
    compute_core_financial_metrics,
    restore_database_from_backup,
    validate_restore_integrity,
)
from backend.storage.validator import validate_dataset_integrity
from evaluation.mock_llm import patch_offline_evaluation_llm

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("paypilot.benchmark.disaster_recovery")


def run_disaster_recovery_benchmark() -> Dict[str, Any]:
    """Executes the automated disaster recovery benchmark suite."""
    print("=" * 100)
    print("         PAYPILOT DISASTER RECOVERY & DATA RESILIENCE BENCHMARK (PHASE 18)         ")
    print("        [LOCAL DISASTER RECOVERY SIMULATION — NOT A CLOUD SLA GUARANTEE]        ")
    print("=" * 100)

    benchmark_dir = ROOT_DIR / "data" / "benchmark_dr"
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    bench_db_file = benchmark_dir / "bench_paypilot.db"
    bench_db_url = f"sqlite:///{bench_db_file}"
    backup_storage_dir = benchmark_dir / "backups"
    backup_storage_dir.mkdir(parents=True, exist_ok=True)

    # 1. Seed database
    print("\n[1/5] Initializing Test Database & Establishing Financial Baseline...")
    t0_seed = time.perf_counter()
    seed_database_from_csv(database_url=bench_db_url, overwrite=True)
    seed_duration_ms = round((time.perf_counter() - t0_seed) * 1000, 2)
    reset_db_engine()

    repo = SQLTransactionRepository(database_url=bench_db_url)
    baseline_metrics = compute_core_financial_metrics(repo)
    total_txns = int(baseline_metrics["total_transactions"])
    realized_rev = baseline_metrics["total_realized_revenue"]

    print(f"      Database Seeded   : {total_txns} transactions in {seed_duration_ms} ms")
    print(f"      Realized Revenue  : INR {realized_rev:,.2f}")
    print(f"      Success Rate      : {baseline_metrics['payment_success_rate'] * 100:.2f}%")

    # 2. Measure Backup Creation
    print("\n[2/5] Executing Automated Database Backup & Manifest Generation...")
    t0_bkp = time.perf_counter()
    meta = create_database_backup(
        target_dir=backup_storage_dir,
        backup_name="bench_dr_snap",
        source_db_url=bench_db_url,
    )
    backup_duration_ms = round((time.perf_counter() - t0_bkp) * 1000, 2)

    print(f"      Backup ID         : {meta.backup_id}")
    print(f"      Backup Size       : {meta.size_bytes / (1024 * 1024):.2f} MB ({meta.size_bytes} bytes)")
    print(f"      SHA-256 Digest    : {meta.sha256_checksum[:16]}...{meta.sha256_checksum[-8:]}")
    print(f"      Backup Duration   : {backup_duration_ms} ms")

    # 3. Measure Backup Verification Speed
    print("\n[3/5] Validating Backup Cryptographic Integrity...")
    t0_verify = time.perf_counter()
    is_valid, verify_msg = verify_backup_integrity(meta, backup_dir=backup_storage_dir)
    verify_duration_ms = round((time.perf_counter() - t0_verify) * 1000, 2)

    print(f"      Verification Status: {'PASS' if is_valid else 'FAIL'} ({verify_msg})")
    print(f"      Verify Duration   : {verify_duration_ms} ms")
    assert is_valid is True, f"Integrity verification failed: {verify_msg}"

    # 4. Simulate Disaster & Measure Restoration Duration
    print("\n[4/5] Simulating Database Corruption & Measuring Restore...")
    reset_db_engine()
    # Simulate corruption
    with open(bench_db_file, "wb") as f:
        f.write(b"CORRUPTED_DISASTER_RECOVERY_SIMULATION_BYTES")

    t0_restore = time.perf_counter()
    restore_res = restore_database_from_backup(
        meta,
        target_db_url=bench_db_url,
        backup_dir=backup_storage_dir,
    )
    restore_duration_ms = round((time.perf_counter() - t0_restore) * 1000, 2)

    print(f"      Restore Status    : {restore_res['status'].upper()}")
    print(f"      Restore Duration  : {restore_duration_ms} ms")

    # 5. Measure Post-Restore Financial Parity Validation
    print("\n[5/5] Performing Post-Restore Financial Parity & Schema Audit...")
    t0_parity = time.perf_counter()
    val_res = validate_restore_integrity(
        baseline_metrics=baseline_metrics,
        expected_txns=total_txns,
        target_db_url=bench_db_url,
    )
    parity_duration_ms = round((time.perf_counter() - t0_parity) * 1000, 2)


    print(f"      Primary Key Dupes : {val_res['duplicate_primary_keys']} (Target: 0)")
    print(f"      Metrics Evaluated : {val_res['metrics_evaluated']} business metrics")
    print(f"      Financial Parity  : {val_res['metrics_parity_pct']:.2f}% (Target: 100.0%)")
    print(f"      Discrepancies     : {len(val_res['discrepancies'])}")
    print(f"      Parity Audit Time : {parity_duration_ms} ms")

    assert val_res["valid"] is True, f"Post-restore validation failed: {val_res['discrepancies']}"
    assert val_res["metrics_parity_pct"] == 100.0, "Financial metrics parity was not 100.0%!"

    # 6. Data Integrity Hygiene Audit
    print("\n[6/6] Running Data Integrity Hygiene Validator...")
    val_data = validate_dataset_integrity(repo)
    print(f"      Data Valid        : {val_data['is_valid']}")
    print(f"      Total Anomalies   : {val_data['total_issues']}")

    # Clean up benchmark artifacts
    try:
        shutil.rmtree(benchmark_dir)
    except Exception:
        pass

    # Save benchmark report
    report = {
        "benchmark_type": "LOCAL_DISASTER_RECOVERY_SIMULATION",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "disclaimer": (
            "Measured locally using SQLite and MockChatNVIDIA. Demonstrates cryptographic "
            "integrity, snapshot restore speed, and 100% financial parity under simulated "
            "corruption, but does not represent cloud multi-region automated failover."
        ),
        "dataset_rows": total_txns,
        "backup_duration_ms": backup_duration_ms,
        "backup_size_bytes": meta.size_bytes,
        "verify_duration_ms": verify_duration_ms,
        "restore_duration_ms": restore_duration_ms,
        "parity_audit_duration_ms": parity_duration_ms,
        "financial_parity_pct": val_res["metrics_parity_pct"],
        "duplicate_primary_keys": val_res["duplicate_primary_keys"],
        "post_restore_metrics": val_res["current_metrics"],
        "rto_local_measured_ms": backup_duration_ms + restore_duration_ms + verify_duration_ms,
        "rpo_local_measured": "0s (Point-in-time snapshot)",
    }

    report_path = ROOT_DIR / "evaluation" / "disaster_recovery_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 100)
    print(f"Disaster recovery benchmark report successfully saved to: {report_path}")
    print("=" * 100)

    return report


if __name__ == "__main__":
    run_disaster_recovery_benchmark()
