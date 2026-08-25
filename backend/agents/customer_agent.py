"""Customer Specialist Agent.

Investigates customer cohorts (NEW, RETURNING, VIP), repeat purchase friction,
product categories, and refund anomalies using deterministic analytics tools.
"""

import logging
import time
from backend.graph.state import PayPilotState
from backend.tools.analytics import (
    get_conversion_by_customer_type,
    get_category_performance,
    get_refund_rate,
)
from backend.observability.metrics import record_agent_execution, record_error
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


@trace_span("agent.customer", component="customer_agent")
def customer_agent_node(state: PayPilotState) -> PayPilotState:
    """Customer agent execution node.

    Calls deterministic customer and category analytics tools to compile evidence.
    """
    if "customer_agent" not in state.get("required_agents", []):
        return state

    t_start = time.perf_counter()
    try:
        customer_perf = get_conversion_by_customer_type()
        category_perf = get_category_performance()
        overall_refund_rate = get_refund_rate()

        # Identify highest refund category
        highest_refund_cat = max(
            category_perf.items(),
            key=lambda x: x[1]["refund_rate_pct"],
        ) if category_perf else ("None", {"refund_rate_pct": 0.0, "refunded_amount": 0.0})

        # Identify highest grossing category
        top_revenue_cat = max(
            category_perf.items(),
            key=lambda x: x[1]["gross_revenue"],
        ) if category_perf else ("None", {"gross_revenue": 0.0})

        evidence_payload = {
            "customer_cohorts": customer_perf,
            "overall_refund_rate_pct": overall_refund_rate,
            "category_performance": category_perf,
            "highest_refund_category": {
                "category": highest_refund_cat[0],
                "refund_rate_pct": highest_refund_cat[1].get("refund_rate_pct", 0.0),
                "refunded_orders_count": highest_refund_cat[1].get("refunded_orders_count", 0),
                "refunded_amount_inr": highest_refund_cat[1].get("refunded_amount", 0.0),
            },
            "top_revenue_category": {
                "category": top_revenue_cat[0],
                "gross_revenue_inr": top_revenue_cat[1].get("gross_revenue", 0.0),
            },
        }

        # Store in state
        if "evidence" not in state or state["evidence"] is None:
            state["evidence"] = {}
        if "tool_results" not in state or state["tool_results"] is None:
            state["tool_results"] = {}
        if "executed_agents" not in state or state["executed_agents"] is None:
            state["executed_agents"] = []

        state["evidence"]["customer"] = evidence_payload
        state["tool_results"]["customer_analysis"] = evidence_payload
        state["executed_agents"].append("customer_agent")

        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("customer_agent", dur_ms, success=True)
        logger.info("Customer agent successfully executed deterministic analytics.")
    except Exception as e:
        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("customer_agent", dur_ms, success=False)
        record_error("analytics_error")
        logger.error(f"Error in customer_agent_node: {e}")
        state["errors"] = state.get("errors", []) + [f"Customer Agent error: {str(e)}"]

    return state
