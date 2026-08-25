"""PayPilot Versioned Database Migration Engine & Ledger (Phase 23).

Manages forward schema evolution, rollback recovery, cryptographic checksum drift
detection, and migration execution ledger tracking in `paypilot_schema_migrations`.
"""

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    delete,
    desc,
    insert,
    select,
    text,
)
from sqlalchemy.engine import Engine

from backend.storage.connection import get_db_engine
from backend.storage.migrations import ALL_MIGRATIONS, BaseMigration
from backend.storage.models import Base

logger = logging.getLogger("paypilot.storage.versioned_migrator")


class SchemaMigrationModel(Base):
    """Declarative relational model representing applied schema migrations in the ledger."""

    __tablename__ = "paypilot_schema_migrations"

    version = Column(String(64), primary_key=True, nullable=False)
    description = Column(String(256), nullable=False)
    checksum = Column(String(64), nullable=False)
    applied_at = Column(String(64), nullable=False)
    execution_time_ms = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="applied")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "checksum": self.checksum,
            "applied_at": self.applied_at,
            "execution_time_ms": self.execution_time_ms,
            "status": self.status,
        }


class VersionedMigrator:
    """Orchestrates ordered schema migrations with ledger tracking and rollback safety."""

    def __init__(self, migrations: Optional[List[BaseMigration]] = None) -> None:
        self.migrations: List[BaseMigration] = migrations if migrations is not None else list(ALL_MIGRATIONS)
        self._lock = threading.Lock()

    def _ensure_ledger_table(self, engine: Engine) -> None:
        """Ensures the schema migrations ledger table exists."""
        metadata = MetaData()
        Table(
            "paypilot_schema_migrations",
            metadata,
            Column("version", String(64), primary_key=True, nullable=False),
            Column("description", String(256), nullable=False),
            Column("checksum", String(64), nullable=False),
            Column("applied_at", String(64), nullable=False),
            Column("execution_time_ms", Float, nullable=False, default=0.0),
            Column("status", String(32), nullable=False, default="applied"),
        )
        metadata.create_all(engine)

    def get_applied_versions(self, engine: Optional[Engine] = None) -> Dict[str, Dict[str, Any]]:
        """Retrieves currently applied migrations from the ledger."""
        db_engine = engine or get_db_engine()
        self._ensure_ledger_table(db_engine)

        with db_engine.connect() as conn:
            stmt = select(
                text("version"),
                text("description"),
                text("checksum"),
                text("applied_at"),
                text("execution_time_ms"),
                text("status"),
            ).select_from(text("paypilot_schema_migrations"))
            rows = conn.execute(stmt).fetchall()

        applied = {}
        for row in rows:
            applied[str(row[0])] = {
                "version": str(row[0]),
                "description": str(row[1]),
                "checksum": str(row[2]),
                "applied_at": str(row[3]),
                "execution_time_ms": float(row[4] or 0.0),
                "status": str(row[5]),
            }
        return applied

    def get_status(self, engine: Optional[Engine] = None) -> List[Dict[str, Any]]:
        """Returns the migration status of all registered migrations."""
        db_engine = engine or get_db_engine()
        applied = self.get_applied_versions(db_engine)

        statuses = []
        for m in self.migrations:
            rec = applied.get(m.version)
            is_applied = rec is not None and rec["status"] == "applied"
            statuses.append({
                "version": m.version,
                "description": m.description,
                "code_checksum": m.compute_checksum(),
                "db_checksum": rec["checksum"] if rec else None,
                "status": "applied" if is_applied else "pending",
                "applied_at": rec["applied_at"] if rec else None,
                "execution_time_ms": rec["execution_time_ms"] if rec else None,
                "checksum_valid": rec["checksum"] == m.compute_checksum() if rec else True,
            })
        return statuses

    def apply_migrations(
        self,
        engine: Optional[Engine] = None,
        target_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Applies pending migrations sequentially up to target_version."""
        with self._lock:
            db_engine = engine or get_db_engine()
            self._ensure_ledger_table(db_engine)
            applied = self.get_applied_versions(db_engine)

            applied_in_run = []
            start_total = time.perf_counter()

            for m in self.migrations:
                if m.version in applied and applied[m.version]["status"] == "applied":
                    # Already applied
                    if target_version and m.version == target_version:
                        break
                    continue

                logger.info(f"Applying migration '{m.version}': {m.description}...")
                t0 = time.perf_counter()

                # Execute migration up()
                m.up(db_engine)
                duration_ms = round((time.perf_counter() - t0) * 1000, 2)

                now_iso = datetime.now(timezone.utc).isoformat()
                chk = m.compute_checksum()

                # Record in ledger table
                with db_engine.begin() as conn:
                    # Delete any previous record with same version
                    conn.execute(
                        text("DELETE FROM paypilot_schema_migrations WHERE version = :ver"),
                        {"ver": m.version},
                    )
                    conn.execute(
                        text(
                            "INSERT INTO paypilot_schema_migrations "
                            "(version, description, checksum, applied_at, execution_time_ms, status) "
                            "VALUES (:ver, :desc, :chk, :app, :dur, :stat)"
                        ),
                        {
                            "ver": m.version,
                            "desc": m.description,
                            "chk": chk,
                            "app": now_iso,
                            "dur": duration_ms,
                            "stat": "applied",
                        },
                    )

                applied_in_run.append({
                    "version": m.version,
                    "description": m.description,
                    "checksum": chk,
                    "duration_ms": duration_ms,
                    "applied_at": now_iso,
                })
                logger.info(f"Successfully applied migration '{m.version}' in {duration_ms}ms.")

                if target_version and m.version == target_version:
                    break

            total_duration_ms = round((time.perf_counter() - start_total) * 1000, 2)
            return {
                "status": "success",
                "migrations_applied_count": len(applied_in_run),
                "applied_migrations": applied_in_run,
                "total_duration_ms": total_duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def rollback(
        self,
        engine: Optional[Engine] = None,
        target_version: Optional[str] = None,
        steps: int = 1,
    ) -> Dict[str, Any]:
        """Rolls back applied migrations in reverse order."""
        with self._lock:
            db_engine = engine or get_db_engine()
            self._ensure_ledger_table(db_engine)
            applied = self.get_applied_versions(db_engine)

            # Get applied migrations in reverse order
            applied_migrations = [
                m for m in reversed(self.migrations)
                if m.version in applied and applied[m.version]["status"] == "applied"
            ]

            rolled_back = []
            start_total = time.perf_counter()
            count = 0

            for m in applied_migrations:
                if count >= steps and target_version is None:
                    break

                logger.info(f"Rolling back migration '{m.version}': {m.description}...")
                t0 = time.perf_counter()

                # Execute migration down()
                m.down(db_engine)
                duration_ms = round((time.perf_counter() - t0) * 1000, 2)

                # Remove from ledger table
                with db_engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM paypilot_schema_migrations WHERE version = :ver"),
                        {"ver": m.version},
                    )

                rolled_back.append({
                    "version": m.version,
                    "description": m.description,
                    "duration_ms": duration_ms,
                })
                logger.info(f"Successfully rolled back migration '{m.version}' in {duration_ms}ms.")
                count += 1

                if target_version and m.version == target_version:
                    break

            total_duration_ms = round((time.perf_counter() - start_total) * 1000, 2)
            return {
                "status": "success",
                "migrations_rolled_back_count": len(rolled_back),
                "rolled_back_migrations": rolled_back,
                "total_duration_ms": total_duration_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def verify_checksums(self, engine: Optional[Engine] = None) -> Dict[str, Any]:
        """Verifies that all applied database migrations match current source checksums."""
        db_engine = engine or get_db_engine()
        statuses = self.get_status(db_engine)

        drifted = []
        for s in statuses:
            if s["status"] == "applied" and not s["checksum_valid"]:
                drifted.append(s)

        return {
            "verified": len(drifted) == 0,
            "drifted_count": len(drifted),
            "drifted_migrations": drifted,
            "all_statuses": statuses,
        }


# Singleton accessor
_GLOBAL_VERSIONED_MIGRATOR: Optional[VersionedMigrator] = None
_MIGRATOR_LOCK = threading.Lock()


def get_versioned_migrator() -> VersionedMigrator:
    """Singleton accessor for VersionedMigrator."""
    global _GLOBAL_VERSIONED_MIGRATOR
    with _MIGRATOR_LOCK:
        if _GLOBAL_VERSIONED_MIGRATOR is None:
            _GLOBAL_VERSIONED_MIGRATOR = VersionedMigrator()
        return _GLOBAL_VERSIONED_MIGRATOR
