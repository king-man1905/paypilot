"""PayPilot Versioned Database Migration Registry (Phase 23).

Exports all versioned migrations in strict dependency order.
"""

from typing import List

from backend.storage.migrations.base import BaseMigration
from backend.storage.migrations.v001_initial_schema import Migration001InitialSchema
from backend.storage.migrations.v002_analytical_indices import Migration002AnalyticalIndices
from backend.storage.migrations.v003_backup_metadata import Migration003BackupMetadata

# Ordered list of all registered database migrations
ALL_MIGRATIONS: List[BaseMigration] = [
    Migration001InitialSchema(),
    Migration002AnalyticalIndices(),
    Migration003BackupMetadata(),
]

__all__ = [
    "BaseMigration",
    "Migration001InitialSchema",
    "Migration002AnalyticalIndices",
    "Migration003BackupMetadata",
    "ALL_MIGRATIONS",
]
