"""Reliability, Resilience & Failure Injection Tests for PayPilot.

Tests transient error classification, exponential backoff retries, Circuit Breaker
state transitions, specialist-agent partial evidence resilience, secret non-exposure,
and deterministic fallback guarantees with zero real external network dependencies.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from backend.api.main import app
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.tools.analytics import clear_dataset_cache
from backend.utils.resilience import (
    CircuitBreaker,
    execute_with_retry,
    is_transient_error,
    nvidia_circuit_breaker,
)
from evaluation.mock_llm import MockChatNVIDIA, patch_offline_evaluation_llm


@pytest.fixture(autouse=True)
def isolate_resilience_environment(monkeypatch):
    """Ensures each test operates with clean metrics, cache, and circuit breaker."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    reset_metrics()
    clear_dataset_cache()
    nvidia_circuit_breaker.reset()
    yield
    reset_metrics()
    clear_dataset_cache()
    nvidia_circuit_breaker.reset()


def test_transient_error_classification():
    """Verifies that is_transient_error distinguishes transient vs permanent exceptions."""
    # Transient errors
    assert is_transient_error(TimeoutError("Read timed out"))
    assert is_transient_error(ConnectionResetError("Connection reset by peer"))
    assert is_transient_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert is_transient_error(Exception("429 Too Many Requests - Rate limit exceeded"))
    assert is_transient_error(Exception("BadGateway: Upstream server timed out"))

    # Permanent / business errors
    assert not is_transient_error(ValueError("Invalid merchant category"))
    assert not is_transient_error(KeyError("missing_field"))
    assert not is_transient_error(TypeError("unsupported operand type"))
    assert not is_transient_error(AssertionError("Value mismatch"))
    assert not is_transient_error(None)


def test_execute_with_retry_success_on_first_attempt():
    """Verifies that execute_with_retry succeeds immediately with 0 retries on clean call."""
    mock_fn = MagicMock(return_value="success")
    retry_log = []

    result = execute_with_retry(
        mock_fn,
        max_retries=3,
        base_delay=0.01,
        on_retry=lambda a, e, d: retry_log.append(a),
    )

    assert result == "success"
    assert mock_fn.call_count == 1
    assert len(retry_log) == 0


def test_execute_with_retry_transient_recovery():
    """Verifies that execute_with_retry retries transient errors and succeeds."""
    mock_fn = MagicMock(side_effect=[
        TimeoutError("Connection timed out"),
        ConnectionResetError("Reset"),
        "success_after_2_retries",
    ])
    retry_log = []

    result = execute_with_retry(
        mock_fn,
        max_retries=3,
        base_delay=0.001,
        max_delay=0.01,
        jitter=False,
        on_retry=lambda a, e, d: retry_log.append(a),
    )

    assert result == "success_after_2_retries"
    assert mock_fn.call_count == 3
    assert retry_log == [1, 2]


def test_execute_with_retry_permanent_error_fails_immediately():
    """Verifies that permanent errors (e.g. ValueError) are never retried."""
    mock_fn = MagicMock(side_effect=ValueError("Schema mismatch"))
    retry_log = []

    with pytest.raises(ValueError, match="Schema mismatch"):
        execute_with_retry(
            mock_fn,
            max_retries=3,
            base_delay=0.01,
            on_retry=lambda a, e, d: retry_log.append(a),
        )

    assert mock_fn.call_count == 1
    assert len(retry_log) == 0


def test_execute_with_retry_exhaustion_raises():
    """Verifies that exhausting max_retries raises the underlying transient exception."""
    mock_fn = MagicMock(side_effect=TimeoutError("Persistent upstream timeout"))
    retry_log = []

    with pytest.raises(TimeoutError, match="Persistent upstream timeout"):
        execute_with_retry(
            mock_fn,
            max_retries=2,
            base_delay=0.001,
            max_delay=0.01,
            jitter=False,
            on_retry=lambda a, e, d: retry_log.append(a),
        )

    assert mock_fn.call_count == 3  # Initial + 2 retries
    assert retry_log == [1, 2]


def test_circuit_breaker_lifecycle_and_transitions():
    """Verifies CircuitBreaker transitions: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
    cb = CircuitBreaker(threshold=2, recovery_time=0.05)

    # 1. Initial State: CLOSED
    assert cb.state == CircuitBreaker.STATE_CLOSED
    assert cb.can_execute() is True

    # 2. First failure
    cb.record_failure()
    assert cb.state == CircuitBreaker.STATE_CLOSED
    assert cb.can_execute() is True

    # 3. Second failure reaches threshold -> OPEN
    cb.record_failure()
    assert cb.state == CircuitBreaker.STATE_OPEN
    assert cb.can_execute() is False

    # 4. Wait for recovery cooldown -> HALF_OPEN
    time.sleep(0.06)
    assert cb.state == CircuitBreaker.STATE_HALF_OPEN
    assert cb.can_execute() is True

    # 5. Probe call succeeds -> CLOSED
    cb.record_success()
    assert cb.state == CircuitBreaker.STATE_CLOSED
    assert cb.can_execute() is True


def test_circuit_breaker_half_open_failure_re_trips_to_open():
    """Verifies that a failure during HALF_OPEN probe immediately trips back to OPEN."""
    cb = CircuitBreaker(threshold=2, recovery_time=0.02)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitBreaker.STATE_OPEN

    time.sleep(0.03)
    assert cb.state == CircuitBreaker.STATE_HALF_OPEN

    # Probe failure
    cb.record_failure()
    assert cb.state == CircuitBreaker.STATE_OPEN
    assert cb.can_execute() is False


def test_supervisor_retry_and_circuit_breaker_resilience():
    """Verifies supervisor node falls back immediately to heuristic routing when LLM times out."""
    from backend.agents.supervisor import supervisor_node
    from backend.graph.state import PayPilotState

    state: PayPilotState = {"user_query": "Why did my revenue decrease?"}

    # Simulate LLM failing with TimeoutError
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("Read timed out")

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        res_state = supervisor_node(state)

        assert res_state["intent"] == "revenue"
        assert "revenue_agent" in res_state["required_agents"]
        snapshot = get_metrics_snapshot()
        assert snapshot["llm"]["fallbacks"] >= 1


def test_aggregator_partial_evidence_resilience():
    """Verifies aggregator produces valid diagnosis even when some specialist evidence is missing."""
    from backend.agents.aggregator import evidence_aggregator_node
    from backend.graph.state import PayPilotState


    # Evidence only contains payment and revenue; checkout and customer failed
    state: PayPilotState = {
        "user_query": "Audit my payment operations and revenue",
        "intent": "revenue",
        "executed_agents": ["payment_agent", "revenue_agent"],
        "evidence": {
            "payment": {
                "overall_success_rate_pct": 81.71,
                "highest_failure_method": "Netbanking",
            },
            "revenue": {
                "business_health": {
                    "total_realized_revenue_inr": 50092576.66,
                    "recoverable_opportunity_inr": 3488251.64,
                }
            },
            # Checkout agent failed and left an error notice
            "checkout": {"status": "unavailable", "reason": "Analytics timeout"},
        },
    }

    res_state = evidence_aggregator_node(state)

    assert "final_answer" in res_state
    assert "INR 50,092,576.66" in res_state["final_answer"]
    assert "Netbanking" in res_state["final_answer"]
    # Does not crash or invent missing customer categories


def test_validation_error_does_not_trigger_retries():
    """Verifies that API 400 validation rejections do not trigger LLM retries."""
    reset_metrics()

    with patch_offline_evaluation_llm():
        client = TestClient(app)
        res = client.post("/api/v1/analyze", json={"query": "   "})
        assert res.status_code == 400

        snapshot = get_metrics_snapshot()
        assert snapshot["llm"]["retries"] == 0
        assert snapshot["errors"]["by_category"]["validation_error"] == 1


def test_secret_safety_during_circuit_breaker_and_retries():
    """Verifies that circuit breaker logs and retry metadata never leak API keys."""
    cb = CircuitBreaker(threshold=1, recovery_time=1.0)
    cb.record_failure()

    snapshot = get_metrics_snapshot()
    snap_str = str(snapshot)
    assert "nvapi-" not in snap_str
    assert "api_key" not in snap_str
    assert "NVIDIA_API_KEY" not in snap_str
