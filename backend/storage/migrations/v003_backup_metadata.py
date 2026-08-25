"""Migration 003: Backup Metadata Table for Disaster Recovery (Phase 23)."""

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.engine import Engine

from backend.storage.migrations.base import BaseMigration


class Migration003BackupMetadata(BaseMigration):
    """Provisions the table for tracking point-in-time disaster recovery manifests."""

    version = "003_backup_metadata"
    description = "Create paypilot_backup_metadata table for disaster recovery manifests."

    def up(self, engine: Engine) -> None:
        metadata = MetaData()

        Table(
            "paypilot_backup_metadata",
            metadata,
            Column("backup_id", String(64), primary_key=True, nullable=False),
            Column("timestamp", String(64), nullable=False, index=True),
            Column("backend", String(32), nullable=False),
            Column("record_count", Integer, nullable=False),
            Column("total_amount", Float, nullable=False),
            Column("sha256_checksum", String(64), nullable=False),
            Column("file_path", String(256), nullable=False),
            Column("verified", Boolean, nullable=False, default=True),
            Column("schema_version", Integer, nullable=False, default=1),
            Column("extra_metadata_json", Text, nullable=True),
        )

        metadata.create_all(engine)

    def down(self, engine: Engine) -> None:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        if "paypilot_backup_metadata" in metadata.tables:
            metadata.tables["paypilot_backup_metadata"].drop(engine, checkfirst=True)
