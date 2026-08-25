"""PayPilot Data Storage & Persistence Package."""

from backend.storage.backup import (
    BackupMetadata,
    create_database_backup,
    list_backups,
    prune_backups,
    verify_backup_integrity,
)
from backend.storage.connection import (
    check_database_connection,
    create_db_engine,
    dispose_engine,
    get_db_engine,
    get_db_session,
    mask_database_url,
)
from backend.storage.migrator import run_migrations, seed_database_from_csv
from backend.storage.models import AuditEventModel, Base, TransactionModel, TransactionRecord
from backend.storage.versioned_migrator import (
    SchemaMigrationModel,
    VersionedMigrator,
    get_versioned_migrator,
)
from backend.storage.repository import (
    BaseTransactionRepository,
    CSVTransactionRepository,
    SQLTransactionRepository,
    get_transaction_repository,
    reset_transaction_repository,
    set_transaction_repository,
)
from backend.storage.restore import (
    compute_core_financial_metrics,
    restore_database_from_backup,
    validate_restore_integrity,
)
from backend.storage.validator import validate_dataset_integrity

__all__ = [
    "Base",
    "TransactionModel",
    "TransactionRecord",
    "AuditEventModel",
    "SchemaMigrationModel",
    "BaseTransactionRepository",
    "CSVTransactionRepository",
    "SQLTransactionRepository",
    "get_transaction_repository",
    "reset_transaction_repository",
    "set_transaction_repository",
    "create_db_engine",
    "get_db_engine",
    "get_db_session",
    "dispose_engine",
    "check_database_connection",
    "mask_database_url",
    "seed_database_from_csv",
    "run_migrations",
    "VersionedMigrator",
    "get_versioned_migrator",
    "BackupMetadata",
    "create_database_backup",
    "verify_backup_integrity",
    "list_backups",
    "prune_backups",
    "restore_database_from_backup",
    "validate_restore_integrity",
    "compute_core_financial_metrics",
    "validate_dataset_integrity",
]
