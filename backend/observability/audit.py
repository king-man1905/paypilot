"""Structured Audit Logging & Traceability Engine for PayPilot.

Provides strongly-typed AuditEvent records, thread-safe bounded FIFO in-memory storage,
redaction integration, and administrative audit inspection capabilities.
"""

import abc
import collections
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Dict, List, Optional
import uuid

from backend.config import get_audit_max_events, is_audit_enabled
from backend.utils.redaction import redact_sensitive_text, summarize_query_safely

logger = logging.getLogger("paypilot.audit")


@dataclass
class AuditEvent:
    """Represents an immutable, structured audit event in the PayPilot lifecycle."""
    event_id: str = field(default_factory=lambda: f"aud_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str = "request_completed"  # request_completed, request_failed, auth_failure, rate_limit_exceeded, llm_retry, llm_fallback, circuit_breaker_transition, agent_failure
    request_id: str = "unknown"
    endpoint: str = "/api/v1/analyze"
    http_method: str = "POST"
    client_id: str = "anonymous"
    role: str = "anonymous"  # analyst, admin, anonymous
    intent: Optional[str] = None
    executed_agents: List[str] = field(default_factory=list)
    status: str = "success"  # success, failed, rejected
    status_code: int = 200
    duration_ms: float = 0.0
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    retry_count: int = 0
    fallback_used: bool = False
    error_category: Optional[str] = None
    query_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converts AuditEvent to JSON-serializable dictionary with sanitized values."""
        d = asdict(self)
        if d.get("query_summary"):
            d["query_summary"] = redact_sensitive_text(d["query_summary"])
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEvent":
        """Creates an AuditEvent instance from a dictionary."""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class BaseAuditStore(abc.ABC):
    """Abstract interface for PayPilot audit event persistence stores."""

    @abc.abstractmethod
    def record_event(self, event: AuditEvent) -> None:
        """Appends an AuditEvent to the persistent or in-memory audit trail."""
        pass

    @abc.abstractmethod
    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[AuditEvent]:
        """Retrieves paginated audit events with optional filtering."""
        pass

    @abc.abstractmethod
    def get_event_by_id(self, event_id: str) -> Optional[AuditEvent]:
        """Retrieves a single audit record by its unique event_id."""
        pass

    @abc.abstractmethod
    def count(self) -> int:
        """Returns the total number of audit records currently retained."""
        pass

    @abc.abstractmethod
    def reset(self) -> None:
        """Clears all audit records (for test isolation)."""
        pass

    @property
    @abc.abstractmethod
    def max_events(self) -> int:
        """Returns maximum event retention capacity."""
        pass


class InMemoryAuditStore(BaseAuditStore):
    """Thread-safe bounded FIFO in-memory audit store.

    Maintains a ring buffer of AuditEvents. When the store reaches max_events capacity,
    the oldest audit events are evicted automatically (FIFO eviction) to prevent
    unbounded memory consumption.
    """

    def __init__(self, max_events: Optional[int] = None) -> None:
        self._max_events = max_events if max_events is not None else get_audit_max_events()
        self._lock = threading.Lock()
        self._events: collections.deque = collections.deque(maxlen=self._max_events)

    @property
    def max_events(self) -> int:
        return self._max_events

    def reset(self) -> None:
        """Clears all stored audit events."""
        with self._lock:
            self._events.clear()

    def record_event(self, event: AuditEvent) -> None:
        """Stores a structured audit event safely."""
        with self._lock:
            self._events.append(event)

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[AuditEvent]:
        """Returns a paginated list of audit events in reverse-chronological order (newest first)."""
        with self._lock:
            filtered = list(self._events)

        # Apply filtering
        if event_type:
            norm_type = event_type.strip().lower()
            filtered = [e for e in filtered if e.event_type.lower() == norm_type]

        if request_id:
            norm_req_id = request_id.strip()
            filtered = [e for e in filtered if e.request_id == norm_req_id]

        # Reverse chronological ordering (newest first)
        filtered.reverse()

        # Apply pagination
        safe_offset = max(0, offset)
        safe_limit = max(1, min(limit, 500))
        return filtered[safe_offset : safe_offset + safe_limit]

    def get_event_by_id(self, event_id: str) -> Optional[AuditEvent]:
        """Finds an audit event by its unique event ID."""
        with self._lock:
            for event in self._events:
                if event.event_id == event_id:
                    return event
        return None

    def count(self) -> int:
        """Returns current number of audit events in store."""
        with self._lock:
            return len(self._events)



class SQLAuditStore(BaseAuditStore):
    """Durable relational audit store backed by SQLAlchemy (PostgreSQL / SQLite).

    Persists AuditEvents to the 'paypilot_audit_events' relational table ensuring
    regulatory compliance records survive application restarts and worker crashes.
    """

    def __init__(self) -> None:
        from backend.storage.connection import get_db_engine, get_db_session
        from backend.storage.models import AuditEventModel
        import json


        self._engine = get_db_engine()
        self._session_factory = get_db_session
        self._model = AuditEventModel
        self._json = json
        self._lock = threading.Lock()

        # Ensure schema table exists
        self._model.metadata.create_all(bind=self._engine)

    @property
    def max_events(self) -> int:
        return 100000

    def reset(self) -> None:
        """Clears all audit records in database (for test isolation)."""
        with self._lock:
            session = self._session_factory()
            try:
                session.query(self._model).delete()
                session.commit()
            except Exception as e:
                session.rollback()
                logger.warning(f"Error resetting SQL audit store: {e}")
            finally:
                session.close()

    def record_event(self, event: AuditEvent) -> None:
        """Persists structured audit event to relational storage."""
        with self._lock:
            session = self._session_factory()
            try:
                agents_json = self._json.dumps(event.executed_agents or [])
                model_inst = self._model(
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    event_type=event.event_type,
                    request_id=event.request_id,
                    endpoint=event.endpoint,
                    http_method=event.http_method,
                    client_id=event.client_id,
                    role=event.role,
                    intent=event.intent,
                    executed_agents_json=agents_json,
                    status=event.status,
                    status_code=float(event.status_code),
                    duration_ms=float(event.duration_ms),
                    llm_provider=event.llm_provider,
                    model=event.model,
                    retry_count=float(event.retry_count),
                    fallback_used="true" if event.fallback_used else "false",
                    error_category=event.error_category,
                    query_summary=event.query_summary,
                )
                session.merge(model_inst)
                session.commit()
            except Exception as e:
                session.rollback()
                logger.error(f"Failed to persist audit event {event.event_id}: {e}")
            finally:
                session.close()

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[AuditEvent]:
        """Returns paginated audit events ordered from newest to oldest."""
        with self._lock:
            session = self._session_factory()
            try:
                query = session.query(self._model)
                if event_type:
                    query = query.filter(self._model.event_type == event_type.strip().lower())
                if request_id:
                    query = query.filter(self._model.request_id == request_id.strip())

                query = query.order_by(self._model.timestamp.desc())
                safe_offset = max(0, offset)
                safe_limit = max(1, min(limit, 500))
                rows = query.offset(safe_offset).limit(safe_limit).all()

                results = []
                for r in rows:
                    try:
                        agents = self._json.loads(r.executed_agents_json) if r.executed_agents_json else []
                    except Exception:
                        agents = []

                    results.append(
                        AuditEvent(
                            event_id=r.event_id,
                            timestamp=r.timestamp,
                            event_type=r.event_type,
                            request_id=r.request_id,
                            endpoint=r.endpoint,
                            http_method=r.http_method,
                            client_id=r.client_id,
                            role=r.role,
                            intent=r.intent,
                            executed_agents=agents,
                            status=r.status,
                            status_code=int(r.status_code),
                            duration_ms=float(r.duration_ms),
                            llm_provider=r.llm_provider,
                            model=r.model,
                            retry_count=int(r.retry_count),
                            fallback_used=str(r.fallback_used).lower() == "true",
                            error_category=r.error_category,
                            query_summary=r.query_summary,
                        )
                    )
                return results
            finally:
                session.close()

    def get_event_by_id(self, event_id: str) -> Optional[AuditEvent]:
        """Finds an audit event in relational database by its unique event ID."""
        with self._lock:
            session = self._session_factory()
            try:
                r = session.query(self._model).filter(self._model.event_id == event_id).first()
                if not r:
                    return None

                try:
                    agents = self._json.loads(r.executed_agents_json) if r.executed_agents_json else []
                except Exception:
                    agents = []

                return AuditEvent(
                    event_id=r.event_id,
                    timestamp=r.timestamp,
                    event_type=r.event_type,
                    request_id=r.request_id,
                    endpoint=r.endpoint,
                    http_method=r.http_method,
                    client_id=r.client_id,
                    role=r.role,
                    intent=r.intent,
                    executed_agents=agents,
                    status=r.status,
                    status_code=int(r.status_code),
                    duration_ms=float(r.duration_ms),
                    llm_provider=r.llm_provider,
                    model=r.model,
                    retry_count=int(r.retry_count),
                    fallback_used=str(r.fallback_used).lower() == "true",
                    error_category=r.error_category,
                    query_summary=r.query_summary,
                )
            finally:
                session.close()

    def count(self) -> int:
        """Returns total count of audit events in relational table."""
        with self._lock:
            session = self._session_factory()
            try:
                return session.query(self._model).count()
            finally:
                session.close()


# Global singleton store management
_AUDIT_LOCK = threading.Lock()
_GLOBAL_AUDIT_STORE: Optional[BaseAuditStore] = None


def get_audit_store(
    max_events: Optional[int] = None,
    force_new: bool = False,
) -> BaseAuditStore:
    """Factory accessing or initializing the singleton AuditStore."""
    global _GLOBAL_AUDIT_STORE
    from backend.config import get_audit_store_backend

    with _AUDIT_LOCK:
        if _GLOBAL_AUDIT_STORE is not None and not force_new:
            return _GLOBAL_AUDIT_STORE

        backend_type = get_audit_store_backend()
        if backend_type == "sql":
            _GLOBAL_AUDIT_STORE = SQLAuditStore()
        else:
            _GLOBAL_AUDIT_STORE = InMemoryAuditStore(max_events=max_events)

        return _GLOBAL_AUDIT_STORE


def set_audit_store(store: BaseAuditStore) -> None:
    """Explicitly replaces the active audit store instance (for testing)."""
    global _GLOBAL_AUDIT_STORE
    with _AUDIT_LOCK:
        _GLOBAL_AUDIT_STORE = store


def record_audit_event(
    event_type: str = "request_completed",
    request_id: str = "unknown",
    endpoint: str = "/api/v1/analyze",
    http_method: str = "POST",
    client_id: str = "anonymous",
    role: str = "anonymous",
    intent: Optional[str] = None,
    executed_agents: Optional[List[str]] = None,
    status: str = "success",
    status_code: int = 200,
    duration_ms: float = 0.0,
    llm_provider: Optional[str] = None,
    model: Optional[str] = None,
    retry_count: int = 0,
    fallback_used: bool = False,
    error_category: Optional[str] = None,
    query_summary: Optional[str] = None,
) -> Optional[AuditEvent]:
    """Helper to construct and record an AuditEvent on the active audit store."""
    if not is_audit_enabled():
        return None

    # Sanitize inputs
    sanitized_summary = (
        summarize_query_safely(query_summary) if query_summary is not None else None
    )

    event = AuditEvent(
        event_type=event_type,
        request_id=request_id,
        endpoint=endpoint,
        http_method=http_method,
        client_id=client_id,
        role=role,
        intent=intent,
        executed_agents=list(executed_agents) if executed_agents else [],
        status=status,
        status_code=status_code,
        duration_ms=round(duration_ms, 2),
        llm_provider=llm_provider,
        model=model,
        retry_count=retry_count,
        fallback_used=fallback_used,
        error_category=error_category,
        query_summary=sanitized_summary,
    )

    get_audit_store().record_event(event)
    return event


def reset_audit_store() -> None:
    """Helper to clear all audit records (for testing isolation)."""
    get_audit_store().reset()

