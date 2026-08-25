"""Background Job Models, Lifecycle States, and Relational Schema for PayPilot.

Defines the JobRecord schema, status enumeration, dataclass, and SQLAlchemy
JobModel declarative entity for shared multi-worker persistence.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)

from backend.storage.models import Base


class JobStatus(str, Enum):
    """Lifecycle status enumeration for background jobs."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobRecord:
    """Immutable metadata and state record for a background job."""
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    task_type: str = "async_analysis"
    client_id: str = "anonymous"
    role: str = "analyst"
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    status: str = JobStatus.QUEUED.value
    worker_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    retry_count: int = 0
    fallback_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serializes job record into a clean dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobRecord":
        """Instantiates a JobRecord from a dictionary."""
        return cls(
            job_id=str(data.get("job_id", f"job_{uuid.uuid4().hex[:12]}")),
            task_type=str(data.get("task_type", "async_analysis")),
            client_id=str(data.get("client_id", "anonymous")),
            role=str(data.get("role", "analyst")),
            request_id=data.get("request_id"),
            trace_id=data.get("trace_id"),
            status=str(data.get("status", JobStatus.QUEUED.value)),
            worker_id=data.get("worker_id"),
            created_at=str(data.get("created_at", datetime.now(timezone.utc).isoformat())),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            duration_ms=float(data["duration_ms"]) if data.get("duration_ms") is not None else None,
            parameters=dict(data.get("parameters", {})),
            result=dict(data["result"]) if data.get("result") is not None else None,
            error=dict(data["error"]) if data.get("error") is not None else None,
            retry_count=int(data.get("retry_count", 0)),
            fallback_used=bool(data.get("fallback_used", False)),
        )


from backend.utils.redaction import redact_sensitive_dict


class JobModel(Base):
    """Relational table model representing shared background jobs."""

    __tablename__ = "paypilot_jobs"

    job_id = Column(String(32), primary_key=True, nullable=False)
    task_type = Column(String(64), nullable=False, default="async_analysis")
    client_id = Column(String(64), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="analyst")
    request_id = Column(String(64), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, index=True, default=JobStatus.QUEUED.value)
    worker_id = Column(String(64), nullable=True, index=True)
    created_at = Column(String(64), nullable=False)
    started_at = Column(String(64), nullable=True)
    completed_at = Column(String(64), nullable=True)
    duration_ms = Column(Float, nullable=True)
    parameters_json = Column(Text, nullable=False, default="{}")
    result_json = Column(Text, nullable=True)
    error_json = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    fallback_used = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_job_tenant_status", "client_id", "status"),
        Index("idx_job_status_created", "status", "created_at"),
        Index("idx_job_trace_id", "trace_id"),
    )

    def to_record(self) -> JobRecord:
        """Converts SQLAlchemy JobModel into a clean JobRecord dataclass."""
        params = json.loads(self.parameters_json) if self.parameters_json else {}
        res = json.loads(self.result_json) if self.result_json else None
        err = json.loads(self.error_json) if self.error_json else None

        return JobRecord(
            job_id=self.job_id,
            task_type=self.task_type,
            client_id=self.client_id,
            role=self.role,
            request_id=self.request_id,
            trace_id=self.trace_id,
            status=self.status,
            worker_id=self.worker_id,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_ms=self.duration_ms,
            parameters=params,
            result=res,
            error=err,
            retry_count=self.retry_count,
            fallback_used=self.fallback_used,
        )

    @classmethod
    def from_record(cls, record: JobRecord) -> "JobModel":
        """Instantiates a JobModel entity from a JobRecord with redaction applied."""
        sanitized_params = redact_sensitive_dict(record.parameters or {})
        sanitized_result = redact_sensitive_dict(record.result) if record.result is not None else None
        sanitized_error = redact_sensitive_dict(record.error) if record.error is not None else None

        return cls(
            job_id=record.job_id,
            task_type=record.task_type,
            client_id=record.client_id,
            role=record.role,
            request_id=record.request_id,
            trace_id=record.trace_id,
            status=record.status,
            worker_id=record.worker_id,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            duration_ms=record.duration_ms,
            parameters_json=json.dumps(sanitized_params),
            result_json=json.dumps(sanitized_result) if sanitized_result is not None else None,
            error_json=json.dumps(sanitized_error) if sanitized_error is not None else None,
            retry_count=record.retry_count,
            fallback_used=record.fallback_used,
        )


