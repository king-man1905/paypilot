"""FastAPI Application Entry Point for PayPilot.

Configures middleware, structured logging, exception handlers, and mounts API routes.
"""

import asyncio
import contextlib
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional
import uuid
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routes import router
from backend.api.schemas import ErrorResponse
from backend.config import (
    FASTAPI_HOST,
    FASTAPI_PORT,
    MAX_CONCURRENT_REQUESTS,
    get_shutdown_timeout_seconds,
    validate_startup_config,
)
from backend.security import RateLimitMiddleware, SecurityHeadersMiddleware

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("paypilot.app")

# Concurrency Guard helper ensuring loop-safe semaphore creation
_fallback_semaphore: Optional[asyncio.Semaphore] = None

# Global Application Shutdown State Management
_SHUTDOWN_STATE = {
    "is_shutting_down": False,
    "shutdown_started_at": 0.0,
}
_SHUTDOWN_LOCK = threading.Lock()


def is_shutting_down() -> bool:
    """Returns whether the application is currently in shutdown/draining mode."""
    with _SHUTDOWN_LOCK:
        return _SHUTDOWN_STATE["is_shutting_down"]


def set_shutting_down(value: bool = True) -> None:
    """Sets the application shutdown state."""
    global _SHUTDOWN_STATE
    with _SHUTDOWN_LOCK:
        _SHUTDOWN_STATE["is_shutting_down"] = value
        if value:
            _SHUTDOWN_STATE["shutdown_started_at"] = time.time()


async def execute_graceful_shutdown(timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Executes an orderly drain and resource teardown sequence upon SIGTERM/SIGINT."""
    set_shutting_down(True)
    timeout = timeout_seconds if timeout_seconds is not None else get_shutdown_timeout_seconds()
    logger.info(f"Initiating PayPilot graceful shutdown sequence (timeout={timeout}s)...")

    start_t = time.perf_counter()
    summary: Dict[str, Any] = {
        "is_shutting_down": True,
        "job_drain": None,
        "db_disposed": False,
        "duration_ms": 0.0,
    }

    # 1. Drain background job runner
    try:
        from backend.jobs import get_job_runner
        runner = get_job_runner()
        summary["job_drain"] = runner.drain(timeout_seconds=timeout)
    except Exception as e:
        logger.warning(f"Error draining job runner during shutdown: {e}")

    # 2. Dispose database engine if active
    try:
        from backend.storage.connection import get_db_engine
        engine = get_db_engine()
        if engine is not None:
            engine.dispose()
            summary["db_disposed"] = True
    except Exception as e:
        logger.warning(f"Error disposing database engine during shutdown: {e}")

    dur = round((time.perf_counter() - start_t) * 1000, 2)
    summary["duration_ms"] = dur
    logger.info(f"PayPilot graceful shutdown sequence completed in {dur}ms.")
    return summary


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifespan managing startup configuration validation and graceful shutdown."""
    validate_startup_config()
    set_shutting_down(False)
    yield
    await execute_graceful_shutdown()


def get_concurrency_semaphore() -> asyncio.Semaphore:
    """Retrieves or creates an asyncio.Semaphore attached to the active event loop."""
    global _fallback_semaphore
    try:
        loop = asyncio.get_running_loop()
        sem = getattr(loop, "_paypilot_concurrency_semaphore", None)
        if sem is None:
            sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
            setattr(loop, "_paypilot_concurrency_semaphore", sem)
        return sem
    except RuntimeError:
        if _fallback_semaphore is None:
            _fallback_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        return _fallback_semaphore


tags_metadata = [
    {
        "name": "System",
        "description": "Liveness and readiness probes for operational health monitoring.",
    },
    {
        "name": "Analysis",
        "description": "Agentic diagnostic analysis and revenue recovery action prioritization.",
    },
]

# Initialize FastAPI App with Lifespan
app = FastAPI(
    title="PayPilot Revenue Recovery & Action Engine API",
    version="1.0.0",
    description=(
        "Production API for PayPilot — an Agentic Revenue Recovery & Growth System. "
        "Transforms merchant payment metrics and diagnostic evidence into ranked, "
        "measurable action plans using LangGraph multi-agent orchestration and NVIDIA LLMs."
    ),
    openapi_tags=tags_metadata,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# 1. Security Headers & Rate Limiting Middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

# 2. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from backend.observability.metrics import record_request, record_error
from backend.observability.audit import record_audit_event
from backend.observability.tracing import (
    TraceContext,
    get_trace_store,
    reset_trace_context,
    set_current_trace_context,
    trace_span,
)


def _sanitize_trace_id(header_val: Optional[str]) -> str:
    """Sanitizes incoming trace ID ensuring alphanumeric/hyphen/underscore bounds."""
    if not header_val or not isinstance(header_val, str):
        return f"tr_{uuid.uuid4().hex[:16]}"
    clean = "".join(c for c in header_val.strip() if c.isalnum() or c in ("-", "_"))[:64]
    return clean if clean else f"tr_{uuid.uuid4().hex[:16]}"


# 3. Request Tracking & Observability Middleware
@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    """Assigns unique request ID, trace context, limits concurrency, and logs telemetry."""
    raw_trace_id = request.headers.get("X-Trace-ID") or request.headers.get("traceparent")
    trace_id = _sanitize_trace_id(raw_trace_id)
    raw_req_id = request.headers.get("X-Request-ID")
    request_id = _sanitize_trace_id(raw_req_id) if raw_req_id else str(uuid.uuid4())

    request.state.trace_id = trace_id
    request.state.request_id = request_id

    root_ctx = TraceContext(
        trace_id=trace_id,
        request_id=request_id,
        span_id=f"sp_{uuid.uuid4().hex[:12]}",
        parent_span_id=None,
    )
    token = set_current_trace_context(root_ctx)

    start_time = time.perf_counter()
    logger.info(f"--> [{request_id}][{trace_id}] {request.method} {request.url.path}")

    response: Optional[Response] = None
    try:
        with trace_span("http.request", component="http", metadata={"method": request.method, "endpoint": request.url.path}):
            semaphore = get_concurrency_semaphore()
            async with semaphore:
                response = await call_next(request)

        if response is None:
            response = Response(content=b'{"detail":"Internal Server Error"}', status_code=500, media_type="application/json")

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        intent = getattr(request.state, "intent", None)
        record_request(
            endpoint=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            intent=intent,
        )

        # Emit structured audit event for the completed request lifecycle
        record_audit_event(
            event_type="request_completed" if response.status_code < 400 else "request_failed",
            request_id=request_id,
            endpoint=request.url.path,
            http_method=request.method,
            client_id=getattr(request.state, "client_id", "anonymous"),
            role=getattr(request.state, "role", "anonymous"),
            intent=intent,
            executed_agents=getattr(request.state, "executed_agents", []),
            status="success" if response.status_code < 400 else "failed",
            status_code=response.status_code,
            duration_ms=duration_ms,
            llm_provider=getattr(request.state, "llm_provider", None),
            model=getattr(request.state, "model", None),
            retry_count=getattr(request.state, "retry_count", 0),
            fallback_used=getattr(request.state, "fallback_used", False),
            error_category=getattr(request.state, "error_category", None),
            query_summary=getattr(request.state, "query_summary", None),
        )

        logger.info(f"<-- [{request_id}][{trace_id}] {response.status_code} in {duration_ms}ms")
        return response
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        record_request(
            endpoint=request.url.path,
            status_code=500,
            duration_ms=duration_ms,
            intent=getattr(request.state, "intent", None),
        )
        record_error("internal_error")
        record_audit_event(
            event_type="request_failed",
            request_id=request_id,
            endpoint=request.url.path,
            http_method=request.method,
            client_id=getattr(request.state, "client_id", "anonymous"),
            role=getattr(request.state, "role", "anonymous"),
            intent=getattr(request.state, "intent", None),
            executed_agents=getattr(request.state, "executed_agents", []),
            status="failed",
            status_code=500,
            duration_ms=duration_ms,
            error_category="unhandled_exception",
        )
        logger.error(f"<-- [{request_id}][{trace_id}] Unhandled error after {duration_ms}ms: {type(exc).__name__}")
        raise exc
    finally:
        reset_trace_context(token)



# 4. Custom Exception Handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Standardizes HTTP exceptions into clean ErrorResponse schema."""
    request_id = getattr(request.state, "request_id", None)
    if exc.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY):
        err_cat = "validation_error"
    elif exc.status_code == status.HTTP_401_UNAUTHORIZED:
        err_cat = "auth_error"
    elif exc.status_code == status.HTTP_403_FORBIDDEN:
        err_cat = "forbidden_error"
    elif exc.status_code == status.HTTP_504_GATEWAY_TIMEOUT:
        err_cat = "timeout"
    elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        err_cat = "provider_error"
    else:
        err_cat = "internal_error"

    record_error(err_cat)
    request.state.error_category = err_cat

    err = ErrorResponse(
        error=f"HTTP_{exc.status_code}",
        detail=exc.detail,
        request_id=request_id,
        status_code=exc.status_code,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=err.model_dump(),
        headers=getattr(exc, "headers", None),
    )



@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handles Pydantic validation errors safely."""
    request_id = getattr(request.state, "request_id", None)
    record_error("validation_error")
    request.state.error_category = "validation_error"
    errors_summary = "; ".join([f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in exc.errors()])
    err = ErrorResponse(
        error="VALIDATION_ERROR",
        detail=f"Invalid request parameters: {errors_summary}",
        request_id=request_id,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=err.model_dump(),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global catch-all preventing internal secret or stack trace leakage."""
    request_id = getattr(request.state, "request_id", None)
    record_error("internal_error")
    request.state.error_category = "internal_error"
    logger.error(f"[{request_id}] Internal server exception: {type(exc).__name__}")
    err = ErrorResponse(
        error="INTERNAL_SERVER_ERROR",
        detail="An internal server error occurred during processing. Please try again later.",
        request_id=request_id,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=err.model_dump(),
    )



# 4. Mount Routes
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.api.main:app",
        host=FASTAPI_HOST,
        port=FASTAPI_PORT,
        reload=False,
    )
