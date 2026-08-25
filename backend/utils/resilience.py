"""Reliability & Resilience Utilities for PayPilot.

Provides transient error classification, exponential backoff retry execution with jitter,
and a thread-safe 3-state Circuit Breaker for upstream LLM provider calls.
"""

import logging
import random
import threading
import time
from typing import Any, Callable, Optional, TypeVar

from backend.config import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY_TIME,
    LLM_MAX_RETRIES,
    LLM_RETRY_BASE_DELAY,
    LLM_RETRY_MAX_DELAY,
)

logger = logging.getLogger("paypilot.resilience")

T = TypeVar("T")

TRANSIENT_EXCEPTION_NAMES = (
    "Timeout",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionError",
    "ConnectError",
    "RemoteDisconnected",
    "HTTPError",
    "APIConnectionError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailable",
    "BadGateway",
    "GatewayTimeout",
)

TRANSIENT_KEYWORDS = (
    "timeout",
    "timed out",
    "connection",
    "reset",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "temporarily unavailable",
    "try again",
)


def is_transient_error(exc: Exception) -> bool:
    """Determines whether an exception is transient (network/timeout/throttle) or permanent."""
    if exc is None:
        return False

    # Permanent business/code errors are never transient
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError, AssertionError, AttributeError)):
        return False

    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    if any(name.lower() in exc_name.lower() for name in TRANSIENT_EXCEPTION_NAMES):
        return True

    if any(kw in exc_msg for kw in TRANSIENT_KEYWORDS):
        return True

    return False


def execute_with_retry(
    fn: Callable[[], T],
    max_retries: int = LLM_MAX_RETRIES,
    base_delay: float = LLM_RETRY_BASE_DELAY,
    max_delay: float = LLM_RETRY_MAX_DELAY,
    jitter: bool = True,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> T:
    """Executes a callable with bounded exponential backoff on transient failures.

    Args:
        fn: Zero-argument callable to execute.
        max_retries: Maximum number of retry attempts (excluding initial attempt).
        base_delay: Initial retry backoff in seconds.
        max_delay: Upper cap on backoff delay in seconds.
        jitter: If True, applies +/-20% random jitter to avoid thundering herd.
        on_retry: Optional callback invoked on each retry with (attempt, exception, delay).

    Returns:
        T: Result from successful callable execution.

    Raises:
        Exception: The last encountered exception if all retries are exhausted or non-transient.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not is_transient_error(exc) or attempt >= max_retries:
                raise exc

            attempt += 1
            # Exponential backoff formula: base * 2^(attempt - 1)
            raw_delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

            # Apply random jitter if enabled
            if jitter:
                factor = random.uniform(0.8, 1.2)
                delay = round(raw_delay * factor, 3)
            else:
                delay = round(raw_delay, 3)

            logger.warning(
                f"Transient failure (attempt {attempt}/{max_retries}: {type(exc).__name__}: {exc}). "
                f"Backing off for {delay:.3f}s..."
            )

            if on_retry is not None:
                try:
                    on_retry(attempt, exc, delay)
                except Exception as cb_err:
                    logger.debug(f"Error in on_retry callback: {cb_err}")

            if delay > 0:
                time.sleep(delay)


class CircuitBreaker:
    """Thread-safe 3-state Circuit Breaker for upstream provider protection.

    States:
        CLOSED: Normal operation, requests are permitted.
        OPEN: Upstream is failing repeatedly; requests are blocked immediately to protect capacity.
        HALF_OPEN: Cooldown expired; a single probe request is permitted to test recovery.
    """

    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_time: float = CIRCUIT_BREAKER_RECOVERY_TIME,
    ) -> None:
        self.threshold = threshold
        self.recovery_time = recovery_time
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Resets circuit breaker to clean CLOSED initial state."""
        with self._lock:
            self._state = self.STATE_CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0
            self._success_count = 0

    @property
    def state(self) -> str:
        """Returns the current state, evaluating cooldown transitions."""
        with self._lock:
            if self._state == self.STATE_OPEN:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.recovery_time:
                    self._state = self.STATE_HALF_OPEN
                    logger.info("Circuit breaker entered HALF_OPEN state; permitting probe call.")
            return self._state

    def can_execute(self) -> bool:
        """Checks if a request is permitted to proceed."""
        current_state = self.state
        return current_state in (self.STATE_CLOSED, self.STATE_HALF_OPEN)

    def record_success(self) -> None:
        """Records a successful call, closing the circuit if in HALF_OPEN."""
        with self._lock:
            if self._state in (self.STATE_HALF_OPEN, self.STATE_OPEN):
                logger.info("Circuit breaker recorded probe success; transitioning to CLOSED.")
            self._state = self.STATE_CLOSED
            self._failure_count = 0
            self._success_count += 1

    def record_failure(self, is_transient: bool = True) -> None:
        """Records a failure, tripping the circuit to OPEN if threshold is reached."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == self.STATE_HALF_OPEN or self._failure_count >= self.threshold:
                if self._state != self.STATE_OPEN:
                    logger.warning(
                        f"Circuit breaker tripped to OPEN state after {self._failure_count} failures. "
                        f"Cooldown window: {self.recovery_time}s."
                    )
                self._state = self.STATE_OPEN


# Singleton circuit breaker for NVIDIA provider
nvidia_circuit_breaker = CircuitBreaker()
