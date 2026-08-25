"""Security and Hardening Middlewares for PayPilot API.

Provides:
1. SecurityHeadersMiddleware: Injects standard HTTP security headers into every response.
2. RateLimitMiddleware: Enforces per-IP volumetric rate limits.
"""

from datetime import datetime, timezone
import hashlib
import logging
from typing import Callable
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import (
    get_analyze_rate_limit_per_minute,
    get_job_rate_limit_per_minute,
)
from backend.observability.audit import record_audit_event
from backend.observability.metrics import record_rate_limit_rejection
from backend.security.rate_limiter import get_rate_limiter, rate_limiter

logger = logging.getLogger("paypilot.security.middleware")

EXEMPT_RATE_LIMIT_PATHS = (
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects standard production API security headers into all responses."""

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)

        # Essential API security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Disable caching for dynamic analysis, telemetry, and administrative endpoints
        if (
            request.url.path.startswith("/api/")
            or request.url.path == "/metrics"
            or request.url.path.startswith("/admin/")
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces tenant-aware and endpoint-specific sliding-window rate limits."""

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        if path in EXEMPT_RATE_LIMIT_PATHS:
            return await call_next(request)

        # 1. Identify Client / Tenant principal
        client_key = "anonymous"
        auth_header = request.headers.get("Authorization")
        api_key_header = request.headers.get("X-API-Key")

        if api_key_header and api_key_header.strip():
            raw_k = api_key_header.strip()
            client_key = f"key:{hashlib.sha256(raw_k.encode('utf-8')).hexdigest()[:12]}"
        elif auth_header and auth_header.strip().lower().startswith("bearer "):
            token_part = auth_header.strip().split()[1] if len(auth_header.strip().split()) > 1 else ""
            client_key = f"bearer:{hashlib.sha256(token_part.encode('utf-8')).hexdigest()[:12]}"
        else:
            # Fall back to client IP for unauthenticated or public traffic
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                client_key = f"ip:{forwarded_for.split(',')[0].strip()}"
            elif request.client and request.client.host:
                client_key = f"ip:{request.client.host}"
            else:
                client_key = "ip:127.0.0.1"

        # 2. Select endpoint-specific rate limit and window
        limiter = get_rate_limiter()
        def_limit = getattr(limiter, "default_limit", 60)
        def_window = getattr(limiter, "default_window", 60)

        if path == "/api/v1/analyze":
            cfg_limit = get_analyze_rate_limit_per_minute()
            limit = min(cfg_limit, def_limit)
            window = def_window
        elif path.startswith("/api/v1/jobs"):
            cfg_limit = get_job_rate_limit_per_minute()
            limit = min(cfg_limit, def_limit)
            window = def_window
        else:
            limit = None
            window = None

        allowed, retry_after = limiter.is_allowed(
            client_id=client_key,
            limit=limit,
            window=window,
        )

        if not allowed:
            req_id = getattr(request.state, "request_id", "unknown")
            record_rate_limit_rejection()
            record_audit_event(
                event_type="rate_limit_exceeded",
                request_id=req_id,
                endpoint=path,
                http_method=request.method,
                client_id=client_key,
                role="anonymous",
                status="rejected",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                error_category="rate_limit_exceeded",
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": 429,
                        "category": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded for endpoint '{path}'. Try again in {retry_after} seconds.",
                        "request_id": req_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

