"""PayPilot Production Reliability Audit Test Suite.

Verifies all 12 production failure scenarios:
1. Supervisor LLM timeout
2. Aggregator LLM timeout
3. Recovery LLM timeout
4. NVIDIA API unavailable
5. NVIDIA API returns an error
6. One specialist agent fails
7. Malformed/empty agent output
8. Aggregator receives incomplete evidence
9. Recovery receives incomplete evidence
10. Invalid user query
11. Dataset/analytics failure
12. Concurrent requests
"""

import concurrent.futures
from pathlib import Path
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.agents.aggregator import evidence_aggregator_node
from backend.agents.llm_factory import get_llm_info
from backend.agents.recovery_agent import recovery_agent_node
from backend.agents.supervisor import supervisor_node
from backend.graph.run import run_pipeline
from backend.graph.state import PayPilotState
from backend.observability.metrics import get_metrics_snapshot, reset_metrics
from backend.observability.tracing import get_trace_store
from backend.security.rate_limiter import rate_limiter
from backend.tools.analytics import clear_dataset_cache
from backend.utils.resilience import CircuitBreaker, nvidia_circuit_breaker


@pytest.fixture(autouse=True)
def clean_audit_environment(monkeypatch):
    """Resets circuit breakers, metrics, trace store, rate limiter, and cache for clean test isolation."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    reset_metrics()
    clear_dataset_cache()
    nvidia_circuit_breaker.reset()
    rate_limiter.reset()
    yield
    reset_metrics()
    clear_dataset_cache()
    nvidia_circuit_breaker.reset()
    rate_limiter.reset()


@pytest.fixture
def client():
    return TestClient(app)


# ============================================================================
# Scenario 1: Supervisor LLM timeout
# ============================================================================
def test_scenario_1_supervisor_llm_timeout():
    """Verify that a supervisor LLM timeout fails gracefully and falls back to deterministic heuristic routing."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("Supervisor LLM connection timed out")

    state: PayPilotState = {
        "user_query": "Why did my revenue decrease and what should I do?",
        "errors": [],
    }

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        result_state = supervisor_node(state)

        # Fails gracefully without throwing unhandled exception
        assert result_state is not None
        assert result_state["intent"] == "revenue"
        assert "revenue_agent" in result_state["required_agents"]
        assert "payment_agent" in result_state["required_agents"]

        # Observability / metrics captured timeout & fallback
        snapshot = get_metrics_snapshot()
        assert snapshot["llm"]["fallbacks"] >= 1
        assert snapshot["errors"]["by_category"]["timeout"] >= 1


# ============================================================================
# Scenario 2: Aggregator LLM timeout
# ============================================================================
def test_scenario_2_aggregator_llm_timeout():
    """Verify that an aggregator LLM timeout fails gracefully and produces valid deterministic synthesis."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("Aggregator LLM request timed out")

    state: PayPilotState = {
        "user_query": "Why did my revenue decrease?",
        "intent": "revenue",
        "executed_agents": ["payment_agent", "revenue_agent"],
        "evidence": {
            "payment": {
                "overall_success_rate_pct": 81.71,
                "overall_failure_rate_pct": 18.29,
                "gross_failed_value_inr": 12654909.17,
                "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
                "top_overall_failure_reasons": [
                    {"failure_reason": "BANK_SERVER_TIMEOUT", "count": 520, "lost_revenue_inr": 1850000.0}
                ],
            },
            "revenue": {
                "business_health": {
                    "total_realized_revenue_inr": 50092576.66,
                    "recoverable_opportunity_inr": 3488251.64,
                }
            },
        },
    }

    with patch("backend.agents.aggregator.get_llm", return_value=mock_llm):
        result_state = evidence_aggregator_node(state)

        assert "final_answer" in result_state
        assert result_state["final_answer"] is not None
        assert len(result_state["final_answer"]) > 100
        # Preserves deterministic numerical facts
        assert "50,092,576.66" in result_state["final_answer"]
        assert "81.71%" in result_state["final_answer"]
        assert len(result_state.get("recommendations", [])) > 0

        # Observability captured
        snapshot = get_metrics_snapshot()
        assert snapshot["llm"]["fallbacks"] >= 1
        assert snapshot["errors"]["by_category"]["timeout"] >= 1


# ============================================================================
# Scenario 3: Recovery LLM timeout
# ============================================================================
def test_scenario_3_recovery_llm_timeout():
    """Verify that a recovery LLM timeout produces a complete 5-section executive report with deterministic P1-P4 actions."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("Recovery briefing synthesis timed out")

    state: PayPilotState = {
        "user_query": "Why did my revenue decrease?",
        "intent": "revenue",
        "executed_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "evidence": {
            "payment": {
                "overall_success_rate_pct": 81.71,
                "gross_failed_value_inr": 12654909.17,
                "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
                "payment_methods": {"Netbanking": {"failed_amount": 2500000.0}},
                "top_overall_failure_reasons": [
                    {"failure_reason": "BANK_SERVER_TIMEOUT", "count": 520, "lost_revenue_inr": 1850000.0}
                ],
            },
            "revenue": {
                "business_health": {
                    "total_realized_revenue_inr": 50092576.66,
                    "recoverable_opportunity_inr": 3488251.64,
                },
                "what_if_simulation": {
                    "estimated_additional_revenue_inr": 1839235.50,
                    "additional_successful_transactions": 724,
                    "target_success_rate_uplift_pct": 3.0,
                },
            },
            "checkout": {
                "mobile_conversion_rate_pct": 80.78,
                "desktop_conversion_rate_pct": 85.11,
                "mobile_desktop_conversion_gap_pct": 4.33,
                "device_performance": {
                    "Mobile_Android": {"lost_failed_value": 1500000.0},
                    "Mobile_iOS": {"lost_failed_value": 1000000.0},
                },
            },
            "customer": {
                "overall_refund_rate_pct": 8.24,
                "highest_refund_category": {
                    "category": "Fashion",
                    "refund_rate_pct": 17.99,
                    "refunded_orders_count": 628,
                    "refunded_amount_inr": 1648780.21,
                },
            },
        },
        "analysis": {
            "key_facts": {
                "total_revenue_inr": 50092576.66,
                "payment_success_rate_pct": 81.71,
            }
        },
    }

    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):
        result_state = recovery_agent_node(state)

        # Prioritized actions exist and are deterministically ranked
        actions = result_state.get("prioritized_actions", [])
        assert len(actions) >= 4
        for idx, a in enumerate(actions, start=1):
            assert a["rank"] == idx
            assert a["priority_score"] > 0
            assert a["estimated_revenue_impact_inr"] > 0

        # Complete valid 5-section executive report generated
        final_answer = result_state.get("final_answer", "")
        assert "BUSINESS DIAGNOSIS" in final_answer
        assert "TOP REVENUE LEAKS" in final_answer
        assert "PRIORITIZED ACTIONS" in final_answer
        assert "EXPECTED UPSIDE" in final_answer
        assert "EXECUTIVE RECOMMENDATION" in final_answer
        assert "P1" in final_answer
        assert "P2" in final_answer
        assert "P3" in final_answer
        assert "P4" in final_answer


# ============================================================================
# Scenario 4: NVIDIA API unavailable
# ============================================================================
def test_scenario_4_nvidia_api_unavailable(client):
    """Verify that when NVIDIA API key is absent or circuit breaker is open, system operates seamlessly in deterministic fallback."""
    # Ensure no API key
    info = get_llm_info()
    assert info["active_provider"] == "deterministic_fallback"
    assert info["is_live_llm"] is False

    response = client.post(
        "/api/v1/analyze",
        json={"query": "Why did my revenue decrease?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["intent"] == "revenue"
    assert data["is_live_llm"] is False
    assert data["llm_provider"] == "deterministic_fallback"
    assert len(data["prioritized_actions"]) >= 4
    assert data["final_answer"] is not None
    assert "BUSINESS DIAGNOSIS" in data["final_answer"]


# ============================================================================
# Scenario 5: NVIDIA API returns an error
# ============================================================================
def test_scenario_5_nvidia_api_returns_error():
    """Verify that when NVIDIA API returns 500, 429, or other provider errors, the system handles it gracefully."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("503 Service Unavailable: NVIDIA inference server overloaded")

    state: PayPilotState = {
        "user_query": "Why is UPI payment failing?",
        "errors": [],
    }

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        result_state = supervisor_node(state)
        assert result_state["intent"] == "payment"
        assert "payment_agent" in result_state["required_agents"]

    snapshot = get_metrics_snapshot()
    assert snapshot["errors"]["by_category"]["provider_error"] >= 1


# ============================================================================
# Scenario 6: One specialist agent fails
# ============================================================================
def test_scenario_6_one_specialist_agent_fails():
    """Verify that if one specialist agent throws an exception, it does not crash the pipeline or corrupt state."""
    with patch("backend.agents.payment_agent.get_revenue_by_payment_method", side_effect=ZeroDivisionError("Division by zero in payment method analytics")):
        result = run_pipeline("Why did my revenue decrease?")

        # Pipeline completed without crashing
        assert result is not None
        assert "payment_agent" not in result.get("executed_agents", [])
        assert "revenue_agent" in result.get("executed_agents", [])
        assert "checkout_agent" in result.get("executed_agents", [])

        # Error captured in state["errors"]
        assert len(result.get("errors", [])) >= 1
        assert any("Payment Agent error" in err for err in result["errors"])

        # Recovery agent and aggregator still succeeded
        assert result.get("final_answer") is not None
        assert len(result.get("prioritized_actions", [])) >= 4


# ============================================================================
# Scenario 7: Malformed / empty agent output
# ============================================================================
def test_scenario_7_malformed_empty_agent_output():
    """Verify that supervisor and recovery reject malformed, empty, or thinking-contaminated LLM outputs."""
    # 1. Supervisor receives malformed text
    mock_llm_supervisor = MagicMock()
    mock_llm_supervisor.invoke.return_value = MagicMock(content="Hello! I cannot output JSON right now.")

    state: PayPilotState = {"user_query": "Why did my revenue decrease?"}
    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm_supervisor):
        res = supervisor_node(state)
        # Rejects non-JSON and falls back to deterministic rule
        assert res["intent"] == "revenue"
        assert len(res["required_agents"]) > 0

    # 2. Recovery receives contaminated thinking output without complete sections
    mock_llm_recovery = MagicMock()
    mock_llm_recovery.invoke.return_value = MagicMock(
        content="<think>Now let's compute the revenue</think> Here is some text but missing mandatory sections."
    )

    state_rec: PayPilotState = {
        "user_query": "Why did revenue drop?",
        "intent": "revenue",
        "evidence": {
            "payment": {"overall_success_rate_pct": 80.0, "gross_failed_value_inr": 100000.0},
        },
    }
    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm_recovery):
        res_rec = recovery_agent_node(state_rec)
        # Cleaned LLM rejected invalid text, fell back to complete deterministic report
        assert "BUSINESS DIAGNOSIS" in res_rec["final_answer"]
        assert "PRIORITIZED ACTIONS" in res_rec["final_answer"]


# ============================================================================
# Scenario 8: Aggregator receives incomplete evidence
# ============================================================================
def test_scenario_8_aggregator_receives_incomplete_evidence():
    """Verify aggregator gracefully handles partial evidence bundles without crashing."""
    # Evidence has only customer data
    state: PayPilotState = {
        "user_query": "Tell me about refunds",
        "intent": "customer",
        "executed_agents": ["customer_agent"],
        "evidence": {
            "customer": {
                "overall_refund_rate_pct": 12.5,
                "highest_refund_category": {"category": "Electronics", "refund_rate_pct": 19.0},
            }
        },
    }

    result = evidence_aggregator_node(state)
    assert result["final_answer"] is not None
    assert "12.5%" in result["final_answer"]
    assert "Electronics" in result["final_answer"]
    assert len(result["recommendations"]) > 0


# ============================================================================
# Scenario 9: Recovery receives incomplete evidence
# ============================================================================
def test_scenario_9_recovery_receives_incomplete_evidence():
    """Verify recovery agent backfills actions when specialist evidence is partial, guaranteeing P1-P4."""
    state: PayPilotState = {
        "user_query": "Audit checkout",
        "intent": "checkout",
        "executed_agents": ["checkout_agent"],
        "evidence": {
            "checkout": {
                "mobile_conversion_rate_pct": 75.0,
                "desktop_conversion_rate_pct": 85.0,
                "mobile_desktop_conversion_gap_pct": 10.0,
                "device_performance": {
                    "Mobile_Android": {"lost_failed_value": 500000.0},
                },
            }
        },
    }

    result = recovery_agent_node(state)
    actions = result.get("prioritized_actions", [])
    # Guaranteed backfill ensures 4 actions for comprehensive P1-P4 coverage
    assert len(actions) >= 4
    assert actions[0]["rank"] == 1
    assert "Mobile" in actions[0]["action"] or "UPI" in actions[0]["action"]


# ============================================================================
# Scenario 10: Invalid user query
# ============================================================================
def test_scenario_10_invalid_user_query(client):
    """Verify that invalid user queries (empty, whitespace, oversized, malformed types) return standardized error schemas."""
    # 1. Empty string -> 400 or 422 with standardized ErrorResponse
    res_empty = client.post("/api/v1/analyze", json={"query": ""})
    assert res_empty.status_code in (400, 422)
    assert res_empty.json()["error"] in ("HTTP_400", "VALIDATION_ERROR")
    assert "X-Request-ID" in res_empty.headers

    # 2. Whitespace -> 400
    res_ws = client.post("/api/v1/analyze", json={"query": "    \t\n  "})
    assert res_ws.status_code in (400, 422)
    assert "empty" in res_ws.json()["detail"].lower() or "whitespace" in res_ws.json()["detail"].lower()

    # 3. Oversized string (> 1000 characters) -> 400 or 422
    res_over = client.post("/api/v1/analyze", json={"query": "Why did revenue drop? " * 60})
    assert res_over.status_code in (400, 422)
    assert "1000" in res_over.json()["detail"] or "maximum" in res_over.json()["detail"].lower() or "length" in res_over.json()["detail"].lower()

    # 4. Invalid data type (integer query) -> 422
    res_type = client.post("/api/v1/analyze", json={"query": 12345})
    assert res_type.status_code == 422
    assert res_type.json()["error"] == "VALIDATION_ERROR"


# ============================================================================
# Scenario 11: Dataset / analytics failure
# ============================================================================
def test_scenario_11_dataset_analytics_failure(client):
    """Verify that when the dataset is missing or corrupted, readiness probe correctly reports 503."""
    with patch("backend.api.routes.DATA_PATH", Path("non_existent_dataset.csv")):
        res_ready = client.get("/ready")
        assert res_ready.status_code == 503
        data = res_ready.json()
        assert data["error"] == "HTTP_503"
        assert "Service unready" in data["detail"]


# ============================================================================
# Scenario 12: Concurrent requests
# ============================================================================
def test_scenario_12_concurrent_requests(client):
    """Verify thread-safety and correctness under concurrent request load."""
    def _make_request(idx: int):
        return client.post(
            "/api/v1/analyze",
            json={"query": f"Why did revenue drop? Query {idx}"},
            headers={"X-Request-ID": f"concurrent-req-{idx}"},
        )

    num_threads = 6
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_make_request, i) for i in range(num_threads)]
        results = [f.result() for f in futures]

    assert len(results) == num_threads
    for res in results:
        assert res.status_code == 200
        data = res.json()
        assert data["intent"] == "revenue"
        assert len(data["prioritized_actions"]) >= 4
        assert "BUSINESS DIAGNOSIS" in data["final_answer"]
        assert "X-Request-ID" in res.headers
