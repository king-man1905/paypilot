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


def test_supervisor_retries_transient_failure_before_falling_back():
    """Regression test for LLM_MAX_RETRIES threading: a supervisor LLM call that fails once
    with a transient error, then succeeds, must use the LLM's routing decision (not the
    deterministic fallback), proving max_retries=0 is no longer hardcoded at the call site.
    """
    from backend.agents.supervisor import supervisor_node
    from backend.graph.state import PayPilotState

    state: PayPilotState = {"user_query": "Why is UPI failing?"}

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [
        TimeoutError("Read timed out"),
        MagicMock(content='{"intent": "payment", "required_agents": ["payment_agent"], "reasoning": "retry succeeded"}'),
    ]

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        res_state = supervisor_node(state)

        assert mock_llm.invoke.call_count == 2
        assert res_state["intent"] == "payment"
        assert res_state["required_agents"] == ["payment_agent"]


def test_aggregator_retries_transient_failure_before_falling_back():
    """Regression test: aggregator LLM synthesis retries a transient failure once before
    falling back to the deterministic template, using LLM_MAX_RETRIES instead of 0."""
    from backend.agents.aggregator import evidence_aggregator_node
    from backend.graph.state import PayPilotState

    expected_synthesis = (
        "Executive Summary:\n"
        "Total Revenue: INR 2,000,000.00. Payment Success Rate dropped to 74.2%.\n\n"
        "Root-Cause Breakdown:\n"
        "UPI timeouts on mobile devices caused INR 500,000.00 in failed transactions.\n\n"
        "Prioritized Action Plan:\n"
        "1. Deploy dynamic retry logic for UPI timeouts.\n"
        "2. Optimize mobile checkout form flow."
    )
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [
        TimeoutError("Read timed out"),
        MagicMock(content=expected_synthesis),
    ]

    state: PayPilotState = {
        "user_query": "Why did revenue drop?",
        "intent": "revenue",
        "required_agents": ["revenue_agent", "payment_agent"],
        "executed_agents": ["revenue_agent", "payment_agent"],
        "evidence": {
            "payment": {"overall_success_rate_pct": 74.2, "gross_failed_value_inr": 500000.0},
            "revenue": {"business_health": {"total_realized_revenue_inr": 2000000.0}},
        },
        "analysis": {},
        "recommendations": [],
        "final_answer": None,
        "errors": [],
    }

    with patch("backend.agents.aggregator.get_llm", return_value=mock_llm):
        out_state = evidence_aggregator_node(state)

        assert mock_llm.invoke.call_count == 2
        assert out_state["final_answer"] == expected_synthesis


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


def test_recovery_agent_retries_transient_failure_before_falling_back():
    """Regression test: recovery agent executive-briefing LLM call retries a transient
    failure once before falling back, using LLM_MAX_RETRIES instead of a hardcoded 0."""
    from backend.agents.recovery_agent import recovery_agent_node
    from backend.graph.state import PayPilotState
    from backend.tools.analytics import get_business_health_summary, get_revenue_lost_by_failure, get_what_if_success_rate

    valid_report = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n"
        "1. Payment Method Friction: Netbanking at 21.57% failure rate.\n\n"
        "PRIORITIZED ACTIONS\n"
        "P1 — Streamline Mobile Checkout UX\n"
        "  • Estimated Recoverable Impact: INR 2,589,659.65\n"
        "  • Observed Gross Loss: INR 10,358,638.58\n"
        "  • Confidence: 90%\n"
        "  • Effort / Urgency: Medium Effort | High Urgency (Priority Score: 92.5/100)\n"
        "  • Rationale: Mobile checkout friction depresses conversion.\n\n"
        "P2 — Payment Reliability Program\n"
        "  • Estimated Recoverable Impact: INR 1,839,235.50\n"
        "  • Observed Gross Loss: INR 3,488,251.64\n"
        "  • Confidence: 92%\n"
        "  • Effort / Urgency: Medium Effort | High Urgency (Priority Score: 81.41/100)\n"
        "  • Rationale: Target +3% payment success uplift.\n\n"
        "P3 — Dynamic Gateway Routing\n"
        "  • Estimated Recoverable Impact: INR 1,241,965.81\n"
        "  • Observed Gross Loss: INR 3,104,914.53\n"
        "  • Confidence: 95%\n"
        "  • Effort / Urgency: Low Effort | High Urgency (Priority Score: 77.93/100)\n"
        "  • Rationale: Instant fallback for timeouts.\n\n"
        "P4 — Return Controls for Fashion\n"
        "  • Estimated Recoverable Impact: INR 412,195.05\n"
        "  • Observed Gross Loss: INR 1,648,780.20\n"
        "  • Confidence: 85%\n"
        "  • Effort / Urgency: Medium Effort | Medium Urgency (Priority Score: 65.2/100)\n"
        "  • Rationale: Size ambiguity reduction.\n\n"
        "EXPECTED UPSIDE\n"
        "Estimated Recoverable Opportunity : INR 3,488,251.64\n"
        "What-If +3.0% Success Uplift     : +INR 1,839,235.50\n\n"
        "EXECUTIVE RECOMMENDATION\n"
        "Execute P1 as the primary operational priority to recover INR 2,589,659.65. Follow with P2."
    )
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [
        TimeoutError("Read timed out"),
        MagicMock(content=valid_report),
    ]

    evidence = {
        "payment": {
            "overall_success_rate_pct": 81.71,
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
            "payment_methods": {"Netbanking": {"lost_failed_value": 2500000.0}},
            "top_overall_failure_reasons": [],
            "top_upi_failure_reasons": [],
        },
        "revenue": {
            "business_health": get_business_health_summary(),
            "what_if_simulation": get_what_if_success_rate(target_success_rate=3.0),
        },
    }
    state: PayPilotState = {
        "user_query": "What should I prioritize?",
        "intent": "revenue",
        "required_agents": ["revenue_agent", "payment_agent"],
        "executed_agents": ["revenue_agent", "payment_agent"],
        "evidence": evidence,
        "analysis": {},
        "recommendations": [],
        "final_answer": None,
        "errors": [],
    }

    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):
        out_state = recovery_agent_node(state)

        assert mock_llm.invoke.call_count == 2
        assert out_state["final_answer"] == valid_report


def test_secret_safety_during_circuit_breaker_and_retries():
    """Verifies that circuit breaker logs and retry metadata never leak API keys."""
    cb = CircuitBreaker(threshold=1, recovery_time=1.0)
    cb.record_failure()

    snapshot = get_metrics_snapshot()
    snap_str = str(snapshot)
    assert "nvapi-" not in snap_str
    assert "api_key" not in snap_str
    assert "NVIDIA_API_KEY" not in snap_str
