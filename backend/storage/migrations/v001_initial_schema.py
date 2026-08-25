"""Migration 001: Initial Core Schema for Transactions, Audits, and Jobs (Phase 23)."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
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


class Migration001InitialSchema(BaseMigration):
    """Provisions the fundamental PayPilot tables for transactions, audits, and background jobs."""

    version = "001_initial_schema"
    description = "Create merchant_transactions, paypilot_audit_events, and paypilot_jobs tables."

    def up(self, engine: Engine) -> None:
        metadata = MetaData()

        # 1. merchant_transactions table
        Table(
            "merchant_transactions",
            metadata,
            Column("transaction_id", String(32), primary_key=True, nullable=False),
            Column("timestamp", DateTime, nullable=False, index=True),
            Column("merchant_id", String(64), nullable=False, index=True),
            Column("customer_id", String(64), nullable=False, index=True),
            Column("amount", Float, nullable=False),
            Column("payment_method", String(32), nullable=False, index=True),
            Column("payment_status", String(32), nullable=False, index=True),
            Column("failure_reason", String(64), nullable=False, default="None"),
            Column("device_type", String(32), nullable=False, index=True),
            Column("customer_type", String(32), nullable=False),
            Column("product_category", String(64), nullable=False, index=True),
            Column("refund_status", String(32), nullable=False, default="NO_REFUND"),
            Column("checkout_step_reached", String(64), nullable=False, default="PAYMENT_COMPLETED"),
        )

        # 2. paypilot_audit_events table
        Table(
            "paypilot_audit_events",
            metadata,
            Column("event_id", String(32), primary_key=True, nullable=False),
            Column("timestamp", String(64), nullable=False, index=True),
            Column("event_type", String(64), nullable=False, index=True),
            Column("request_id", String(64), nullable=False, index=True),
            Column("endpoint", String(128), nullable=False),
            Column("http_method", String(16), nullable=False),
            Column("client_id", String(64), nullable=False, index=True),
            Column("role", String(32), nullable=False),
            Column("intent", String(64), nullable=True),
            Column("executed_agents_json", String(512), nullable=False, default="[]"),
            Column("status", String(32), nullable=False),
            Column("status_code", Float, nullable=False, default=200),
            Column("duration_ms", Float, nullable=False, default=0.0),
            Column("llm_provider", String(32), nullable=True),
            Column("model", String(64), nullable=True),
            Column("retry_count", Float, nullable=False, default=0),
            Column("fallback_used", String(16), nullable=False, default="false"),
            Column("error_category", String(64), nullable=True),
            Column("query_summary", String(256), nullable=True),
        )

        # 3. paypilot_jobs table
        Table(
            "paypilot_jobs",
            metadata,
            Column("job_id", String(32), primary_key=True, nullable=False),
            Column("task_type", String(64), nullable=False, default="async_analysis"),
            Column("client_id", String(64), nullable=False, index=True),
            Column("role", String(32), nullable=False, default="analyst"),
            Column("request_id", String(64), nullable=True, index=True),
            Column("trace_id", String(64), nullable=True, index=True),
            Column("status", String(32), nullable=False, index=True, default="queued"),
            Column("worker_id", String(64), nullable=True, index=True),
            Column("created_at", String(64), nullable=False),
            Column("started_at", String(64), nullable=True),
            Column("completed_at", String(64), nullable=True),
            Column("duration_ms", Float, nullable=True),
            Column("parameters_json", Text, nullable=False, default="{}"),
            Column("result_json", Text, nullable=True),
            Column("error_json", Text, nullable=True),
            Column("retry_count", Integer, nullable=False, default=0),
            Column("fallback_used", Boolean, nullable=False, default=False),
        )

        metadata.create_all(engine)

    def down(self, engine: Engine) -> None:
        metadata = MetaData()
        metadata.reflect(bind=engine)
        for tbl_name in ("merchant_transactions", "paypilot_audit_events", "paypilot_jobs"):
            if tbl_name in metadata.tables:
                metadata.tables[tbl_name].drop(engine, checkfirst=True)
