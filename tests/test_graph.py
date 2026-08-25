"""Unit tests for LangGraph Agentic Core, Supervisor routing, and Evidence Aggregation."""

import pytest
from backend.graph.state import PayPilotState
from backend.agents.supervisor import supervisor_node, _rule_based_routing
from backend.agents.payment_agent import payment_agent_node
from backend.agents.checkout_agent import checkout_agent_node
from backend.agents.customer_agent import customer_agent_node
from backend.agents.revenue_agent import revenue_agent_node
from backend.agents.aggregator import evidence_aggregator_node
from backend.graph.workflow import paypilot_graph
from backend.tools.tool_registry import (
    tool_get_business_health_summary,
    tool_get_payment_method_analysis,
    tool_get_failure_reasons,
    tool_get_conversion_by_device,
    tool_get_customer_analysis,
    tool_get_category_performance,
    tool_get_top_revenue_leaks,
    tool_get_what_if_success_rate,
)


def test_supervisor_routing_categories():
    """Verify that supervisor correctly classifies intents and routes agents."""
    # Holistic revenue query
    dec_rev = _rule_based_routing("Why did my revenue decrease and what should I do?")
    assert dec_rev.intent == "revenue"
    assert set(dec_rev.required_agents) == {"revenue_agent", "payment_agent", "checkout_agent", "customer_agent"}

    # Payment specific query
    dec_pay = _rule_based_routing("Which payment method has the highest failure rate?")
    assert dec_pay.intent == "payment"
    assert dec_pay.required_agents == ["payment_agent"]

    # Checkout / Device query
    dec_chk = _rule_based_routing("Why are mobile users converting less on Android?")
    assert dec_chk.intent == "checkout"
    assert dec_chk.required_agents == ["checkout_agent"]

    # Customer / Category query
    dec_cust = _rule_based_routing("Which product category has the highest refunds?")
    assert dec_cust.intent == "customer"
    assert dec_cust.required_agents == ["customer_agent"]

    # What-if simulation query
    dec_sim = _rule_based_routing("What if payment success rate improves by 3%?")
    assert dec_sim.intent == "what_if"
    assert "revenue_agent" in dec_sim.required_agents


def test_tool_registry_execution():
    """Verify that all wrapped LangChain tools execute deterministically without errors."""
    health = tool_get_business_health_summary.invoke({})
    assert "total_realized_revenue_inr" in health
    assert health["total_transaction_attempts"] == 15000

    methods = tool_get_payment_method_analysis.invoke({})
    assert "UPI" in methods

    reasons = tool_get_failure_reasons.invoke({"payment_method": "UPI"})
    assert len(reasons) > 0

    devices = tool_get_conversion_by_device.invoke({})
    assert "Mobile_Android" in devices

    customers = tool_get_customer_analysis.invoke({})
    assert "NEW" in customers

    categories = tool_get_category_performance.invoke({})
    assert "Fashion" in categories

    leaks = tool_get_top_revenue_leaks.invoke({"limit": 3})
    assert len(leaks) == 3

    what_if = tool_get_what_if_success_rate.invoke({"target_success_rate": 3.0})
    assert what_if["additional_successful_transactions"] == 450


def test_specialist_agents_direct_nodes():
    """Test direct execution of specialist agent nodes."""
    base_state: PayPilotState = {
        "user_query": "Test query",
        "intent": "general",
        "required_agents": ["payment_agent", "checkout_agent", "customer_agent", "revenue_agent"],
        "executed_agents": [],
        "tool_results": {},
        "evidence": {},
        "analysis": {},
        "recommendations": [],
        "final_answer": None,
        "errors": [],
    }

    state = payment_agent_node(base_state)
    assert "payment_agent" in state["executed_agents"]
    assert "payment" in state["evidence"]
    assert "overall_success_rate_pct" in state["evidence"]["payment"]

    state = checkout_agent_node(state)
    assert "checkout_agent" in state["executed_agents"]
    assert "checkout" in state["evidence"]

    state = customer_agent_node(state)
    assert "customer_agent" in state["executed_agents"]
    assert "customer" in state["evidence"]

    state = revenue_agent_node(state)
    assert "revenue_agent" in state["executed_agents"]
    assert "revenue" in state["evidence"]

    state = evidence_aggregator_node(state)
    assert "key_facts" in state["analysis"]
    assert "total_revenue_inr" in state["analysis"]["key_facts"]


def test_full_graph_holistic_query():
    """Verify that full LangGraph executes end-to-end for a holistic query."""
    input_state: PayPilotState = {
        "user_query": "Why did my revenue decrease and where is my biggest leakage?",
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

    result = paypilot_graph.invoke(input_state)

    assert result["intent"] == "revenue"
    assert {"revenue_agent", "payment_agent", "checkout_agent", "customer_agent"}.issubset(set(result["executed_agents"]))
    assert "recovery_agent" in result["executed_agents"]
    assert "payment" in result["evidence"]
    assert "checkout" in result["evidence"]
    assert "customer" in result["evidence"]
    assert "revenue" in result["evidence"]
    assert result["analysis"]["key_facts"]["payment_success_rate_pct"] > 70.0
    assert result.get("final_answer") is not None
    assert len(result.get("priority_actions", [])) > 0


def test_full_graph_selective_routing():
    """Verify that a specific query only executes the required specialist agent."""
    input_state: PayPilotState = {
        "user_query": "Which payment method has the highest failure rate?",
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

    result = paypilot_graph.invoke(input_state)

    assert result["intent"] == "payment"
    assert "payment_agent" in result["executed_agents"]
    assert "checkout_agent" not in result["executed_agents"]
    assert "customer_agent" not in result["executed_agents"]
    assert "recovery_agent" in result["executed_agents"]
    assert "payment" in result["evidence"]
    assert "checkout" not in result["evidence"]
    assert "customer" not in result["evidence"]
    assert result.get("final_answer") is not None


def test_empty_query_handling():
    """Verify that an empty query is safely intercepted with an error."""
    input_state: PayPilotState = {
        "user_query": "",
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

    result = paypilot_graph.invoke(input_state)
    assert len(result["errors"]) > 0
    assert result["executed_agents"] == []
