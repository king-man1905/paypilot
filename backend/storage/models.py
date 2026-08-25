"""SQLAlchemy Declarative Models & Data Structures for PayPilot Transactions.

Defines the relational transaction schema, column data types, constraints,
and targeted B-tree indexes for high-throughput analytical query execution.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    String,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TransactionModel(Base):
    """Relational table model representing merchant payment transactions."""

    __tablename__ = "merchant_transactions"

    transaction_id = Column(String(32), primary_key=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    merchant_id = Column(String(64), nullable=False, index=True)
    customer_id = Column(String(64), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_method = Column(String(32), nullable=False, index=True)
    payment_status = Column(String(32), nullable=False, index=True)
    failure_reason = Column(String(64), nullable=False, default="None")
    device_type = Column(String(32), nullable=False, index=True)
    customer_type = Column(String(32), nullable=False)
    product_category = Column(String(64), nullable=False, index=True)
    refund_status = Column(String(32), nullable=False, default="NO_REFUND")
    checkout_step_reached = Column(String(64), nullable=False, default="PAYMENT_COMPLETED")

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

    event_id = Column(String(32), primary_key=True, nullable=False)
    timestamp = Column(String(64), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    endpoint = Column(String(128), nullable=False)
    http_method = Column(String(16), nullable=False)
    client_id = Column(String(64), nullable=False, index=True)
    role = Column(String(32), nullable=False)
    intent = Column(String(64), nullable=True)
    executed_agents_json = Column(String(512), nullable=False, default="[]")
    status = Column(String(32), nullable=False)
    status_code = Column(Float, nullable=False, default=200)
    duration_ms = Column(Float, nullable=False, default=0.0)
    llm_provider = Column(String(32), nullable=True)
    model = Column(String(64), nullable=True)
    retry_count = Column(Float, nullable=False, default=0)
    fallback_used = Column(String(16), nullable=False, default="false")
    error_category = Column(String(64), nullable=True)
    query_summary = Column(String(256), nullable=True)

    __table_args__ = (
        Index("idx_audit_tenant_type", "client_id", "event_type"),
        Index("idx_audit_created_type", "timestamp", "event_type"),
    )

