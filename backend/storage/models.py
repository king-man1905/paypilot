"""SQLAlchemy Declarative Models & Data Structures for PayPilot Transactions.

Defines the relational transaction schema, column data types, constraints,
and targeted B-tree indexes for high-throughput analytical query execution.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import (
    DateTime,
    Float,
    Index,
    String,
)
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

Base = declarative_base()


class TransactionModel(Base):
    """Relational table model representing merchant payment transactions."""

    __tablename__ = "merchant_transactions"

    transaction_id: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="None")
    device_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    customer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    product_category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    refund_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NO_REFUND")
    checkout_step_reached: Mapped[str] = mapped_column(String(64), nullable=False, default="PAYMENT_COMPLETED")

    # Composite B-tree indexes for accelerated multidimensional queries
    __table_args__ = (
        Index("idx_txn_status_method", "payment_status", "payment_method"),
        Index("idx_txn_device_status", "device_type", "payment_status"),
        Index("idx_txn_category_refund", "product_category", "refund_status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes SQLAlchemy model instance into a dictionary."""
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S") if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "merchant_id": self.merchant_id,
            "customer_id": self.customer_id,
            "amount": float(self.amount),
            "payment_method": self.payment_method,
            "payment_status": self.payment_status,
            "failure_reason": self.failure_reason,
            "device_type": self.device_type,
            "customer_type": self.customer_type,
            "product_category": self.product_category,
            "refund_status": self.refund_status,
            "checkout_step_reached": self.checkout_step_reached,
        }


@dataclass
class TransactionRecord:
    """Lightweight Python dataclass representation of a transaction record."""
    transaction_id: str
    timestamp: str
    merchant_id: str
    customer_id: str
    amount: float
    payment_method: str
    payment_status: str
    failure_reason: str
    device_type: str
    customer_type: str
    product_category: str
    refund_status: str
    checkout_step_reached: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransactionRecord":
        return cls(
            transaction_id=str(data["transaction_id"]),
            timestamp=str(data["timestamp"]),
            merchant_id=str(data["merchant_id"]),
            customer_id=str(data["customer_id"]),
            amount=float(data["amount"]),
            payment_method=str(data["payment_method"]),
            payment_status=str(data["payment_status"]),
            failure_reason=str(data.get("failure_reason", "None")),
            device_type=str(data["device_type"]),
            customer_type=str(data["customer_type"]),
            product_category=str(data["product_category"]),
            refund_status=str(data.get("refund_status", "NO_REFUND")),
            checkout_step_reached=str(data.get("checkout_step_reached", "PAYMENT_COMPLETED")),
        )


class AuditEventModel(Base):
    """Relational table model representing durable compliance audit records."""

    __tablename__ = "paypilot_audit_events"

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    http_method: Mapped[str] = mapped_column(String(16), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    executed_agents_json: Mapped[str] = mapped_column(String(512), nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_code: Mapped[float] = mapped_column(Float, nullable=False, default=200.0)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fallback_used: Mapped[str] = mapped_column(String(16), nullable=False, default="false")
    error_category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    query_summary: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        Index("idx_audit_tenant_type", "client_id", "event_type"),
        Index("idx_audit_created_type", "timestamp", "event_type"),
    )

