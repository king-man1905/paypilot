import pytest

from backend.jobs import reset_job_runner
from backend.jobs.store import reset_job_store
from backend.observability.audit import reset_audit_store
from backend.observability.metrics import reset_metrics
from backend.security.rate_limiter import reset_rate_limiter
from backend.storage import reset_transaction_repository
from backend.tools.analytics import clear_dataset_cache
from backend.utils.resilience import nvidia_circuit_breaker


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Ensures test suite operates offline in deterministic fallback mode by default.

    Individual tests that explicitly verify NVIDIA live initialization can override
    NVIDIA_API_KEY using monkeypatch.setenv().
    """
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("DATA_BACKEND", "csv")
    monkeypatch.setenv("JOB_STORE_BACKEND", "memory")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    nvidia_circuit_breaker.reset()
    reset_metrics()
    reset_audit_store()
    reset_rate_limiter()
    reset_job_runner()
    reset_job_store()
    reset_transaction_repository()
    clear_dataset_cache()
    yield
    nvidia_circuit_breaker.reset()
    reset_metrics()
    reset_audit_store()
    reset_rate_limiter()
    reset_job_runner()
    reset_job_store()
    reset_transaction_repository()
    clear_dataset_cache()

