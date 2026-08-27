"""Job Storage and State Management for PayPilot Background Jobs.

Provides:
1. BaseJobStore: Abstract interface with atomic lease/claim and crash recovery methods.
2. InMemoryJobStore: Thread-safe bounded in-memory store with FIFO eviction and lease expiration.
3. SQLJobStore: Shared relational store using SQLAlchemy for multi-worker deployments with atomic lease recovery.
4. Factory management dynamically selecting backend based on JOB_STORE_BACKEND.
"""

import abc
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, desc, func, or_, select, update

from backend.config import get_job_lease_timeout, get_job_max_retained_jobs
from backend.jobs.models import JobModel, JobRecord, JobStatus
from backend.storage.connection import get_db_engine, get_db_session
from backend.storage.models import Base

logger = logging.getLogger("paypilot.jobs.store")


class BaseJobStore(abc.ABC):
    """Abstract interface defining job persistence and query operations."""

    @abc.abstractmethod
    def save_job(self, job: JobRecord) -> None:
        """Stores a newly created job record."""
        pass

    @abc.abstractmethod
    def update_job(self, job: JobRecord) -> None:
        """Updates an existing job record."""
        pass

    @abc.abstractmethod
    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        lease_timeout_seconds: Optional[int] = None,
    ) -> bool:
        """Atomically claims a queued job or recovers an expired lease for worker_id."""
        pass

    @abc.abstractmethod
    def recover_stale_jobs(self, lease_timeout_seconds: Optional[int] = None) -> int:
        """Finds and resets stale RUNNING jobs whose leases expired back to QUEUED."""
        pass

    @abc.abstractmethod
    def get_job(
        self,
        job_id: str,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[JobRecord]:
        """Retrieves a single job by ID, respecting tenant isolation."""
        pass

    @abc.abstractmethod
    def list_jobs(
        self,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        """Returns paginated jobs, respecting tenant isolation."""
        pass

    @abc.abstractmethod
    def count(self, client_id: Optional[str] = None, role: Optional[str] = None) -> int:
        """Returns total number of retained jobs matching tenant scope."""
        pass

    @abc.abstractmethod
    def reset(self) -> None:
        """Clears all stored job records."""
        pass


class InMemoryJobStore(BaseJobStore):
    """Thread-safe bounded in-memory job store with FIFO eviction and lease timeout recovery."""

    def __init__(self, max_retained: Optional[int] = None) -> None:
        self._max_retained = max_retained or get_job_max_retained_jobs()
        self._jobs: OrderedDict[str, JobRecord] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def max_retained(self) -> int:
        return self._max_retained

    def save_job(self, job: JobRecord) -> None:
        with self._lock:
            # Enforce bounded capacity via FIFO eviction
            while len(self._jobs) >= self._max_retained:
                self._jobs.popitem(last=False)
            self._jobs[job.job_id] = job

    def update_job(self, job: JobRecord) -> None:
        with self._lock:
            if job.job_id in self._jobs:
                self._jobs[job.job_id] = job
            else:
                self.save_job(job)

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        lease_timeout_seconds: Optional[int] = None,
    ) -> bool:
        """Atomically claims a queued job or recovers an expired running lease in memory."""
        timeout = lease_timeout_seconds if lease_timeout_seconds is not None else get_job_lease_timeout()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            is_queued = job.status == JobStatus.QUEUED.value
            is_stale = False

            if job.status == JobStatus.RUNNING.value and job.started_at:
                try:
                    started_dt = datetime.fromisoformat(job.started_at)
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=timezone.utc)
                    if (now - started_dt).total_seconds() >= timeout:
                        is_stale = True
                except Exception:
                    pass

            if is_queued or is_stale:
                if is_stale:
                    job.retry_count += 1
                    logger.warning(
                        f"[{job_id}] Recovered stale running job lease (worker: {job.worker_id}) -> re-claimed by {worker_id}."
                    )
                job.status = JobStatus.RUNNING.value
                job.worker_id = worker_id
                job.started_at = now_iso
                return True

            return False

    def recover_stale_jobs(self, lease_timeout_seconds: Optional[int] = None) -> int:
        """Resets all expired RUNNING jobs back to QUEUED."""
        timeout = lease_timeout_seconds if lease_timeout_seconds is not None else get_job_lease_timeout()
        now = datetime.now(timezone.utc)
        recovered_count = 0

        with self._lock:
            for job in self._jobs.values():
                if job.status == JobStatus.RUNNING.value and job.started_at:
                    try:
                        started_dt = datetime.fromisoformat(job.started_at)
                        if started_dt.tzinfo is None:
                            started_dt = started_dt.replace(tzinfo=timezone.utc)
                        if (now - started_dt).total_seconds() >= timeout:
                            job.status = JobStatus.QUEUED.value
                            job.worker_id = None
                            job.started_at = None
                            job.retry_count += 1
                            recovered_count += 1
                    except Exception:
                        pass

        return recovered_count

    def get_job(
        self,
        job_id: str,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            # Tenant isolation: non-admin can only access own jobs
            if role != "admin" and client_id is not None and job.client_id != client_id:
                logger.warning(
                    f"Forbidden job access: Client '{client_id}' attempted to access job '{job_id}' owned by '{job.client_id}'."
                )
                return None

            return job

    def list_jobs(
        self,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        with self._lock:
            all_jobs = list(reversed(self._jobs.values()))

            if role != "admin" and client_id is not None:
                all_jobs = [j for j in all_jobs if j.client_id == client_id]

            if status:
                norm_status = status.strip().lower()
                all_jobs = [j for j in all_jobs if j.status.lower() == norm_status]

            return all_jobs[offset : offset + limit]

    def count(self, client_id: Optional[str] = None, role: Optional[str] = None) -> int:
        with self._lock:
            if role == "admin" or client_id is None:
                return len(self._jobs)
            return sum(1 for j in self._jobs.values() if j.client_id == client_id)

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()


class SQLJobStore(BaseJobStore):
    """Shared relational job store backed by SQLAlchemy with atomic lease timeout & crash recovery."""

    def __init__(self) -> None:
        engine = get_db_engine()
        Base.metadata.create_all(bind=engine, tables=[JobModel.__table__])
        # Safe schema migration for newly added columns
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(engine)
            existing_cols = [c["name"] for c in inspector.get_columns("paypilot_jobs")]
            if "trace_id" not in existing_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE paypilot_jobs ADD COLUMN trace_id VARCHAR(64)"))
        except Exception as e:
            logger.debug(f"Notice during SQLJobStore schema check: {e}")
        self._lock = threading.Lock()

    def save_job(self, job: JobRecord) -> None:
        with self._lock:
            with get_db_session() as session:
                model = JobModel.from_record(job)
                session.merge(model)
                session.commit()

    def update_job(self, job: JobRecord) -> None:
        self.save_job(job)

    def claim_job(
        self,
        job_id: str,
        worker_id: str,
        lease_timeout_seconds: Optional[int] = None,
    ) -> bool:
        """Atomically transitions status from 'queued' or stale 'running' to 'running' for worker_id."""
        timeout = lease_timeout_seconds if lease_timeout_seconds is not None else get_job_lease_timeout()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        stale_cutoff_iso = (now - timedelta(seconds=timeout)).isoformat()

        with self._lock:
            with get_db_session() as session:
                stmt = (
                    update(JobModel)
                    .where(
                        JobModel.job_id == job_id,
                        or_(
                            JobModel.status == JobStatus.QUEUED.value,
                            and_(
                                JobModel.status == JobStatus.RUNNING.value,
                                JobModel.started_at <= stale_cutoff_iso,
                            ),
                        ),
                    )
                    .values(
                        status=JobStatus.RUNNING.value,
                        worker_id=worker_id,
                        started_at=now_iso,
                    )
                )
                res = session.execute(stmt)
                session.commit()
                rowcount = getattr(res, "rowcount", 0) or 0
                return rowcount > 0

    def recover_stale_jobs(self, lease_timeout_seconds: Optional[int] = None) -> int:
        """Finds and resets expired RUNNING jobs back to QUEUED."""
        timeout = lease_timeout_seconds if lease_timeout_seconds is not None else get_job_lease_timeout()
        now = datetime.now(timezone.utc)
        stale_cutoff_iso = (now - timedelta(seconds=timeout)).isoformat()

        with self._lock:
            with get_db_session() as session:
                stmt = (
                    update(JobModel)
                    .where(
                        JobModel.status == JobStatus.RUNNING.value,
                        JobModel.started_at <= stale_cutoff_iso,
                    )
                    .values(
                        status=JobStatus.QUEUED.value,
                        worker_id=None,
                        started_at=None,
                        retry_count=JobModel.retry_count + 1,
                    )
                )
                res = session.execute(stmt)
                session.commit()
                return int(getattr(res, "rowcount", 0) or 0)

    def get_job(
        self,
        job_id: str,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> Optional[JobRecord]:
        with self._lock:
            with get_db_session() as session:
                stmt = select(JobModel).where(JobModel.job_id == job_id)
                model = session.execute(stmt).scalars().first()
                if not model:
                    return None

                # Tenant isolation
                if role != "admin" and client_id is not None and model.client_id != client_id:
                    logger.warning(
                        f"Forbidden job access: Client '{client_id}' attempted to access SQL job '{job_id}' owned by '{model.client_id}'."
                    )
                    return None

                return model.to_record()

    def list_jobs(
        self,
        client_id: Optional[str] = None,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobRecord]:
        with self._lock:
            with get_db_session() as session:
                stmt = select(JobModel).order_by(desc(JobModel.created_at))

                if role != "admin" and client_id is not None:
                    stmt = stmt.where(JobModel.client_id == client_id)

                if status:
                    stmt = stmt.where(JobModel.status == status.strip().lower())

                stmt = stmt.offset(offset).limit(limit)
                models = session.execute(stmt).scalars().all()
                return [m.to_record() for m in models]

    def count(self, client_id: Optional[str] = None, role: Optional[str] = None) -> int:
        with self._lock:
            with get_db_session() as session:
                stmt = select(func.count(JobModel.job_id))
                if role != "admin" and client_id is not None:
                    stmt = stmt.where(JobModel.client_id == client_id)
                return session.execute(stmt).scalar() or 0

    def reset(self) -> None:
        with self._lock:
            with get_db_session() as session:
                session.query(JobModel).delete()
                session.commit()



# Singleton JobStore Factory
_STORE_LOCK = threading.Lock()
_GLOBAL_JOB_STORE: Optional[BaseJobStore] = None


def get_job_store() -> BaseJobStore:
    """Singleton factory returning active BaseJobStore based on configuration."""
    global _GLOBAL_JOB_STORE
    with _STORE_LOCK:
        if _GLOBAL_JOB_STORE is not None:
            return _GLOBAL_JOB_STORE

        backend = os.getenv("JOB_STORE_BACKEND", "memory").strip().lower()
        if backend == "sql":
            logger.info("Initializing shared SQLJobStore backend.")
            _GLOBAL_JOB_STORE = SQLJobStore()
        else:
            _GLOBAL_JOB_STORE = InMemoryJobStore()
        return _GLOBAL_JOB_STORE


def set_job_store(store: BaseJobStore) -> None:
    """Sets active job store (for testing)."""
    global _GLOBAL_JOB_STORE
    with _STORE_LOCK:
        _GLOBAL_JOB_STORE = store


def reset_job_store() -> None:
    """Resets active job store instance (for test isolation)."""
    global _GLOBAL_JOB_STORE
    with _STORE_LOCK:
        if _GLOBAL_JOB_STORE is not None:
            _GLOBAL_JOB_STORE.reset()
        _GLOBAL_JOB_STORE = None
