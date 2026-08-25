"""Unit tests for the NVIDIA LLM Factory, Routing, and Synthesis."""

import pytest
from unittest.mock import MagicMock, patch

from backend.agents.llm_factory import get_llm, get_llm_info, is_valid_key
from backend.agents.supervisor import supervisor_node
from backend.agents.aggregator import evidence_aggregator_node
from backend.graph.state import PayPilotState, SupervisorDecision


def test_is_valid_key():
    """Verify validation of API key strings and placeholder detection."""
    assert is_valid_key("nvapi-1234567890abcdef") is True
    assert is_valid_key("sk-validkey12345") is True
    
    assert is_valid_key("") is False
    assert is_valid_key(None) is False
    assert is_valid_key("   ") is False
    assert is_valid_key("your_nvidia_api_key_here") is False
    assert is_valid_key("<insert-key-here>") is False


def test_deterministic_fallback_when_keys_missing(monkeypatch):
    """Verify that when no NVIDIA key is configured, get_llm returns None gracefully."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    llm = get_llm()
    assert llm is None

    info = get_llm_info()
    assert info["is_llm_active"] is False
    assert info["is_live_llm"] is False
    assert info["active_provider"] == "deterministic_fallback"
    assert info["model"] == "none"


def test_nvidia_provider_initialization(monkeypatch):
    """Verify ChatNVIDIA / ChatOpenAI initialization when NVIDIA_API_KEY is configured."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-mock-test-key-12345")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    llm = get_llm()
    assert llm is not None
    cls_name = llm.__class__.__name__
    assert cls_name in ["ChatNVIDIA", "ChatOpenAI"]

    info = get_llm_info()
    assert info["is_llm_active"] is True
    assert info["is_live_llm"] is True
    assert info["active_provider"] == "nvidia"


def test_invalid_provider_name():
    """Verify that invalid provider names degrade gracefully to None."""
    llm = get_llm(provider="nonexistent_provider_xyz")
    assert llm is None


def test_get_llm_info_never_exposes_secret_key(monkeypatch):
    """Verify that get_llm_info never leaks the secret API key."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret-key-do-not-leak")
    info = get_llm_info()
    for v in info.values():
        assert "nvapi-secret-key" not in str(v)


def test_supervisor_with_mocked_llm():
    """Verify that supervisor node uses structured output from NVIDIA LLM when available."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = SupervisorDecision(
        intent="payment",
        required_agents=["payment_agent"],
        reasoning="Mocked NVIDIA LLM routing decision",
    )
    mock_llm.with_structured_output.return_value = mock_structured

    with patch("backend.agents.supervisor.get_llm", return_value=mock_llm):
        state: PayPilotState = {
            "user_query": "Why are UPI transactions failing?",
            "intent": "",
            "required_agents": [],
            "executed_agents": [],
            "tool_results": {},
            "evidence": {},
            "analysis": {},
            "recommendations": [],
            "final_answer": None,
            "errors": [],
        }

        out_state = supervisor_node(state)
        assert out_state["intent"] == "payment"
        assert out_state["required_agents"] == ["payment_agent"]


def test_evidence_aggregator_with_mocked_llm():
    """Verify that evidence aggregator node uses NVIDIA LLM synthesis when available."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Executive Synthesis: Revenue decreased due to UPI timeouts on mobile devices."
    mock_llm.invoke.return_value = mock_response

    with patch("backend.agents.aggregator.get_llm", return_value=mock_llm):
        state: PayPilotState = {
            "user_query": "Why did revenue drop?",
            "intent": "revenue",
            "required_agents": ["revenue_agent", "payment_agent"],
            "executed_agents": ["revenue_agent", "payment_agent"],
            "tool_results": {},
            "evidence": {
                "payment": {"overall_success_rate_pct": 74.2, "gross_failed_value_inr": 500000.0},
                "revenue": {"business_health": {"total_realized_revenue_inr": 2000000.0}},
            },
            "analysis": {},
            "recommendations": [],
            "final_answer": None,
            "errors": [],
        }

        out_state = evidence_aggregator_node(state)
        assert out_state["final_answer"] == "Executive Synthesis: Revenue decreased due to UPI timeouts on mobile devices."
        assert "revenue" in out_state["analysis"]["evidence_sections"]
