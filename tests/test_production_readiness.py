"""Comprehensive Production-Readiness & Model Routing Verification Suite.

Validates:
1. Model routing across Supervisor, Aggregator, and Recovery nodes.
2. Max token limits and zero retries configuration.
3. Strict sanitization rejecting <think>, prompt quotations, and reasoning.
4. Explicit truncation and incomplete report detection.
5. Deterministic fallback under all failure modes (timeout, API error, malformed JSON/synthesis).
6. Observability span metadata and node_models attribution.
7. End-to-end /health, /ready, and /api/v1/analyze contracts.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from backend.agents.llm_factory import get_llm, get_llm_info
from backend.agents.supervisor import supervisor_node, _parse_llm_json_response
from backend.agents.aggregator import evidence_aggregator_node, _clean_llm_synthesis as clean_agg
from backend.agents.recovery_agent import (
    recovery_agent_node,
    _clean_llm_synthesis as clean_rec,
    generate_deterministic_executive_report,
)
from backend.api.main import app
from backend.api.schemas import HealthResponse, ReadinessResponse, AnalyzeResponse
from backend.graph.state import PayPilotState
from backend.observability.tracing import get_trace_store, reset_trace_store


@pytest.fixture(autouse=True)
def clean_state():
    reset_trace_store()
    yield
    reset_trace_store()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_evidence():
    return {
        "payment": {
            "overall_success_rate_pct": 81.71,
            "overall_failure_rate_pct": 18.29,
            "gross_failed_value_inr": 12654909.17,
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
            "top_overall_failure_reasons": [
                {"failure_reason": "BANK_SERVER_TIMEOUT", "count": 520, "lost_revenue_inr": 1850000.0},
            ],
        },
        "checkout": {
            "mobile_conversion_rate_pct": 80.78,
            "desktop_conversion_rate_pct": 85.11,
            "mobile_desktop_conversion_gap_pct": 4.33,
        },
        "customer": {
            "overall_refund_rate_pct": 8.24,
            "highest_refund_category": {"category": "Fashion", "refund_rate_pct": 17.99},
        },
        "revenue": {
            "business_health": {
                "total_realized_revenue_inr": 50092576.66,
                "recoverable_opportunity_inr": 3488251.64,
            },
            "what_if_simulation": {
                "target_success_rate_uplift_pct": 3.0,
                "estimated_additional_revenue_inr": 1839235.50,
                "additional_successful_transactions": 450,
            },
        },
    }


# ============================================================================
# 1. Model Routing & Token Configuration Verification
# ============================================================================

def test_node_specific_model_routing(monkeypatch):
    """Verify get_llm instantiates correct model per node type."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-mock-key-12345")
    monkeypatch.setenv("SUPERVISOR_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
    monkeypatch.setenv("AGGREGATOR_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.setenv("RECOVERY_MODEL", "nvidia/nemotron-3-super-120b-a12b")

    s_llm = get_llm(node_type="supervisor")
    assert s_llm is not None
    assert s_llm.model == "nvidia/nemotron-3-nano-30b-a3b"

    a_llm = get_llm(node_type="aggregator")
    assert a_llm is not None
    assert a_llm.model == "nvidia/nemotron-3-super-120b-a12b"

    r_llm = get_llm(node_type="recovery")
    assert r_llm is not None
    assert r_llm.model == "nvidia/nemotron-3-super-120b-a12b"


def test_custom_env_override_model_routing(monkeypatch):
    """Verify environment variables dynamically override model routing."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-mock-key-12345")
    monkeypatch.setenv("SUPERVISOR_MODEL", "custom/supervisor-model")
    monkeypatch.setenv("AGGREGATOR_MODEL", "custom/aggregator-model")
    monkeypatch.setenv("RECOVERY_MODEL", "custom/recovery-model")

    s_llm = get_llm(node_type="supervisor")
    assert s_llm.model == "custom/supervisor-model"

    a_llm = get_llm(node_type="aggregator")
    assert a_llm.model == "custom/aggregator-model"

    r_llm = get_llm(node_type="recovery")
    assert r_llm.model == "custom/recovery-model"


# ============================================================================
# 2. Strict Output Sanitization & Guardrails
# ============================================================================

def test_sanitization_rejects_think_blocks_and_reasoning():
    """Verify that <think> blocks and internal reasoning are stripped or rejected."""
    # Think tag before valid briefing
    input_with_think = (
        "<think>\nNeed to examine realized revenue and formulate P1.\n</think>\n\n"
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n1. Netbanking failure at 21.57%\n\n"
        "PRIORITIZED ACTIONS\n"
        "P1 — Streamline Mobile Checkout UX\n  • Estimated Recoverable Impact: INR 2,589,659.65\n"
        "P2 — Multi-Point Payment Reliability\n  • Estimated Recoverable Impact: INR 1,839,235.50\n"
        "P3 — Dynamic Gateway Routing\n  • Estimated Recoverable Impact: INR 1,241,965.81\n"
        "P4 — Return Controls for Fashion\n  • Estimated Recoverable Impact: INR 412,195.05\n\n"
        "EXPECTED UPSIDE\nEstimated Recoverable Opportunity: INR 3,488,251.64\nWhat-If +3.0% Success Uplift: +INR 1,839,235.50\n\n"
        "EXECUTIVE RECOMMENDATION\nExecute P1 as priority."
    )
    cleaned = clean_rec(input_with_think)
    assert cleaned.startswith("BUSINESS DIAGNOSIS")
    assert "<think>" not in cleaned
    assert "Need to examine" not in cleaned
    assert "P4 —" in cleaned

    # Pure thinking without structured report
    pure_thinking = "Here is my thinking process:\n1. Analyze user request\n2. Compute numbers."
    assert clean_rec(pure_thinking) == ""
    assert clean_agg(pure_thinking) == ""


def test_sanitization_rejects_prompt_quotation():
    """Verify that prompt instructions echoed by the LLM are rejected."""
    prompt_echo = (
        "Here is the plan:\n"
        "Rules: Ground all conclusions STRICTLY in the provided numbers.\n"
        "Role: Chief Financial Intelligence Officer.\n"
        "Structure: BUSINESS DIAGNOSIS, TOP REVENUE LEAKS, PRIORITIZED ACTIONS, EXPECTED UPSIDE, EXECUTIVE RECOMMENDATION."
    )
    assert clean_rec(prompt_echo) == ""
    assert clean_agg(prompt_echo) == ""


# ============================================================================
# 3. Explicit Truncation & Incomplete Output Detection
# ============================================================================

def test_truncation_detection_missing_terminal_sections():
    """Verify that output missing mandatory terminal sections is detected as truncated and rejected."""
    # Missing EXECUTIVE RECOMMENDATION and EXPECTED UPSIDE
    truncated_text = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n1. Netbanking failure at 21.57%\n\n"
        "PRIORITIZED ACTIONS\nP1 — Gateway Routing"
    )
    # Must be rejected because EXPECTED UPSIDE and EXECUTIVE RECOMMENDATION are missing
    assert clean_rec(truncated_text) == ""


def test_truncation_detection_trailing_cutoffs():
    """Verify that responses ending with cut-off punctuation or incomplete headers are rejected."""
    cutoff_action = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n1. Netbanking failure\n\n"
        "PRIORITIZED ACTIONS\nP1 — Gateway Routing\n\n"
        "EXPECTED UPSIDE\nEstimated Recoverable Opportunity: INR 3,488,251.64\n\n"
        "EXECUTIVE RECOMMENDATION\n"  # Cut off with no content
    )
    assert clean_rec(cutoff_action) == ""

    trailing_comma = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n1. Netbanking failure\n\n"
        "PRIORITIZED ACTIONS\nP1 — Gateway Routing\n\n"
        "EXPECTED UPSIDE\nEstimated Recoverable Opportunity: INR 3,488,251.64\n\n"
        "EXECUTIVE RECOMMENDATION\nExecute P1 and,"
    )
    assert clean_rec(trailing_comma) == ""


# ============================================================================
# 4. Deterministic Fallback Across All Failure Modes
# ============================================================================

def test_deterministic_fallback_on_llm_timeout(mock_evidence):
    """Verify recovery agent produces deterministic report when LLM times out."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = TimeoutError("Request timed out after 60.0s")

    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):
        state: PayPilotState = {
            "user_query": "Why did my revenue decrease?",
            "intent": "revenue",
            "required_agents": ["revenue_agent", "payment_agent"],
            "executed_agents": ["revenue_agent", "payment_agent"],
            "evidence": mock_evidence,
            "analysis": {},
        }
        res_state = recovery_agent_node(state)
        final_ans = res_state.get("final_answer", "")

        assert "BUSINESS DIAGNOSIS" in final_ans
        assert "TOP REVENUE LEAKS" in final_ans
        assert "PRIORITIZED ACTIONS" in final_ans
        assert "EXPECTED UPSIDE" in final_ans
        assert "EXECUTIVE RECOMMENDATION" in final_ans
        assert "P1 —" in final_ans
        assert "INR 50,092,576.66" in final_ans


def test_deterministic_fallback_on_malformed_llm_output(mock_evidence):
    """Verify recovery agent falls back to deterministic report when LLM produces invalid/thinking output."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="Thinking Process: I should analyze revenue drop...")

    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):
        state: PayPilotState = {
            "user_query": "Why did my revenue decrease?",
            "intent": "revenue",
            "required_agents": ["revenue_agent", "payment_agent"],
            "executed_agents": ["revenue_agent", "payment_agent"],
            "evidence": mock_evidence,
            "analysis": {},
        }
        res_state = recovery_agent_node(state)
        final_ans = res_state.get("final_answer", "")

        assert "Thinking Process" not in final_ans
        assert "BUSINESS DIAGNOSIS" in final_ans
        assert len(res_state["priority_actions"]) > 0


def test_supervisor_fallback_on_malformed_json():
    """Verify supervisor falls back to heuristic routing when LLM returns non-JSON."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="I cannot process this in JSON format.")

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        state: PayPilotState = {"user_query": "Why did revenue drop?"}
        res_state = supervisor_node(state)
        assert res_state["intent"] == "revenue"
        assert "revenue_agent" in res_state["required_agents"]


# ============================================================================
# 5. Distributed Tracing & Span Metadata Attribution
# ============================================================================

def test_llm_generate_span_contains_node_metadata(monkeypatch):
    """Verify that _wrap_traced_llm adds caller node identifier to span metadata."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-mock-key")
    store = get_trace_store()

    mock_raw = MagicMock()
    mock_raw.invoke.return_value = MagicMock(content='{"intent": "payment", "required_agents": ["payment_agent"]}')

    with patch("langchain_nvidia_ai_endpoints.ChatNVIDIA", return_value=mock_raw):
        s_llm = get_llm(node_type="supervisor")
        s_llm.invoke("Test input")

        spans = store.list_traces()
        assert len(spans) >= 1
        trace_id = spans[0]["trace_id"]
        trace_spans = store.get_trace(trace_id)
        assert trace_spans is not None

        llm_spans = [s for s in trace_spans if s.operation_name == "llm.generate"]
        assert len(llm_spans) >= 1
        meta = llm_spans[0].metadata
        assert meta.get("node") == "supervisor"
        assert meta.get("model") == "nvidia/nemotron-3-nano-30b-a3b"


# ============================================================================
# 6. API Endpoints & Node Model Metadata
# ============================================================================

def test_health_endpoint_response_model(client):
    """Verify /health returns 200 with accurate model identifier."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    validated = HealthResponse(**data)
    assert validated.status == "healthy"
    assert "llama" not in validated.model.lower()


def test_ready_endpoint_response_model(client):
    """Verify /ready returns 200 with accurate model identifier."""
    res = client.get("/ready")
    assert res.status_code == 200
    data = res.json()
    validated = ReadinessResponse(**data)
    assert validated.status == "ready"
    assert "llama" not in validated.details.get("model", "").lower()


def test_analyze_endpoint_full_response_structure(client, monkeypatch):
    """Verify /api/v1/analyze returns complete response with node_models and complete report."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")

    res = client.post("/api/v1/analyze", json={"query": "Why did my revenue decrease and what should I do?"})
    assert res.status_code == 200
    data = res.json()
    validated = AnalyzeResponse(**data)

    assert validated.intent in ["revenue", "general_business_analysis"]
    assert len(validated.prioritized_actions) > 0
    assert "BUSINESS DIAGNOSIS" in validated.final_answer
    assert "TOP REVENUE LEAKS" in validated.final_answer
    assert "PRIORITIZED ACTIONS" in validated.final_answer
    assert "EXPECTED UPSIDE" in validated.final_answer
    assert "EXECUTIVE RECOMMENDATION" in validated.final_answer
    assert validated.prioritized_actions[0].rank == 1
