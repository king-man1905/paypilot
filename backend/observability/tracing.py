"""Distributed Tracing & Span Context Engine for PayPilot.

Provides thread-safe, async-safe trace context propagation, hierarchical span tracking,
bounded in-memory trace storage, and strict sensitive credential redaction.
"""

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import functools
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from backend.config import (
    get_trace_max_events,
    get_trace_max_traces,
    is_tracing_enabled,
)
from backend.utils.redaction import redact_sensitive_text

logger = logging.getLogger("paypilot.observability.tracing")


@dataclass
class TraceContext:
    """Thread-safe and async-safe trace propagation context."""
    trace_id: str = field(default_factory=lambda: f"tr_{uuid.uuid4().hex[:16]}")
    request_id: Optional[str] = None
    span_id: str = field(default_factory=lambda: f"sp_{uuid.uuid4().hex[:12]}")
    parent_span_id: Optional[str] = None

    def create_child(self) -> "TraceContext":
        """Creates a child trace context inheriting trace_id and request_id."""
        return TraceContext(
            trace_id=self.trace_id,
            request_id=self.request_id,
            span_id=f"sp_{uuid.uuid4().hex[:12]}",
            parent_span_id=self.span_id,
        )


# Global ContextVar for active trace context propagation across async tasks & threads
_CURRENT_TRACE_CONTEXT: ContextVar[Optional[TraceContext]] = ContextVar(
    "current_trace_context", default=None
)


def get_current_trace_context() -> Optional[TraceContext]:
    """Retrieves the active TraceContext from the current execution context."""
    return _CURRENT_TRACE_CONTEXT.get()


def set_current_trace_context(context: Optional[TraceContext]) -> Token:
    """Binds a TraceContext to the current execution context."""
    return _CURRENT_TRACE_CONTEXT.set(context)


def reset_trace_context(token: Token) -> None:
    """Resets the TraceContext back to its previous state using the token."""
    try:
        _CURRENT_TRACE_CONTEXT.reset(token)
    except Exception as e:
        logger.debug(f"Notice resetting trace context token: {e}")


@dataclass
class SpanRecord:
    """Immutable record capturing execution metrics and lifecycle of a single span."""
    trace_id: str
    span_id: str
    operation_name: str
    component: str
    parent_span_id: Optional[str] = None
    request_id: Optional[str] = None
    start_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    status: str = "OK"  # OK, ERROR
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes span into clean dictionary with redacted sensitive values."""
        data = asdict(self)
        # Deep scrub metadata
        if data.get("metadata"):
            scrubbed_meta = {}
            for k, v in data["metadata"].items():
                if isinstance(v, str):
                    scrubbed_meta[k] = redact_sensitive_text(v)
                elif isinstance(v, dict):
                    scrubbed_meta[k] = {
                        sub_k: redact_sensitive_text(str(sub_v)) if isinstance(sub_v, str) else sub_v
                        for sub_k, sub_v in v.items()
                    }
                else:
                    scrubbed_meta[k] = v
            data["metadata"] = scrubbed_meta
        if data.get("error_message"):
            data["error_message"] = redact_sensitive_text(str(data["error_message"]))
        return data


class BaseTraceStore:
    """Abstract interface defining storage and retrieval of distributed traces."""

    def record_span(self, span: SpanRecord) -> None:
        raise NotImplementedError

    def get_trace(self, trace_id: str) -> Optional[List[SpanRecord]]:
        raise NotImplementedError

    def list_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_total_spans_count(self) -> int:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


from collections import OrderedDict


class InMemoryTraceStore(BaseTraceStore):
    """Thread-safe in-memory circular trace store with bounded FIFO retention."""

    def __init__(
        self,
        max_events: Optional[int] = None,
        max_traces: Optional[int] = None,
    ) -> None:
        self._max_events = max_events if max_events is not None else get_trace_max_events()
        self._max_traces = max_traces if max_traces is not None else get_trace_max_traces()
        self._lock = threading.Lock()
        # trace_id -> List[SpanRecord] in FIFO insertion order
        self._traces: OrderedDict[str, List[SpanRecord]] = OrderedDict()
        self._total_spans = 0

    def record_span(self, span: SpanRecord) -> None:
        """Appends span to trace store, evicting oldest traces if limits are exceeded."""
        with self._lock:
            trace_id = span.trace_id
            if trace_id not in self._traces:
                # Evict oldest trace if max_traces exceeded
                if len(self._traces) >= self._max_traces:
                    oldest_id, evicted_spans = self._traces.popitem(last=False)
                    self._total_spans -= len(evicted_spans)

                self._traces[trace_id] = []

            self._traces[trace_id].append(span)
            self._total_spans += 1

            # Evict oldest traces or spans if max_events exceeded
            while self._total_spans > self._max_events and self._traces:
                first_trace_id = next(iter(self._traces))
                trace_spans = self._traces[first_trace_id]
                if len(trace_spans) > 1:
                    trace_spans.pop(0)
                    self._total_spans -= 1
                else:
                    self._traces.popitem(last=False)
                    self._total_spans -= 1

    def get_trace(self, trace_id: str) -> Optional[List[SpanRecord]]:
        """Retrieves all recorded spans belonging to a given trace_id."""
        with self._lock:
            spans = self._traces.get(trace_id)
            if spans is None:
                return None
            return list(spans)

    def list_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Summarizes recent traces with root span, span count, and duration."""
        with self._lock:
            summaries = []
            # Iterate backwards through most recent traces up to limit
            keys = list(self._traces.keys())[-limit:]
            for trace_id in reversed(keys):
                spans = self._traces.get(trace_id, [])
                if not spans:
                    continue
                root_span = spans[0]
                last_span = spans[-1]
                total_duration = 0.0
                has_errors = any(s.status == "ERROR" for s in spans)
                if root_span.duration_ms is not None:
                    total_duration = root_span.duration_ms
                elif len(spans) > 1 and last_span.duration_ms is not None:
                    total_duration = sum(s.duration_ms or 0.0 for s in spans)

                summaries.append({
                    "trace_id": trace_id,
                    "request_id": root_span.request_id,
                    "operation_name": root_span.operation_name,
                    "component": root_span.component,
                    "start_time": root_span.start_time,
                    "span_count": len(spans),
                    "total_duration_ms": round(total_duration, 2),
                    "status": "ERROR" if has_errors else "OK",
                })
            return summaries

    def get_total_spans_count(self) -> int:
        """Returns total active spans across all traces."""
        with self._lock:
            return self._total_spans

    def clear(self) -> None:
        """Clears all stored traces."""
        with self._lock:
            self._traces.clear()
            self._total_spans = 0


# Global singleton trace store initialized at import time
_GLOBAL_TRACE_STORE: BaseTraceStore = InMemoryTraceStore()


def get_trace_store() -> BaseTraceStore:
    """Retrieves the global trace store singleton."""
    return _GLOBAL_TRACE_STORE


def set_trace_store(store: BaseTraceStore) -> None:
    """Explicitly sets the active trace store."""
    global _GLOBAL_TRACE_STORE
    _GLOBAL_TRACE_STORE = store


def reset_trace_store() -> None:
    """Resets the active trace store."""
    global _GLOBAL_TRACE_STORE
    if _GLOBAL_TRACE_STORE is not None:
        _GLOBAL_TRACE_STORE.clear()
    else:
        _GLOBAL_TRACE_STORE = InMemoryTraceStore()


class trace_span:
    """Context manager & decorator for measuring and recording execution spans."""

    def __init__(
        self,
        operation_name: str,
        component: str = "app",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.operation_name = operation_name
        self.component = component
        self.metadata = metadata or {}
        self.span: Optional[SpanRecord] = None
        self._token: Optional[Token] = None
        self._t0: float = 0.0

    def __enter__(self) -> SpanRecord:
        if not is_tracing_enabled():
            # Return dummy span if tracing is disabled
            self.span = SpanRecord(
                trace_id="tr_disabled",
                span_id="sp_disabled",
                operation_name=self.operation_name,
                component=self.component,
            )
            return self.span

        parent_ctx = get_current_trace_context()
        if parent_ctx is None:
            ctx = TraceContext()
        else:
            ctx = parent_ctx.create_child()

        self._token = set_current_trace_context(ctx)
        self._t0 = time.perf_counter()

        self.span = SpanRecord(
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            parent_span_id=ctx.parent_span_id,
            request_id=ctx.request_id,
            operation_name=self.operation_name,
            component=self.component,
            start_time=datetime.now(timezone.utc).isoformat(),
            metadata=dict(self.metadata),
        )
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if not is_tracing_enabled() or self.span is None:
            if self._token is not None:
                reset_trace_context(self._token)
            return False

        duration_ms = (time.perf_counter() - self._t0) * 1000.0
        self.span.duration_ms = round(duration_ms, 3)
        self.span.end_time = datetime.now(timezone.utc).isoformat()

        if exc_val is not None:
            self.span.status = "ERROR"
            self.span.error_category = exc_type.__name__
            self.span.error_message = redact_sensitive_text(str(exc_val))

        try:
            store = get_trace_store()
            store.record_span(self.span)
        except Exception as e:
            logger.warning(f"Error persisting span '{self.operation_name}': {e}")

        if self._token is not None:
            reset_trace_context(self._token)

        # Do not suppress exception
        return False

    def __call__(self, func: Callable) -> Callable:
        """Allows trace_span to be used as a function decorator."""
        op_name = self.operation_name
        comp = self.component
        meta = self.metadata

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with trace_span(op_name, component=comp, metadata=meta):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with trace_span(op_name, component=comp, metadata=meta):
                return await func(*args, **kwargs)

        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return wrapper

