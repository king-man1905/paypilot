"""Tenant Quota Management for PayPilot (Phase 21).

Provides multi-tenant quota enforcement for:
1. Daily / windowed interactive analysis queries per tenant
2. Maximum active concurrent background jobs per tenant
3. Daily background job submission quotas
4. Pluggable BaseQuotaManager with thread-safe in-memory and distributed Redis implementations
"""

import abc
from datetime import datetime, timezone
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from backend.config import (
    REDIS_URL,
    get_tenant_daily_analyze_quota,
    get_tenant_daily_job_quota,
    get_tenant_max_concurrent_jobs,
)

logger = logging.getLogger("paypilot.security.quotas")


class BaseQuotaManager(abc.ABC):
    """Abstract base class for PayPilot tenant quota enforcement."""

    @abc.abstractmethod
    def check_and_consume_analyze_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        """Checks if tenant has remaining analyze quota and increments if permitted.

        Returns:
            Tuple[bool, int, int]: (allowed, current_count, max_quota)
        """
        pass

    @abc.abstractmethod
    def check_and_consume_job_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        """Checks if tenant has remaining daily job submission quota and increments if permitted.

        Returns:
            Tuple[bool, int, int]: (allowed, current_count, max_quota)
        """
        pass

    @abc.abstractmethod
    def rollback_job_quota(self, tenant_id: str) -> None:
        """Rolls back 1 consumed job quota upon admission drop/queue rejection."""
        pass

    @abc.abstractmethod
    def rollback_analyze_quota(self, tenant_id: str) -> None:
        """Rolls back 1 consumed analyze quota upon admission drop."""
        pass

    @abc.abstractmethod
    def check_concurrent_job_limit(
        self,
        tenant_id: str,
        max_concurrent: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        """Checks if tenant can launch an additional concurrent background job.

        Returns:
            Tuple[bool, int, int]: (allowed, current_active_jobs, max_concurrent)
        """
        pass

    @abc.abstractmethod
    def record_job_started(self, tenant_id: str) -> None:
        """Increments active concurrent job count for tenant."""
        pass

    @abc.abstractmethod
    def record_job_finished(self, tenant_id: str) -> None:
        """Decrements active concurrent job count for tenant."""
        pass

    @abc.abstractmethod
    def reset(self) -> None:
        """Resets all quota tracking states (for testing isolation)."""
        pass


class InMemoryQuotaManager(BaseQuotaManager):
    """Thread-safe in-memory quota manager using local process state."""

    def __init__(self, day_provider: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._day_provider = day_provider
        self._analyze_counts: Dict[str, int] = {}
        self._job_counts: Dict[str, int] = {}
        self._active_jobs: Dict[str, int] = {}
        self._current_day_bucket = self._get_current_day()

    def _get_current_day(self) -> str:
        if self._day_provider and callable(self._day_provider):
            return str(self._day_provider())
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _ensure_day_bucket(self) -> None:
        today = self._get_current_day()
        if today != self._current_day_bucket:
            self._current_day_bucket = today
            self._analyze_counts.clear()
            self._job_counts.clear()

    def check_and_consume_analyze_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_quota = limit if limit is not None else get_tenant_daily_analyze_quota()
        with self._lock:
            self._ensure_day_bucket()
            current = self._analyze_counts.get(tenant_id, 0)
            if current >= max_quota:
                logger.warning(
                    f"Analyze quota exceeded for tenant '{tenant_id}': {current}/{max_quota} requests today."
                )
                return False, current, max_quota
            self._analyze_counts[tenant_id] = current + 1
            return True, current + 1, max_quota

    def rollback_analyze_quota(self, tenant_id: str) -> None:
        with self._lock:
            cur = self._analyze_counts.get(tenant_id, 0)
            if cur > 0:
                self._analyze_counts[tenant_id] = cur - 1

    def check_and_consume_job_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_quota = limit if limit is not None else get_tenant_daily_job_quota()
        with self._lock:
            self._ensure_day_bucket()
            current = self._job_counts.get(tenant_id, 0)
            if current >= max_quota:
                logger.warning(
                    f"Job submission quota exceeded for tenant '{tenant_id}': {current}/{max_quota} jobs today."
                )
                return False, current, max_quota
            self._job_counts[tenant_id] = current + 1
            return True, current + 1, max_quota

    def rollback_job_quota(self, tenant_id: str) -> None:
        with self._lock:
            cur = self._job_counts.get(tenant_id, 0)
            if cur > 0:
                self._job_counts[tenant_id] = cur - 1

    def check_concurrent_job_limit(
        self,
        tenant_id: str,
        max_concurrent: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_allowed = max_concurrent if max_concurrent is not None else get_tenant_max_concurrent_jobs()
        with self._lock:
            current_active = self._active_jobs.get(tenant_id, 0)
            if current_active >= max_allowed:
                logger.warning(
                    f"Concurrent job limit exceeded for tenant '{tenant_id}': {current_active}/{max_allowed} active jobs."
                )
                return False, current_active, max_allowed
            return True, current_active, max_allowed

    def record_job_started(self, tenant_id: str) -> None:
        with self._lock:
            self._active_jobs[tenant_id] = self._active_jobs.get(tenant_id, 0) + 1

    def record_job_finished(self, tenant_id: str) -> None:
        with self._lock:
            current = self._active_jobs.get(tenant_id, 0)
            if current > 0:
                self._active_jobs[tenant_id] = current - 1
            else:
                self._active_jobs[tenant_id] = 0

    def reset(self) -> None:
        with self._lock:
            self._analyze_counts.clear()
            self._job_counts.clear()
            self._active_jobs.clear()
            self._current_day_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RedisQuotaManager(BaseQuotaManager):
    """Distributed Redis-backed tenant quota manager with resilient in-memory fallback."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or REDIS_URL
        self._fallback = InMemoryQuotaManager()
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
            logger.info("Connected to Redis for distributed tenant quota management.")
        except Exception as e:
            logger.warning(f"Redis unavailable for quota manager ({e}). Using local in-memory fallback.")
            self._client = None

    def check_and_consume_analyze_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_quota = limit if limit is not None else get_tenant_daily_analyze_quota()
        if self._client is None:
            return self._fallback.check_and_consume_analyze_quota(tenant_id, max_quota)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"paypilot:quota:analyze:{tenant_id}:{today}"
        try:
            val = self._client.incr(key)
            if val == 1:
                self._client.expire(key, 86400 * 2)
            if val > max_quota:
                return False, val - 1, max_quota
            return True, val, max_quota
        except Exception as exc:
            logger.warning(f"Redis quota error ({exc}). Falling back to in-memory quota manager.")
            return self._fallback.check_and_consume_analyze_quota(tenant_id, max_quota)

    def rollback_analyze_quota(self, tenant_id: str) -> None:
        if self._client is None:
            self._fallback.rollback_analyze_quota(tenant_id)
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"paypilot:quota:analyze:{tenant_id}:{today}"
        try:
            val = self._client.decr(key)
            if val < 0:
                self._client.set(key, 0)
        except Exception:
            self._fallback.rollback_analyze_quota(tenant_id)

    def check_and_consume_job_quota(
        self,
        tenant_id: str,
        limit: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_quota = limit if limit is not None else get_tenant_daily_job_quota()
        if self._client is None:
            return self._fallback.check_and_consume_job_quota(tenant_id, max_quota)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"paypilot:quota:job:{tenant_id}:{today}"
        try:
            val = self._client.incr(key)
            if val == 1:
                self._client.expire(key, 86400 * 2)
            if val > max_quota:
                return False, val - 1, max_quota
            return True, val, max_quota
        except Exception as exc:
            logger.warning(f"Redis job quota error ({exc}). Falling back to in-memory.")
            return self._fallback.check_and_consume_job_quota(tenant_id, max_quota)

    def rollback_job_quota(self, tenant_id: str) -> None:
        if self._client is None:
            self._fallback.rollback_job_quota(tenant_id)
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"paypilot:quota:job:{tenant_id}:{today}"
        try:
            val = self._client.decr(key)
            if val < 0:
                self._client.set(key, 0)
        except Exception:
            self._fallback.rollback_job_quota(tenant_id)

    def check_concurrent_job_limit(
        self,
        tenant_id: str,
        max_concurrent: Optional[int] = None,
    ) -> Tuple[bool, int, int]:
        max_allowed = max_concurrent if max_concurrent is not None else get_tenant_max_concurrent_jobs()
        if self._client is None:
            return self._fallback.check_concurrent_job_limit(tenant_id, max_allowed)

        key = f"paypilot:quota:active_jobs:{tenant_id}"
        try:
            raw = self._client.get(key)
            current = int(raw) if raw is not None else 0
            if current >= max_allowed:
                return False, current, max_allowed
            return True, current, max_allowed
        except Exception as exc:
            logger.warning(f"Redis active jobs error ({exc}). Falling back to in-memory.")
            return self._fallback.check_concurrent_job_limit(tenant_id, max_allowed)

    def record_job_started(self, tenant_id: str) -> None:
        if self._client is None:
            self._fallback.record_job_started(tenant_id)
            return
        key = f"paypilot:quota:active_jobs:{tenant_id}"
        try:
            self._client.incr(key)
            self._client.expire(key, 3600)
        except Exception:
            self._fallback.record_job_started(tenant_id)

    def record_job_finished(self, tenant_id: str) -> None:
        if self._client is None:
            self._fallback.record_job_finished(tenant_id)
            return
        key = f"paypilot:quota:active_jobs:{tenant_id}"
        try:
            val = self._client.decr(key)
            if val < 0:
                self._client.set(key, 0)
        except Exception:
            self._fallback.record_job_finished(tenant_id)

    def reset(self) -> None:
        if self._client:
            try:
                keys = self._client.keys("paypilot:quota:*")
                if keys:
                    self._client.delete(*keys)
            except Exception:
                pass
        self._fallback.reset()


# Factory and Singleton Management
_QUOTA_LOCK = threading.Lock()
_GLOBAL_QUOTA_MANAGER: Optional[BaseQuotaManager] = None
_default_quota_manager = InMemoryQuotaManager()


def get_quota_manager() -> BaseQuotaManager:
    """Factory accessing or initializing the active QuotaManager."""
    global _default_quota_manager, _GLOBAL_QUOTA_MANAGER
    with _QUOTA_LOCK:
        if _GLOBAL_QUOTA_MANAGER is not None:
            return _GLOBAL_QUOTA_MANAGER

        backend = os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()
        if backend == "redis" and REDIS_URL:
            logger.info("Initializing distributed RedisQuotaManager.")
            _GLOBAL_QUOTA_MANAGER = RedisQuotaManager()
            return _GLOBAL_QUOTA_MANAGER
        return _default_quota_manager


def set_quota_manager(manager: BaseQuotaManager) -> None:
    """Sets active QuotaManager instance (for testing)."""
    global _GLOBAL_QUOTA_MANAGER
    with _QUOTA_LOCK:
        _GLOBAL_QUOTA_MANAGER = manager


def reset_quota_manager() -> None:
    """Resets active QuotaManager instance."""
    global _GLOBAL_QUOTA_MANAGER, _default_quota_manager
    with _QUOTA_LOCK:
        if _GLOBAL_QUOTA_MANAGER is not None:
            _GLOBAL_QUOTA_MANAGER.reset()
        _GLOBAL_QUOTA_MANAGER = None
        _default_quota_manager.reset()
