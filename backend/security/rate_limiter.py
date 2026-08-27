"""Sliding-Window Rate Limiter for PayPilot.

Provides:
1. BaseRateLimiter: Abstract interface for rate-limiting strategies.
2. InMemoryRateLimiter: Process-local sliding window using collections.deque.
3. RedisRateLimiter: Distributed sliding window using Redis ZSET with graceful fallback.
4. Factory management dynamically selecting backend based on RATE_LIMIT_BACKEND.
"""

import abc
import collections
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from backend.config import (
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    REDIS_URL,
)

logger = logging.getLogger("paypilot.security.ratelimit")


class BaseRateLimiter(abc.ABC):
    """Abstract interface for PayPilot rate limiters."""

    @abc.abstractmethod
    def is_allowed(
        self,
        client_id: str,
        limit: Optional[int] = None,
        window: Optional[int] = None,
    ) -> Tuple[bool, int]:
        """Evaluates whether a request from client_id is permitted."""
        pass

    @abc.abstractmethod
    def reset(self) -> None:
        """Clears rate-limiting state (for test isolation)."""
        pass


class InMemoryRateLimiter(BaseRateLimiter):
    """Thread-safe process-local sliding-window rate limiter."""

    def __init__(
        self,
        default_limit: int = RATE_LIMIT_REQUESTS,
        default_window: int = RATE_LIMIT_WINDOW_SECONDS,
        enabled: bool = RATE_LIMIT_ENABLED,
    ) -> None:
        self.default_limit = default_limit
        self.default_window = default_window
        self.enabled = enabled
        self._lock = threading.Lock()
        self._clients: Dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def reset(self) -> None:
        """Clears all tracking history (for test isolation)."""
        with self._lock:
            self._clients.clear()

    def is_allowed(
        self,
        client_id: str,
        limit: Optional[int] = None,
        window: Optional[int] = None,
    ) -> Tuple[bool, int]:
        """Evaluates whether a request from client_id is permitted."""
        if not self.enabled:
            return True, 0

        max_reqs = limit if limit is not None else self.default_limit
        window_sec = window if window is not None else self.default_window

        now = time.time()
        cutoff = now - window_sec

        with self._lock:
            timestamps = self._clients[client_id]

            # Evict timestamps outside active sliding window
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) < max_reqs:
                timestamps.append(now)
                return True, 0
            else:
                oldest = timestamps[0]
                retry_after = max(1, int(window_sec - (now - oldest)))
                logger.warning(
                    f"Rate limit exceeded for client '{client_id}': {len(timestamps)}/{max_reqs} "
                    f"in {window_sec}s. Retry after {retry_after}s."
                )
                return False, retry_after


class RedisRateLimiter(BaseRateLimiter):
    """Distributed sliding-window rate limiter backed by Redis ZSET.

    Gracefully falls back to InMemoryRateLimiter if Redis connection fails.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        default_limit: int = RATE_LIMIT_REQUESTS,
        default_window: int = RATE_LIMIT_WINDOW_SECONDS,
        enabled: bool = RATE_LIMIT_ENABLED,
    ) -> None:
        self.redis_url = redis_url or REDIS_URL
        self.default_limit = default_limit
        self.default_window = default_window
        self.enabled = enabled
        self._fallback_limiter = InMemoryRateLimiter(
            default_limit=default_limit,
            default_window=default_window,
            enabled=enabled,
        )
        self._client = None
        self._init_redis()

    def _init_redis(self) -> None:
        if not self.redis_url:
            return
        try:
            import importlib
            redis_mod = importlib.import_module("redis")
            self._client = redis_mod.Redis.from_url(
                self.redis_url,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                decode_responses=True,
            )
            self._client.ping()
            logger.info("Connected to Redis for distributed rate limiting.")
        except Exception as e:
            logger.warning(f"Redis unavailable for rate limiting ({e}). Using local in-memory fallback.")
            self._client = None

    def is_allowed(
        self,
        client_id: str,
        limit: Optional[int] = None,
        window: Optional[int] = None,
    ) -> Tuple[bool, int]:
        if not self.enabled:
            return True, 0

        max_reqs = limit if limit is not None else self.default_limit
        window_sec = window if window is not None else self.default_window

        if self._client is None:
            return self._fallback_limiter.is_allowed(client_id, max_reqs, window_sec)

        now = time.time()
        cutoff = now - window_sec
        key = f"paypilot:ratelimit:{client_id}"

        try:
            pipe = self._client.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            pipe.zrange(key, 0, 0, withscores=True)
            pipe.expire(key, window_sec * 2)
            results = pipe.execute()

            current_count = results[1]
            oldest_entries = results[2]

            if current_count < max_reqs:
                self._client.zadd(key, {str(now): now})
                return True, 0
            else:
                retry_after = 1
                if oldest_entries:
                    oldest_time = float(oldest_entries[0][1])
                    retry_after = max(1, int(window_sec - (now - oldest_time)))
                return False, retry_after
        except Exception as exc:
            logger.warning(f"Redis rate limit evaluation error ({exc}). Falling back to in-memory limiter.")
            return self._fallback_limiter.is_allowed(client_id, max_reqs, window_sec)

    def reset(self) -> None:
        if self._client:
            try:
                keys = self._client.keys("paypilot:ratelimit:*")
                if keys:
                    self._client.delete(*keys)
            except Exception:
                pass
        self._fallback_limiter.reset()


# Factory and Singleton Management
_LIMITER_LOCK = threading.Lock()
_GLOBAL_RATE_LIMITER: Optional[BaseRateLimiter] = None
rate_limiter = InMemoryRateLimiter()


def get_rate_limiter() -> BaseRateLimiter:
    """Factory accessing or initializing the active rate limiter."""
    global rate_limiter, _GLOBAL_RATE_LIMITER
    with _LIMITER_LOCK:
        if _GLOBAL_RATE_LIMITER is not None:
            return _GLOBAL_RATE_LIMITER

        backend = os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()
        if backend == "redis" and REDIS_URL:
            logger.info("Initializing distributed RedisRateLimiter.")
            _GLOBAL_RATE_LIMITER = RedisRateLimiter()
            return _GLOBAL_RATE_LIMITER
        return rate_limiter


def set_rate_limiter(limiter: BaseRateLimiter) -> None:
    """Explicitly replaces active rate limiter instance (for testing)."""
    global _GLOBAL_RATE_LIMITER
    with _LIMITER_LOCK:
        _GLOBAL_RATE_LIMITER = limiter


def reset_rate_limiter() -> None:
    """Resets active rate limiter instance (for test isolation)."""
    global _GLOBAL_RATE_LIMITER, rate_limiter
    with _LIMITER_LOCK:
        if _GLOBAL_RATE_LIMITER is not None:
            _GLOBAL_RATE_LIMITER.reset()
        _GLOBAL_RATE_LIMITER = None
        rate_limiter.reset()

