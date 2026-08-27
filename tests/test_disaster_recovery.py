"""Automated Test Suite for PayPilot Disaster Recovery, Backup & Data Resilience (Phase 18).

Validates backup creation, SHA-256 cryptographic verification, corrupted backup detection,
database restoration, 100.0% post-restore financial parity, data integrity validation,
persistent SQL audit logging, job recovery across restarts, and secret non-exposure.
"""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import pytest
import pandas as pd

from backend.config import ROOT_DIR, get_database_url
from backend.observability.audit import AuditEvent, SQLAuditStore, get_audit_store, set_audit_store
from backend.jobs import JobRecord, JobStatus
from backend.jobs.store import SQLJobStore
from backend.storage.backup import (
    BackupMetadata,
    calculate_sha256,
    create_database_backup,
    list_backups,
    prune_backups,
    verify_backup_integrity,
)
from backend.storage.connection import (
    dispose_engine as reset_db_engine,
    get_db_engine,
    get_db_session,
)
from backend.storage.migrator import seed_database_from_csv

from backend.storage.models import AuditEventModel, Base, TransactionModel
from backend.storage.repository import SQLTransactionRepository
from backend.storage.restore import (
    compute_core_financial_metrics,
    restore_database_from_backup,
    validate_restore_integrity,
)
from backend.storage.validator import validate_dataset_integrity


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Initializes isolated test database and backup directory."""
    test_db_file = tmp_path / "test_dr.db"
    test_db_url = f"sqlite:///{test_db_file}"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Seed test database
    seed_database_from_csv(database_url=test_db_url, overwrite=True)
    reset_db_engine()

    yield {
        "db_file": test_db_file,
        "db_url": test_db_url,
        "backup_dir": backup_dir,
    }

    reset_db_engine()


def test_backup_metadata_creation_and_checksum(setup_test_db, monkeypatch):
    """Verifies automated database backup creation, metadata manifest, and SHA-256 calculation."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    monkeypatch.setenv("BACKUP_DIR", str(db_info["backup_dir"]))

    meta = create_database_backup(target_dir=db_info["backup_dir"])

    assert meta.backup_id.startswith("bkp_")
    assert meta.size_bytes > 0
    assert len(meta.sha256_checksum) == 64
    assert meta.transaction_count > 0

    # Verify manifest file on disk
    manifest_path = db_info["backup_dir"] / f"{meta.backup_file}.meta.json"
    assert manifest_path.exists()

    with open(manifest_path, "r", encoding="utf-8") as f:
        saved_dict = json.load(f)
    assert saved_dict["backup_id"] == meta.backup_id
    assert saved_dict["sha256_checksum"] == meta.sha256_checksum


def test_backup_integrity_verification_success_and_failure(setup_test_db, monkeypatch):
    """Verifies that intact backups pass validation and corrupted/tampered files fail."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("DATA_BACKEND", "sqlite")

    meta = create_database_backup(target_dir=db_info["backup_dir"])

    # 1. Verification of untouched backup passes
    valid, msg = verify_backup_integrity(meta, backup_dir=db_info["backup_dir"])
    assert valid is True
    assert "verified successfully" in msg

    # 2. Tampering with backup file causes checksum failure
    backup_file_path = db_info["backup_dir"] / meta.backup_file
    with open(backup_file_path, "ab") as f:
        f.write(b"\x00CORRUPT_BYTE_DATA")

    valid_corrupt, msg_corrupt = verify_backup_integrity(meta, backup_dir=db_info["backup_dir"])
    assert valid_corrupt is False
    assert "Checksum mismatch" in msg_corrupt


def test_restore_database_and_financial_metrics_parity(setup_test_db, monkeypatch):
    """Verifies that restoring from backup preserves 100.0% numerical truth across core financial metrics."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("DATA_BACKEND", "sqlite")

    # 1. Establish pre-backup financial metrics baseline
    repo = SQLTransactionRepository(database_url=db_info["db_url"])
    baseline_metrics = compute_core_financial_metrics(repo)
    initial_txns = baseline_metrics["total_transactions"]
    assert initial_txns > 0

    # 2. Create backup
    meta = create_database_backup(target_dir=db_info["backup_dir"])

    # 3. Simulate database wipe / corruption
    corrupt_dest = db_info["db_file"]
    reset_db_engine()
    with open(corrupt_dest, "wb") as f:
        f.write(b"CORRUPTED_DATABASE_GARBAGE")

    # 4. Restore database from verified backup
    restore_res = restore_database_from_backup(
        meta,
        target_db_url=db_info["db_url"],
        backup_dir=db_info["backup_dir"],
    )
    assert restore_res["status"] == "restored"

    # 5. Validate post-restore financial integrity
    val_res = validate_restore_integrity(baseline_metrics=baseline_metrics)
    assert val_res["valid"] is True
    assert val_res["duplicate_primary_keys"] == 0
    assert val_res["metrics_parity_pct"] == 100.0
    assert len(val_res["discrepancies"]) == 0


def test_corrupted_backup_restore_rejection(setup_test_db, monkeypatch):
    """Ensures restore_database_from_backup strictly aborts on corrupted backups."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("DATA_BACKEND", "sqlite")

    meta = create_database_backup(target_dir=db_info["backup_dir"])

    # Corrupt backup
    backup_file_path = db_info["backup_dir"] / meta.backup_file
    with open(backup_file_path, "wb") as f:
        f.write(b"INVALID_SQLITE_HEADER")

    with pytest.raises(ValueError, match="integrity failure"):
        restore_database_from_backup(
            meta,
            target_db_url=db_info["db_url"],
            backup_dir=db_info["backup_dir"],
        )


def test_data_integrity_validator_rules():
    """Verifies that validate_dataset_integrity detects duplicate IDs, nulls, negative amounts, and bad statuses."""
    # 1. Clean valid dataset
    clean_df = pd.DataFrame({
        "transaction_id": ["txn_001", "txn_002", "txn_003"],
        "timestamp": ["2026-08-01 10:00:00", "2026-08-01 11:00:00", "2026-08-01 12:00:00"],
        "merchant_id": ["m_1", "m_1", "m_2"],
        "customer_id": ["c_1", "c_2", "c_3"],
        "amount": [100.0, 250.5, 50.0],
        "payment_method": ["UPI", "Credit Card", "Debit Card"],
        "payment_status": ["SUCCESS", "FAILED", "SUCCESS"],
        "device_type": ["Mobile", "Desktop", "Mobile"],
        "customer_type": ["New", "Returning", "VIP"],
        "product_category": ["Electronics", "Fashion", "Beauty"],
    })
    res_clean = validate_dataset_integrity(clean_df)
    assert res_clean["is_valid"] is True
    assert res_clean["total_issues"] == 0

    # 2. Corrupted dataset with anomalies
    bad_df = pd.DataFrame({
        "transaction_id": ["txn_001", "txn_001", "txn_003"],  # Duplicate ID
        "timestamp": ["2026-08-01 10:00:00", "INVALID_DATE", "2026-08-01 12:00:00"],  # Bad timestamp
        "merchant_id": ["m_1", None, "m_2"],  # Null value
        "customer_id": ["c_1", "c_2", "c_3"],
        "amount": [100.0, -50.0, 50.0],  # Negative amount
        "payment_method": ["UPI", "Credit Card", "Debit Card"],
        "payment_status": ["SUCCESS", "UNKNOWN_STATUS", "SUCCESS"],  # Invalid status
        "device_type": ["Mobile", "Desktop", "Mobile"],
        "customer_type": ["New", "Returning", "VIP"],
        "product_category": ["Electronics", "Fashion", "Beauty"],
    })
    res_bad = validate_dataset_integrity(bad_df)
    assert res_bad["is_valid"] is False
    assert res_bad["total_issues"] > 0
    assert res_bad["checks"]["duplicate_transaction_ids"]["count"] == 1
    assert res_bad["checks"]["invalid_amounts"]["count"] == 1
    assert res_bad["checks"]["invalid_payment_status"]["count"] == 1


def test_sql_audit_store_durability_across_restarts(setup_test_db, monkeypatch):
    """Verifies that SQLAuditStore preserves compliance records across simulated restarts."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("AUDIT_STORE_BACKEND", "sql")

    # 1. Record events into SQL audit store
    store1 = SQLAuditStore()
    store1.reset()

    ev1 = AuditEvent(
        event_id="aud_test_001",
        request_id="req_001",
        endpoint="/api/v1/analyze",
        client_id="merchant_alpha",
        role="analyst",
        query_summary="Analyze revenue drop",
    )
    ev2 = AuditEvent(
        event_id="aud_test_002",
        request_id="req_002",
        endpoint="/api/v1/jobs",
        client_id="merchant_beta",
        role="admin",
        query_summary="Check background jobs",
    )
    store1.record_event(ev1)
    store1.record_event(ev2)
    assert store1.count() == 2

    # 2. Simulate process restart / new instance connection
    store2 = SQLAuditStore()
    assert store2.count() == 2

    events = store2.get_events(limit=10)
    event_ids = [e.event_id for e in events]
    assert "aud_test_001" in event_ids
    assert "aud_test_002" in event_ids

    # Verify query by ID
    fetched = store2.get_event_by_id("aud_test_001")
    assert fetched is not None
    assert fetched.client_id == "merchant_alpha"


def test_job_recovery_across_database_restart(setup_test_db, monkeypatch):
    """Verifies that background jobs in all lifecycle states survive restarts and stale leases recover."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])

    store1 = SQLJobStore()
    store1.reset()

    # Seed jobs
    j_queued = JobRecord(job_id="job_q1", status=JobStatus.QUEUED.value, parameters={"query": "q1"})
    j_running = JobRecord(
        job_id="job_r1",
        status=JobStatus.RUNNING.value,
        worker_id="crashed_node",
        started_at=(datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat(),
        parameters={"query": "r1"},
    )
    j_completed = JobRecord(
        job_id="job_c1",
        status=JobStatus.COMPLETED.value,
        result={"summary": "done"},
        parameters={"query": "c1"},
    )
    j_failed = JobRecord(
        job_id="job_f1",
        status=JobStatus.FAILED.value,
        error={"message": "network timeout"},
        parameters={"query": "f1"},
    )

    store1.save_job(j_queued)
    store1.save_job(j_running)
    store1.save_job(j_completed)
    store1.save_job(j_failed)
    assert store1.count() == 4

    # 2. Simulate restart / reconnect
    store2 = SQLJobStore()
    assert store2.count() == 4

    # Stale running job can be recovered
    recovered_count = store2.recover_stale_jobs(lease_timeout_seconds=300)
    assert recovered_count == 1

    # Check that job_r1 is now QUEUED and recoverable
    recovered_job = store2.get_job("job_r1")
    assert recovered_job is not None
    assert recovered_job.status == JobStatus.QUEUED.value

    # Completed and failed jobs retain payloads
    job_c1 = store2.get_job("job_c1")
    assert job_c1 is not None
    assert job_c1.result == {"summary": "done"}
    job_f1 = store2.get_job("job_f1")
    assert job_f1 is not None
    assert job_f1.error == {"message": "network timeout"}


def test_secret_omission_in_backup_manifests(setup_test_db, monkeypatch):
    """Verifies that database credentials and secrets are scrubbed from backup metadata."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", "postgresql://admin_user:super_secret_password@db.internal:5432/paypilot")
    monkeypatch.setenv("DATA_BACKEND", "postgres")

    meta = create_database_backup(target_dir=db_info["backup_dir"])
    meta_dict = meta.to_dict()

    # Verify no raw password leaked
    assert "super_secret_password" not in json.dumps(meta_dict)
    assert "***:***" in meta.source_identifier


def test_backup_pruning_lifecycle(setup_test_db, monkeypatch):
    """Verifies that prune_backups deletes older backups based on retention policy."""
    db_info = setup_test_db
    monkeypatch.setenv("DATABASE_URL", db_info["db_url"])
    monkeypatch.setenv("DATA_BACKEND", "sqlite")

    # Create backup 1 (old)
    meta1 = create_database_backup(target_dir=db_info["backup_dir"], backup_name="old_backup")
    meta1_path = db_info["backup_dir"] / f"{meta1.backup_file}.meta.json"
    
    # Backdate meta1 timestamp to 10 days ago
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with open(meta1_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    d["timestamp"] = old_ts
    with open(meta1_path, "w", encoding="utf-8") as f:
        json.dump(d, f)

    # Create backup 2 (current)
    meta2 = create_database_backup(target_dir=db_info["backup_dir"], backup_name="new_backup")

    all_backups = list_backups(db_info["backup_dir"])
    assert len(all_backups) >= 2

    # Prune with 7-day retention
    pruned = prune_backups(retention_days=7, backup_dir=db_info["backup_dir"])
    assert pruned == 1

    remaining = list_backups(db_info["backup_dir"])
    remaining_ids = [b.backup_id for b in remaining]
    assert meta1.backup_id not in remaining_ids
    assert meta2.backup_id in remaining_ids
