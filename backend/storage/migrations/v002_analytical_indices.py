"""Migration 002: Composite Analytical Indices for Accelerated Queries (Phase 23)."""

from sqlalchemy import Index, MetaData, Table
from sqlalchemy.engine import Engine

from backend.storage.migrations.base import BaseMigration


class Migration002AnalyticalIndices(BaseMigration):
    """Provisions composite B-tree indices on transaction, audit, and job tables."""

    version = "002_analytical_indices"
    description = "Add composite analytical indices on transactions, audits, and jobs."

    def up(self, engine: Engine) -> None:
        metadata = MetaData()
        metadata.reflect(bind=engine)

        # 1. Transaction composite indices
        if "merchant_transactions" in metadata.tables:
            txn_tbl = metadata.tables["merchant_transactions"]
            indices = [
                Index("idx_txn_status_method", txn_tbl.c.payment_status, txn_tbl.c.payment_method),
                Index("idx_txn_device_status", txn_tbl.c.device_type, txn_tbl.c.payment_status),
                Index("idx_txn_category_refund", txn_tbl.c.product_category, txn_tbl.c.refund_status),
            ]
            for idx in indices:
                idx.create(engine, checkfirst=True)

        # 2. Audit composite indices
        if "paypilot_audit_events" in metadata.tables:
            audit_tbl = metadata.tables["paypilot_audit_events"]
            indices = [
                Index("idx_audit_tenant_type", audit_tbl.c.client_id, audit_tbl.c.event_type),
                Index("idx_audit_created_type", audit_tbl.c.timestamp, audit_tbl.c.event_type),
            ]
            for idx in indices:
                idx.create(engine, checkfirst=True)

        # 3. Job composite indices
        if "paypilot_jobs" in metadata.tables:
            job_tbl = metadata.tables["paypilot_jobs"]
            indices = [
                Index("idx_job_tenant_status", job_tbl.c.client_id, job_tbl.c.status),
                Index("idx_job_status_created", job_tbl.c.status, job_tbl.c.created_at),
                Index("idx_job_trace_id", job_tbl.c.trace_id),
            ]
            for idx in indices:
                idx.create(engine, checkfirst=True)

    def down(self, engine: Engine) -> None:
        metadata = MetaData()
        metadata.reflect(bind=engine)

        idx_names = [
            ("merchant_transactions", "idx_txn_status_method"),
            ("merchant_transactions", "idx_txn_device_status"),
            ("merchant_transactions", "idx_txn_category_refund"),
            ("paypilot_audit_events", "idx_audit_tenant_type"),
            ("paypilot_audit_events", "idx_audit_created_type"),
            ("paypilot_jobs", "idx_job_tenant_status"),
            ("paypilot_jobs", "idx_job_status_created"),
            ("paypilot_jobs", "idx_job_trace_id"),
        ]
        for tbl_name, idx_name in idx_names:
            if tbl_name in metadata.tables:
                tbl = metadata.tables[tbl_name]
                for idx in tbl.indexes:
                    if idx.name == idx_name:
                        idx.drop(engine, checkfirst=True)
