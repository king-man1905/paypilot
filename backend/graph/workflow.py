"""LangGraph Workflow Definition for PayPilot.

Orchestrates the complete multi-agent pipeline:
START -> Supervisor -> Specialist Agents -> Evidence Aggregator -> Recovery Agent -> END
"""

import logging
from langgraph.graph import StateGraph, START, END

from backend.graph.state import PayPilotState
from backend.agents.supervisor import supervisor_node
from backend.agents.revenue_agent import revenue_agent_node
from backend.agents.payment_agent import payment_agent_node
from backend.agents.checkout_agent import checkout_agent_node
from backend.agents.customer_agent import customer_agent_node
from backend.agents.aggregator import evidence_aggregator_node
from backend.agents.recovery_agent import recovery_agent_node

logger = logging.getLogger(__name__)


def build_paypilot_graph() -> StateGraph:
    """Constructs and compiles the complete PayPilot multi-agent StateGraph workflow."""
    builder = StateGraph(PayPilotState)

    # Register Nodes
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("revenue_agent", revenue_agent_node)
    builder.add_node("payment_agent", payment_agent_node)
    builder.add_node("checkout_agent", checkout_agent_node)
    builder.add_node("customer_agent", customer_agent_node)
    builder.add_node("evidence_aggregator", evidence_aggregator_node)
    builder.add_node("recovery_agent", recovery_agent_node)

    # Connect Edges:
    # START -> supervisor -> revenue_agent -> payment_agent -> checkout_agent -> customer_agent -> evidence_aggregator -> recovery_agent -> END
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "revenue_agent")
    builder.add_edge("revenue_agent", "payment_agent")
    builder.add_edge("payment_agent", "checkout_agent")
    builder.add_edge("checkout_agent", "customer_agent")
    builder.add_edge("customer_agent", "evidence_aggregator")
    builder.add_edge("evidence_aggregator", "recovery_agent")
    builder.add_edge("recovery_agent", END)

    compiled_graph = builder.compile()
    return compiled_graph


# Singleton compiled graph instance
paypilot_graph = build_paypilot_graph()
