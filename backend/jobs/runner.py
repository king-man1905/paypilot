"""Job Runner and Bounded Execution Pipeline for PayPilot Background Jobs.

Executes asynchronous jobs in a bounded thread pool with atomic job leasing,
state transitions, resilience integration, observability metrics, and structured audit logs.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from backend.config import get_job_max_queue_size, get_job_max_workers, get_shutdown_timeout_seconds
from backend.jobs.models import JobRecord, JobStatus
from backend.jobs.store import BaseJobStore, get_job_store, reset_job_store
from backend.observability.audit import record_audit_event
from backend.observability.metrics import (
    record_error,
    record_job_completed,
    record_job_failed,
    record_job_submitted,
)
from backend.utils.redaction import redact_sensitive_dict, summarize_query_safely

logger = logging.getLogger("paypilot.jobs.runner")


class RunnerState(str, Enum):
    """Lifecycle states of the JobRunner."""
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"


class JobQueueFullError(Exception):
    """Raised when the background job queue exceeds maximum capacity."""
    pass


class JobRunnerDrainingError(Exception):
    """Raised when job submission is rejected because the runner is draining."""
    pass


class JobRunnerStoppedError(Exception):
    """Raised when job submission is rejected because the runner is stopped."""
    pass


class JobRunner:
    """Bounded worker pool that executes background tasks and updates job lifecycle states."""

    def __init__(
        self,
        store: Optional[BaseJobStore] = None,
        max_workers: Optional[int] = None,
        max_queue_size: Optional[int] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self.store = store or get_job_store()
        self.max_workers = max_workers or get_job_max_workers()
        self.max_queue_size = max_queue_size or get_job_max_queue_size()
        self.worker_id = worker_id or f"worker_{uuid.uuid4().hex[:8]}"
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"paypilot-{self.worker_id}",
        )
        self._active_jobs: set = set()
        self._state: RunnerState = RunnerState.RUNNING
        self._lock = threading.Lock()

    @property
    def state(self) -> RunnerState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state == RunnerState.RUNNING

    @property
    def is_draining(self) -> bool:
        with self._lock:
            return self._state == RunnerState.DRAINING

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            return self._state == RunnerState.STOPPED

    @property
    def active_job_count(self) -> int:
        with self._lock:
            return len(self._active_jobs)

    def submit_job(
        self,
        task_type: str,
        client_id: str,
        role: str,
        request_id: Optional[str],
        parameters: Dict[str, Any],
        target_fn: Callable[..., Any],
        *args: Any,
        trace_id: Optional[str] = None,
        **kwargs: Any,
    ) -> JobRecord:
        """Submits a new background job to the bounded execution pool.

        Args:
            task_type: Categorical task type identifier.
            client_id: Authenticated principal owner.
            role: Principal role (analyst / admin).
            request_id: Correlation tracking ID.
            parameters: Job input parameters (sanitized).
            target_fn: Executable task function.
            trace_id: Optional distributed trace tracking ID.

        Returns:
            JobRecord: Initial job record in QUEUED status.

        Raises:
            JobRunnerDrainingError: If runner is currently draining.
            JobRunnerStoppedError: If runner is stopped.
            JobQueueFullError: If active/queued task count exceeds max_queue_size.
        """
        from backend.observability.tracing import get_current_trace_context

        with self._lock:
            if self._state == RunnerState.DRAINING:
                logger.warning(
                    f"[{self.worker_id}] Job submission rejected: Runner is currently draining."
                )
                raise JobRunnerDrainingError("Job runner is currently draining and not accepting new tasks.")

            if self._state == RunnerState.STOPPED:
                logger.warning(
                    f"[{self.worker_id}] Job submission rejected: Runner is stopped."
                )
                raise JobRunnerStoppedError("Job runner is stopped.")

            if len(self._active_jobs) >= self.max_queue_size:
                logger.warning(
                    f"[{self.worker_id}] Job submission rejected: Queue depth {len(self._active_jobs)} reached max capacity {self.max_queue_size}."
                )
                record_error("queue_full")
                raise JobQueueFullError(
                    f"Background job queue is full ({self.max_queue_size} pending/running jobs). Try again later."
                )

        current_ctx = get_current_trace_context()
        resolved_trace_id = trace_id or (current_ctx.trace_id if current_ctx else f"tr_{uuid.uuid4().hex[:16]}")

        sanitized_params = redact_sensitive_dict(parameters)
        job = JobRecord(
            task_type=task_type,
            client_id=client_id,
            role=role,
            request_id=request_id,
            trace_id=resolved_trace_id,
            status=JobStatus.QUEUED.value,
            parameters=sanitized_params,
        )

        self.store.save_job(job)
        record_job_submitted()

        # Emit audit event for job submission
        q_summary = summarize_query_safely(str(parameters.get("query", "")), max_chars=60)
        record_audit_event(
            event_type="job_submitted",
            request_id=request_id or job.job_id,
            endpoint="/api/v1/jobs",
            http_method="POST",
            client_id=client_id,
            role=role,
            status="queued",
            status_code=202,
            query_summary=q_summary if q_summary else None,
        )

        with self._lock:
            self._active_jobs.add(job.job_id)

        # Dispatch execution to thread worker
        self._executor.submit(self._execute_job, job, target_fn, *args, **kwargs)
        return job

    def _execute_job(
        self,
        job: JobRecord,
        target_fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Internal execution wrapper updating job lifecycle and tracking failures."""
        from backend.observability.tracing import (
            TraceContext,
            reset_trace_context,
            set_current_trace_context,
            trace_span,
        )

        start_time = time.perf_counter()

        # Atomic Claim Mechanism to prevent duplicate execution across workers
        claimed = self.store.claim_job(job.job_id, worker_id=self.worker_id)
        if not claimed:
            logger.warning(
                f"[{self.worker_id}][{job.job_id}] Skipped execution: Job already claimed or no longer queued."
            )
            with self._lock:
                self._active_jobs.discard(job.job_id)
            return

        # Reload updated state
        claimed_job = self.store.get_job(job.job_id)
        if claimed_job:
            job = claimed_job

        logger.info(f"[{self.worker_id}][{job.job_id}] Job '{job.task_type}' status transitioned to RUNNING.")

        # Activate Trace Context for worker thread
        root_ctx = TraceContext(
            trace_id=job.trace_id or f"tr_{job.job_id}",
            request_id=job.request_id,
            span_id=f"sp_{uuid.uuid4().hex[:12]}",
            parent_span_id=None,
        )
        token = set_current_trace_context(root_ctx)

        try:
            with trace_span("job.execute", component="job", metadata={"job_id": job.job_id, "task_type": job.task_type}):
                result = target_fn(*args, **kwargs)

            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            job.status = JobStatus.COMPLETED.value
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.duration_ms = duration_ms
            job.result = redact_sensitive_dict(result) if isinstance(result, dict) else {"output": str(result)}
            self.store.update_job(job)

            record_job_completed(duration_ms=duration_ms)
            logger.info(f"[{self.worker_id}][{job.job_id}] Job COMPLETED in {duration_ms}ms.")

            # Record audit event on completion
            record_audit_event(
                event_type="job_completed",
                request_id=job.request_id or job.job_id,
                endpoint="/api/v1/jobs",
                http_method="ASYNC",
                client_id=job.client_id,
                role=job.role,
                status="success",
                status_code=200,
                duration_ms=duration_ms,
            )


        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(f"[{self.worker_id}][{job.job_id}] Job FAILED after {duration_ms}ms: {type(exc).__name__}: {exc}")

            job.status = JobStatus.FAILED.value
            job.completed_at = datetime.now(timezone.utc).isoformat()
            job.duration_ms = duration_ms
            job.error = {
                "category": "job_execution_error",
                "message": f"Job failed during execution: {type(exc).__name__}: {exc}",
            }
            self.store.update_job(job)

            record_job_failed()
            record_error("job_error")

            record_audit_event(
                event_type="job_failed",
                request_id=job.request_id or job.job_id,
                endpoint="/api/v1/jobs",
                http_method="ASYNC",
                client_id=job.client_id,
                role=job.role,
                status="failed",
                status_code=500,
                duration_ms=duration_ms,
                error_category="job_execution_error",
            )

        finally:
            if token is not None:
                reset_trace_context(token)
            with self._lock:
                self._active_jobs.discard(job.job_id)
            try:
                from backend.security.quotas import get_quota_manager
                get_quota_manager().record_job_finished(job.client_id)
            except Exception:
                pass


    def get_job(
        self,
        job_id: str,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[JobRecord]:
        """Fetches job by ID respecting tenant authorization."""
        return self.store.get_job(job_id=job_id, client_id=client_id, role=role)

    def list_jobs(
        self,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        """Lists paginated jobs respecting tenant authorization."""
        return self.store.list_jobs(
            client_id=client_id,
            role=role,
            status=status,
            limit=limit,
            offset=offset,
        )

    def count(self, client_id: Optional[str] = None, role: Optional[str] = None) -> int:
        """Counts jobs for tenant."""
        return self.store.count(client_id=client_id, role=role)

    def drain(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        """Gracefully drains the worker fleet, allowing running jobs to complete up to timeout.

        Transitions state to DRAINING, rejects new jobs, polls active jobs,
        and transitions to STOPPED.
        """
        timeout = timeout_seconds if timeout_seconds is not None else get_shutdown_timeout_seconds()
        start_time = time.perf_counter()

        with self._lock:
            self._state = RunnerState.DRAINING
            initial_active = len(self._active_jobs)

        logger.info(
            f"[{self.worker_id}] Initiating graceful job runner drain ({initial_active} active jobs, timeout={timeout}s)..."
        )

        # Poll for completion of active jobs
        while time.perf_counter() - start_time < timeout:
            with self._lock:
                if len(self._active_jobs) == 0:
                    break
            time.sleep(0.02)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        with self._lock:
            remaining = len(self._active_jobs)
            self._state = RunnerState.STOPPED

        # Non-blocking executor shutdown
        self._executor.shutdown(wait=False)

        if remaining > 0:
            logger.warning(
                f"[{self.worker_id}] Drain timeout reached after {duration_ms}ms with {remaining} jobs still active. "
                "Tasks remain recoverable via lease recovery."
            )
        else:
            logger.info(f"[{self.worker_id}] All background jobs drained cleanly in {duration_ms}ms.")

        return {
            "status": self._state.value,
            "active_jobs_remaining": remaining,
            "drained_cleanly": remaining == 0,
            "drain_duration_ms": duration_ms,
        }

    def stop(self, wait: bool = True) -> None:
        """Stops the runner immediately."""
        with self._lock:
            self._state = RunnerState.STOPPED
        self._executor.shutdown(wait=wait)

    def shutdown(self, wait: bool = True) -> None:
        """Alias for stop()."""
        self.stop(wait=wait)


# Singleton JobRunner Manager
_RUNNER_LOCK = threading.Lock()
_GLOBAL_RUNNER: Optional[JobRunner] = None


def get_job_runner() -> JobRunner:
    """Singleton accessor for active JobRunner."""
    global _GLOBAL_RUNNER
    with _RUNNER_LOCK:
        if _GLOBAL_RUNNER is None:
            _GLOBAL_RUNNER = JobRunner()
        return _GLOBAL_RUNNER


def set_job_runner(runner: JobRunner) -> None:
    """Sets the active JobRunner instance (for testing/mocking)."""
    global _GLOBAL_RUNNER
    with _RUNNER_LOCK:
        _GLOBAL_RUNNER = runner


def reset_job_runner() -> None:
    """Resets the singleton JobRunner instance (used for test isolation)."""
    global _GLOBAL_RUNNER
    with _RUNNER_LOCK:
        if _GLOBAL_RUNNER is not None:
            _GLOBAL_RUNNER.store.reset()
            _GLOBAL_RUNNER.shutdown(wait=False)
        _GLOBAL_RUNNER = None
    reset_job_store()
