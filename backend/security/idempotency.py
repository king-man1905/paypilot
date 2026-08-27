"""Request Idempotency Layer for PayPilot (Phase 21).

Provides:
1. Idempotency-Key header extraction, syntax validation, and payload hashing.
2. Atomic reservation semantics preventing duplicate concurrent job executions.
3. Multi-tenant namespace isolation (Tenant A and Tenant B keys never collide).
4. Conflict detection (409 Conflict) when an identical key is used with a different payload.
5. Pluggable BaseIdempotencyStore supporting InMemoryIdempotencyStore and distributed RedisIdempotencyStore.
"""

import abc
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from backend.config import (
    REDIS_URL,
    get_idempotency_max_records,
    get_idempotency_store_backend,
    get_idempotency_ttl_seconds,
)

logger = logging.getLogger("paypilot.security.idempotency")

# Syntax validation: 1 to 128 characters, alphanumeric with dashes, underscores, and colons
IDEMPOTENCY_KEY_REGEX = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")


class IdempotencyReservationStatus(str, Enum):
    """Outcome of attempting to reserve an Idempotency-Key."""
    RESERVED = "reserved"  # Key is reserved; caller must proceed with job execution
    REPLAY = "replay"      # Matching key and payload found; return cached response
    CONFLICT = "conflict"  # Same key used with a materially different payload


@dataclass
class IdempotencyRecord:
    """Stored record representing an idempotent operation state."""
    tenant_id: str
    key: str
    payload_hash: str
    created_at: float
    expires_at: float = 0.0
    job_id: Optional[str] = None
    status: str = "reserved"  # 'reserved', 'completed', 'failed'
    response_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdempotencyRecord":
        return cls(**data)


def validate_idempotency_key(key: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Validates syntax and bounded size of an Idempotency-Key header.

    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
    """
    if not key or not str(key).strip():
        return False, "Idempotency-Key header cannot be empty."

    clean_key = str(key).strip()
    if len(clean_key) > 128:
        return False, f"Idempotency-Key length ({len(clean_key)}) exceeds maximum limit of 128 characters."

    if not IDEMPOTENCY_KEY_REGEX.match(clean_key):
        return False, "Idempotency-Key contains invalid characters. Allowed: [A-Za-z0-9_\\-:.]."

    return True, None


def fingerprint_idempotency_key(key: Optional[str]) -> str:
    """Computes a non-sensitive SHA-256 fingerprint of an Idempotency-Key for safe logging/auditing."""
    if not key or not str(key).strip():
        return "none"
    return f"idem_{hashlib.sha256(str(key).strip().encode('utf-8')).hexdigest()[:12]}"


def compute_payload_hash(payload: Any) -> str:
    """Computes a deterministic SHA-256 hash of a normalized JSON-serializable request payload."""
    try:
        normalized_str = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        normalized_str = str(payload)
    return hashlib.sha256(normalized_str.encode("utf-8")).hexdigest()


class BaseIdempotencyStore(abc.ABC):
    """Abstract base interface for request idempotency persistence."""

    @abc.abstractmethod
    def reserve(
        self,
        tenant_id: str,
        key: str,
        payload_hash: str,
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[IdempotencyReservationStatus, Optional[IdempotencyRecord]]:
        """Atomically reserves or checks the status of an idempotency key.

        Returns:
            Tuple[IdempotencyReservationStatus, Optional[IdempotencyRecord]]
        """
        pass

    @abc.abstractmethod
    def complete(
        self,
        tenant_id: str,
        key: str,
        job_id: str,
        response_payload: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        """Updates the idempotency record upon successful job submission / completion."""
        pass

    @abc.abstractmethod
    def cancel_reservation(self, tenant_id: str, key: str) -> None:
        """Cancels an incomplete or aborted reservation so subsequent requests can proceed."""
        pass

    @abc.abstractmethod
    def get_record(self, tenant_id: str, key: str) -> Optional[IdempotencyRecord]:
        """Retrieves active idempotency record for tenant and key."""
        pass

    @abc.abstractmethod
    def reset(self) -> None:
        """Clears all stored idempotency keys (for testing isolation)."""
        pass

    def wait_for_completion(
        self,
        tenant_id: str,
        key: str,
        timeout_seconds: float = 3.0,
        poll_interval_seconds: float = 0.005,
    ) -> Optional[IdempotencyRecord]:
        """Polls until the idempotency record is completed with a job_id/response_payload or timeout occurs."""
        start = time.perf_counter()
        while time.perf_counter() - start < timeout_seconds:
            rec = self.get_record(tenant_id, key)
            if rec and (rec.response_payload is not None or rec.job_id is not None or rec.status in ("completed", "failed")):
                return rec
            time.sleep(poll_interval_seconds)
        return self.get_record(tenant_id, key)


class InMemoryIdempotencyStore(BaseIdempotencyStore):
    """Thread-safe bounded in-memory idempotency store with FIFO and TTL eviction."""

    def __init__(self, max_records: Optional[int] = None) -> None:
        self.max_records = max_records or get_idempotency_max_records()
        self._lock = threading.Lock()
        self._records: Dict[Tuple[str, str], IdempotencyRecord] = {}

    def _evict_expired_or_overflow(self) -> None:
        now = time.time()
        # Evict expired
        expired_keys = [
            k for k, r in self._records.items()
            if (r.expires_at > 0 and now >= r.expires_at)
        ]
        for k in expired_keys:
            self._records.pop(k, None)

        # Evict oldest if exceeding max capacity
        while len(self._records) >= self.max_records:
            oldest_key = min(self._records.keys(), key=lambda k: self._records[k].created_at)
            self._records.pop(oldest_key, None)

    def reserve(
        self,
        tenant_id: str,
        key: str,
        payload_hash: str,
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[IdempotencyReservationStatus, Optional[IdempotencyRecord]]:
        ttl = ttl_seconds if ttl_seconds is not None else get_idempotency_ttl_seconds()
        lookup_key = (tenant_id, key)
        now = time.time()

        with self._lock:
            self._evict_expired_or_overflow()

            existing = self._records.get(lookup_key)
            if existing is not None:
                # Check if expired
                if existing.expires_at > 0 and now >= existing.expires_at:
                    self._records.pop(lookup_key, None)
                else:
                    # Validate payload match
                    if existing.payload_hash == payload_hash:
                        return IdempotencyReservationStatus.REPLAY, existing
                    else:
                        logger.warning(
                            f"Idempotency conflict for tenant '{tenant_id}' key '{key}': payload hash mismatch."
                        )
                        return IdempotencyReservationStatus.CONFLICT, existing

            # Reserve new record atomically
            new_record = IdempotencyRecord(
                tenant_id=tenant_id,
                key=key,
                payload_hash=payload_hash,
                created_at=now,
                expires_at=now + ttl,
                status="reserved",
            )
            self._records[lookup_key] = new_record
            return IdempotencyReservationStatus.RESERVED, new_record

    def complete(
        self,
        tenant_id: str,
        key: str,
        job_id: str,
        response_payload: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        lookup_key = (tenant_id, key)
        with self._lock:
            record = self._records.get(lookup_key)
            if record is not None:
                record.job_id = job_id
                record.response_payload = response_payload
                record.status = status

    def cancel_reservation(self, tenant_id: str, key: str) -> None:
        lookup_key = (tenant_id, key)
        with self._lock:
            rec = self._records.get(lookup_key)
            if rec and rec.status == "reserved":
                self._records.pop(lookup_key, None)

    def get_record(self, tenant_id: str, key: str) -> Optional[IdempotencyRecord]:
        lookup_key = (tenant_id, key)
        with self._lock:
            return self._records.get(lookup_key)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


class RedisIdempotencyStore(BaseIdempotencyStore):
    """Distributed Redis-backed idempotency store with resilient in-memory fallback."""

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = redis_url or REDIS_URL
        self._fallback = InMemoryIdempotencyStore()
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
            logger.info("Connected to Redis for distributed request idempotency.")
        except Exception as e:
            logger.warning(f"Redis unavailable for idempotency store ({e}). Using local in-memory fallback.")
            self._client = None

    def reserve(
        self,
        tenant_id: str,
        key: str,
        payload_hash: str,
        ttl_seconds: Optional[int] = None,
    ) -> Tuple[IdempotencyReservationStatus, Optional[IdempotencyRecord]]:
        if self._client is None:
            return self._fallback.reserve(tenant_id, key, payload_hash, ttl_seconds)

        ttl = ttl_seconds if ttl_seconds is not None else get_idempotency_ttl_seconds()
        redis_key = f"paypilot:idempotency:{tenant_id}:{key}"

        try:
            # Atomic reservation via SET NX
            initial_record = IdempotencyRecord(
                tenant_id=tenant_id,
                key=key,
                payload_hash=payload_hash,
                created_at=time.time(),
                status="reserved",
            )
            payload_json = json.dumps(initial_record.to_dict())
            acquired = self._client.set(redis_key, payload_json, nx=True, ex=ttl)

            if acquired:
                return IdempotencyReservationStatus.RESERVED, initial_record

            # Key already exists -> fetch and inspect
            raw_existing = self._client.get(redis_key)
            if raw_existing:
                existing_dict = json.loads(raw_existing)
                existing_rec = IdempotencyRecord.from_dict(existing_dict)
                if existing_rec.payload_hash == payload_hash:
                    return IdempotencyReservationStatus.REPLAY, existing_rec
                else:
                    return IdempotencyReservationStatus.CONFLICT, existing_rec

            # Edge case if key expired between set and get
            return IdempotencyReservationStatus.RESERVED, initial_record
        except Exception as exc:
            logger.warning(f"Redis idempotency reservation error ({exc}). Falling back to in-memory.")
            return self._fallback.reserve(tenant_id, key, payload_hash, ttl_seconds)

    def complete(
        self,
        tenant_id: str,
        key: str,
        job_id: str,
        response_payload: Dict[str, Any],
        status: str = "completed",
    ) -> None:
        if self._client is None:
            self._fallback.complete(tenant_id, key, job_id, response_payload, status)
            return

        redis_key = f"paypilot:idempotency:{tenant_id}:{key}"
        ttl = get_idempotency_ttl_seconds()
        try:
            raw = self._client.get(redis_key)
            if raw:
                d = json.loads(raw)
                rec = IdempotencyRecord.from_dict(d)
                rec.job_id = job_id
                rec.response_payload = response_payload
                rec.status = status
                self._client.set(redis_key, json.dumps(rec.to_dict()), ex=ttl)
        except Exception as exc:
            logger.warning(f"Redis idempotency completion error ({exc}).")
            self._fallback.complete(tenant_id, key, job_id, response_payload, status)

    def cancel_reservation(self, tenant_id: str, key: str) -> None:
        if self._client is None:
            self._fallback.cancel_reservation(tenant_id, key)
            return
        redis_key = f"paypilot:idempotency:{tenant_id}:{key}"
        try:
            raw = self._client.get(redis_key)
            if raw:
                d = json.loads(raw)
                if d.get("status") == "reserved":
                    self._client.delete(redis_key)
        except Exception as exc:
            logger.warning(f"Redis cancel reservation error ({exc}).")
            self._fallback.cancel_reservation(tenant_id, key)

    def get_record(self, tenant_id: str, key: str) -> Optional[IdempotencyRecord]:
        if self._client is None:
            return self._fallback.get_record(tenant_id, key)

        redis_key = f"paypilot:idempotency:{tenant_id}:{key}"
        try:
            raw = self._client.get(redis_key)
            if raw:
                return IdempotencyRecord.from_dict(json.loads(raw))
            return None
        except Exception:
            return self._fallback.get_record(tenant_id, key)

    def reset(self) -> None:
        if self._client:
            try:
                keys = self._client.keys("paypilot:idempotency:*")
                if keys:
                    self._client.delete(*keys)
            except Exception:
                pass
        self._fallback.reset()


# Factory and Singleton Management
_IDEMPOTENCY_LOCK = threading.Lock()
_GLOBAL_IDEMPOTENCY_STORE: Optional[BaseIdempotencyStore] = None
_default_idempotency_store = InMemoryIdempotencyStore()


def get_idempotency_store() -> BaseIdempotencyStore:
    """Factory accessing or initializing the active IdempotencyStore."""
    global _default_idempotency_store, _GLOBAL_IDEMPOTENCY_STORE
    with _IDEMPOTENCY_LOCK:
        if _GLOBAL_IDEMPOTENCY_STORE is not None:
            return _GLOBAL_IDEMPOTENCY_STORE

        backend = get_idempotency_store_backend()
        if backend == "redis" and REDIS_URL:
            logger.info("Initializing distributed RedisIdempotencyStore.")
            _GLOBAL_IDEMPOTENCY_STORE = RedisIdempotencyStore()
            return _GLOBAL_IDEMPOTENCY_STORE
        return _default_idempotency_store


def set_idempotency_store(store: BaseIdempotencyStore) -> None:
    """Sets active IdempotencyStore instance (for testing)."""
    global _GLOBAL_IDEMPOTENCY_STORE
    with _IDEMPOTENCY_LOCK:
        _GLOBAL_IDEMPOTENCY_STORE = store


def reset_idempotency_store() -> None:
    """Resets active IdempotencyStore instance."""
    global _GLOBAL_IDEMPOTENCY_STORE, _default_idempotency_store
    with _IDEMPOTENCY_LOCK:
        if _GLOBAL_IDEMPOTENCY_STORE is not None:
            _GLOBAL_IDEMPOTENCY_STORE.reset()
        _GLOBAL_IDEMPOTENCY_STORE = None
        _default_idempotency_store.reset()
