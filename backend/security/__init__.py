"""PayPilot Security, Authentication, Rate Limiting, Quotas & Idempotency Package."""

from backend.security.auth import (
    AuthenticatedUser,
    get_current_user,
    require_admin,
    require_analyst,
)
from backend.security.idempotency import (
    BaseIdempotencyStore,
    IdempotencyRecord,
    IdempotencyReservationStatus,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    compute_payload_hash,
    get_idempotency_store,
    reset_idempotency_store,
    set_idempotency_store,
    validate_idempotency_key,
)
from backend.security.middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from backend.security.quotas import (
    BaseQuotaManager,
    InMemoryQuotaManager,
    RedisQuotaManager,
    get_quota_manager,
    reset_quota_manager,
    set_quota_manager,
)
from backend.security.rate_limiter import (
    BaseRateLimiter,
    InMemoryRateLimiter,
    RedisRateLimiter,
    get_rate_limiter,
    rate_limiter,
    reset_rate_limiter,
    set_rate_limiter,
)

__all__ = [
    "AuthenticatedUser",
    "get_current_user",
    "require_analyst",
    "require_admin",
    "SecurityHeadersMiddleware",
    "RateLimitMiddleware",
    "BaseRateLimiter",
    "InMemoryRateLimiter",
    "RedisRateLimiter",
    "get_rate_limiter",
    "set_rate_limiter",
    "reset_rate_limiter",
    "rate_limiter",
    "BaseQuotaManager",
    "InMemoryQuotaManager",
    "RedisQuotaManager",
    "get_quota_manager",
    "set_quota_manager",
    "reset_quota_manager",
    "IdempotencyReservationStatus",
    "IdempotencyRecord",
    "BaseIdempotencyStore",
    "InMemoryIdempotencyStore",
    "RedisIdempotencyStore",
    "get_idempotency_store",
    "set_idempotency_store",
    "reset_idempotency_store",
    "validate_idempotency_key",
    "compute_payload_hash",
]
