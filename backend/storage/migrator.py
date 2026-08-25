"""Database Seeding and Migration Engine for PayPilot.

Provides deterministic migration of the synthetic merchant transaction dataset
from CSV to relational database backends (SQLite / PostgreSQL) with validation
and duplicate prevention.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import DATA_PATH
from backend.storage.connection import get_db_engine
from backend.storage.models import Base, TransactionModel

logger = logging.getLogger("paypilot.storage.migrator")


def seed_database_from_csv(
    csv_path: Optional[Union[str, Path]] = None,
    engine=None,
    database_url: Optional[str] = None,
    overwrite: bool = False,
    batch_size: int = 2000,
) -> Dict[str, Any]:
    """Deterministically loads merchant transactions from CSV into the target database.

    Args:
        csv_path: Path to the source CSV file (defaults to DATA_PATH).
        engine: SQLAlchemy Engine instance (defaults to global db engine).
        database_url: Optional database connection URL string.
        overwrite: If True, clears existing rows before seeding.
        batch_size: Chunk size for bulk inserts.

    Returns:
        Dict[str, Any]: Seeding summary with row count and verification status.

    Raises:
        FileNotFoundError: If source CSV does not exist.
        ValueError: If CSV is empty or row count mismatch occurs.
    """
    path = Path(csv_path) if csv_path else Path(DATA_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Source CSV dataset not found at: {path}")

    if database_url:
        from backend.storage.connection import create_db_engine
        db_engine = create_db_engine(db_url=database_url)
    else:
        db_engine = engine or get_db_engine()


    # 1. Ensure table schemas exist
    Base.metadata.create_all(db_engine)

    # 2. Check current row count
    with db_engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM merchant_transactions"))
        existing_count = int(res.scalar() or 0)

    # 3. Read and validate CSV
    df = pd.read_csv(path)
    csv_count = len(df)

    if df.empty:
        raise ValueError(f"Source CSV dataset at {path} is empty.")

    # 4. Skip insertion if already seeded and overwrite not requested
    if existing_count > 0 and not overwrite:
        logger.info(
            f"Database already contains {existing_count} records. "
            f"Skipping seeding (overwrite=False)."
        )
        return {
            "status": "already_seeded",
            "rows_migrated": existing_count,
            "csv_rows": csv_count,
            "verified": existing_count == csv_count,
            "csv_source": str(path),
        }

    # 5. Clear table if overwrite is requested
    if existing_count > 0 and overwrite:
        logger.info(f"Clearing existing {existing_count} records from merchant_transactions...")
        with db_engine.begin() as conn:
            conn.execute(text("DELETE FROM merchant_transactions"))

    # 6. Clean and convert data types
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["failure_reason"] = df["failure_reason"].fillna("None").astype(str)
    df["refund_status"] = df["refund_status"].fillna("NO_REFUND").astype(str)
    df["checkout_step_reached"] = df["checkout_step_reached"].fillna("PAYMENT_COMPLETED").astype(str)

    # 7. Bulk insert in batches
    logger.info(f"Seeding {csv_count} transactions into merchant_transactions in chunks of {batch_size}...")
    df.to_sql(
        name="merchant_transactions",
        con=db_engine,
        if_exists="append",
        index=False,
        chunksize=batch_size,
    )

    # 8. Verify final row count
    with db_engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM merchant_transactions"))
        final_count = int(res.scalar() or 0)

    is_verified = final_count == csv_count
    if not is_verified:
        raise ValueError(
            f"Migration row count mismatch: inserted {final_count}, expected {csv_count}."
        )

    logger.info(f"Successfully migrated and verified {final_count} transaction records.")

    return {
        "status": "success",
        "rows_migrated": final_count,
        "csv_rows": csv_count,
        "verified": is_verified,
        "csv_source": str(path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_migrations(
    engine=None,
    database_url: Optional[str] = None,
    auto_seed: bool = True,
    csv_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Executes safe, idempotent database migrations and schema setup with version ledger tracking.

    Ensures all SQL models (TransactionModel, JobModel, AuditEventModel, BackupMetadataModel)
    have their tables created with appropriate primary keys, indices, and constraints.
    Applies versioned migrations to paypilot_schema_migrations ledger.
    Optionally seeds transaction data from CSV if table is empty.
    """
    if database_url:
        from backend.storage.connection import create_db_engine
        db_engine = create_db_engine(db_url=database_url)
    else:
        db_engine = engine or get_db_engine()

    # 1. Apply all versioned migrations (creates schema, indices, backup metadata & ledger)
    from backend.storage.versioned_migrator import get_versioned_migrator
    migrator = get_versioned_migrator()
    migrator_res = migrator.apply_migrations(engine=db_engine)

    # 2. Also ensure Base.metadata for any dynamic or ad-hoc models
    Base.metadata.create_all(db_engine)

    # 3. Inspect table schemas
    with db_engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM merchant_transactions"))
        tx_count = int(res.scalar() or 0)

    seed_result = None
    if auto_seed and tx_count == 0:
        seed_result = seed_database_from_csv(
            csv_path=csv_path,
            engine=db_engine,
            overwrite=False,
        )

    return {
        "status": "success",
        "schemas_created": list(Base.metadata.tables.keys()),
        "transactions_count": seed_result["rows_migrated"] if seed_result else tx_count,
        "seeded": bool(seed_result and seed_result.get("status") == "success"),
        "versioned_migrations_applied": migrator_res["migrations_applied_count"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        res = run_migrations()
        print(f"Database migration completed: {res}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Database migration failed: {e}")
        sys.exit(1)

