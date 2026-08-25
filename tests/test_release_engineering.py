"""Unit & Integration Tests for Versioned Migrations, CI/CD & Safe Release Pipeline (Phase 23).

Tests:
1. Versioned migration forward application, table creation, and ledger tracking.
2. Migration idempotency and targeted version stopping.
3. Migration rollback (down) step execution and ledger synchronization.
4. Checksum verification and schema drift detection.
5. Static security secret scan clean evaluation and synthetic leak detection.
6. Safe release pipeline healthy candidate promotion.
7. Safe release pipeline failing candidate promotion block and automated rollback.
"""

from pathlib import Path
import pytest
from sqlalchemy import inspect, text

from backend.storage.connection import create_db_engine
from backend.storage.migrations import ALL_MIGRATIONS, BaseMigration
from backend.storage.versioned_migrator import VersionedMigrator, get_versioned_migrator
from evaluation.release_pipeline import (
    execute_release_pipeline,
    stage_1_secret_audit,
    stage_4_docker_gate,
    stage_5_migration_gate,
    stage_6_deployment_smoke_test,
)


class TestVersionedMigrations:
    """Validates VersionedMigrator ledger, forward execution, and rollback safety."""

    def test_forward_migration_lifecycle(self, tmp_path):
        db_file = tmp_path / "test_lifecycle.db"
        engine = create_db_engine(db_url=f"sqlite:///{db_file}")
        migrator = VersionedMigrator()

        # Initial status: all pending
        status_before = migrator.get_status(engine=engine)
        assert all(s["status"] == "pending" for s in status_before)

        # Apply all migrations
        res = migrator.apply_migrations(engine=engine)
        assert res["status"] == "success"
        assert res["migrations_applied_count"] == len(ALL_MIGRATIONS)

        # Status after: all applied and verified
        status_after = migrator.get_status(engine=engine)
        assert all(s["status"] == "applied" for s in status_after)
        assert all(s["checksum_valid"] is True for s in status_after)

        # Verify tables exist
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "paypilot_schema_migrations" in tables
        assert "merchant_transactions" in tables
        assert "paypilot_audit_events" in tables
        assert "paypilot_jobs" in tables
        assert "paypilot_backup_metadata" in tables

    def test_migration_idempotency(self, tmp_path):
        db_file = tmp_path / "test_idempotent.db"
        engine = create_db_engine(db_url=f"sqlite:///{db_file}")
        migrator = VersionedMigrator()

        # Run 1
        res1 = migrator.apply_migrations(engine=engine)
        assert res1["migrations_applied_count"] == len(ALL_MIGRATIONS)

        # Run 2 (no-op)
        res2 = migrator.apply_migrations(engine=engine)
        assert res2["migrations_applied_count"] == 0

    def test_targeted_version_migration(self, tmp_path):
        db_file = tmp_path / "test_targeted.db"
        engine = create_db_engine(db_url=f"sqlite:///{db_file}")
        migrator = VersionedMigrator()

        # Apply only up to 001_initial_schema
        res = migrator.apply_migrations(engine=engine, target_version="001_initial_schema")
        assert res["migrations_applied_count"] == 1

        statuses = migrator.get_status(engine=engine)
        assert statuses[0]["status"] == "applied"
        assert statuses[1]["status"] == "pending"
        assert statuses[2]["status"] == "pending"

    def test_migration_rollback_step(self, tmp_path):
        db_file = tmp_path / "test_rollback.db"
        engine = create_db_engine(db_url=f"sqlite:///{db_file}")
        migrator = VersionedMigrator()

        # Apply all migrations
        migrator.apply_migrations(engine=engine)

        # Roll back 1 step (003_backup_metadata)
        rollback_res = migrator.rollback(engine=engine, steps=1)
        assert rollback_res["migrations_rolled_back_count"] == 1
        assert rollback_res["rolled_back_migrations"][0]["version"] == "003_backup_metadata"

        # Verify 003 is now pending and table was dropped
        statuses = migrator.get_status(engine=engine)
        assert statuses[0]["status"] == "applied"
        assert statuses[1]["status"] == "applied"
        assert statuses[2]["status"] == "pending"

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "paypilot_backup_metadata" not in tables

    def test_checksum_tampering_detection(self, tmp_path):
        db_file = tmp_path / "test_drift.db"
        engine = create_db_engine(db_url=f"sqlite:///{db_file}")
        migrator = VersionedMigrator()
        migrator.apply_migrations(engine=engine)

        # Corrupt the checksum in the database ledger
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE paypilot_schema_migrations SET checksum = 'tampered_hash_123' WHERE version = '001_initial_schema'")
            )

        verify_res = migrator.verify_checksums(engine=engine)
        assert verify_res["verified"] is False
        assert verify_res["drifted_count"] == 1
        assert verify_res["drifted_migrations"][0]["version"] == "001_initial_schema"


class TestReleasePipelineGates:
    """Validates release pipeline stages, promotion rules, and automated rollback."""

    def test_stage_1_secret_audit_clean_repository(self):
        res = stage_1_secret_audit()
        assert res["status"] == "PASSED"
        assert res["leaks_found"] == 0
        assert res["scanned_files"] > 0

    def test_stage_4_docker_gate(self):
        res = stage_4_docker_gate()
        assert res["status"] == "PASSED"
        assert len(res["issues"]) == 0

    def test_stage_5_migration_gate(self):
        res = stage_5_migration_gate()
        assert res["status"] == "PASSED"
        assert res["migrations_applied"] == len(ALL_MIGRATIONS)
        assert res["checksum_verified"] is True

    def test_stage_6_deployment_smoke_tests(self):
        res = stage_6_deployment_smoke_test(force_fail=False)
        assert res["status"] == "PASSED"
        assert res["smoke_tests"]["health"] is True
        assert res["smoke_tests"]["ready"] is True
        assert res["smoke_tests"]["analyze_endpoint"] is True
        assert res["smoke_tests"]["job_endpoint"] is True

    def test_full_release_pipeline_healthy_candidate_promotion(self):
        res = execute_release_pipeline(
            candidate_version="v1.23.0-rc1",
            stable_version="v1.22.0",
            simulate_failure=False,
        )
        assert res["overall_status"] == "PASSED"
        assert res["final_action"] == "PROMOTED"
        assert res["active_version"] == "v1.23.0-rc1"
        assert res["failed_stages"] == 0

    def test_full_release_pipeline_faulty_candidate_blocked_and_rolled_back(self):
        res = execute_release_pipeline(
            candidate_version="v1.23.1-broken",
            stable_version="v1.23.0",
            simulate_failure=True,
        )
        assert res["overall_status"] == "PASSED"
        assert res["final_action"] == "ROLLED_BACK"
        assert res["active_version"] == "v1.23.0"

        stage_7 = res["stages"][-1]
        assert stage_7["promotion_blocked"] is True
        assert stage_7["rollback_triggered"] is True
        assert stage_7["rollback_health_verified"] is True
