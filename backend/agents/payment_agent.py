"""Payment Specialist Agent.

Investigates payment methods, success and failure rates, failure reasons, and lost payment values
using deterministic analytics tools.
"""

import logging
import time
from backend.graph.state import PayPilotState
from backend.tools.analytics import (
    get_payment_success_rate,
    get_payment_failure_rate,
    get_revenue_by_payment_method,
    get_failure_rate_by_payment_method,
    get_failure_reasons,
    get_failed_payment_value,
    get_revenue_lost_by_failure,
)
from backend.observability.metrics import record_agent_execution, record_error
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


@trace_span("agent.payment", component="payment_agent")
def payment_agent_node(state: PayPilotState) -> PayPilotState:
    """Payment agent execution node.

    Calls deterministic payment analytics tools and compiles factual evidence.
    """
    if "payment_agent" not in state.get("required_agents", []):
        return state

    t_start = time.perf_counter()
    try:
        # Invoke deterministic payment tools
        overall_success_rate = get_payment_success_rate()
        overall_failure_rate = get_payment_failure_rate()
        method_perf = get_revenue_by_payment_method()
        method_failure_rates = get_failure_rate_by_payment_method()
        top_failure_reasons = get_failure_reasons(limit=5)
        upi_failure_reasons = get_failure_reasons(payment_method="UPI", limit=3)
        failed_val = get_failed_payment_value()
        loss_breakdown = get_revenue_lost_by_failure()

        # Identify highest failure payment method
        worst_method = max(method_failure_rates.items(), key=lambda x: x[1]) if method_failure_rates else ("None", 0.0)

        evidence_payload = {
            "overall_success_rate_pct": overall_success_rate,
            "overall_failure_rate_pct": overall_failure_rate,
            "gross_failed_value_inr": failed_val,
            "recoverable_technical_loss_inr": loss_breakdown["recoverable_technical_loss"],
            "recoverable_opportunity_inr": loss_breakdown["recoverable_opportunity_estimate"],
            "highest_failure_method": {
                "method": worst_method[0],
                "failure_rate_pct": worst_method[1],
            },
            "payment_methods": method_perf,
            "failure_rate_by_method": method_failure_rates,
            "top_overall_failure_reasons": top_failure_reasons,
            "top_upi_failure_reasons": upi_failure_reasons,
        }

        # Store in state
        if "evidence" not in state or state["evidence"] is None:
            state["evidence"] = {}
        if "tool_results" not in state or state["tool_results"] is None:
            state["tool_results"] = {}
        if "executed_agents" not in state or state["executed_agents"] is None:
            state["executed_agents"] = []

        state["evidence"]["payment"] = evidence_payload
        state["tool_results"]["payment_analysis"] = evidence_payload
        state["executed_agents"].append("payment_agent")

        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("payment_agent", dur_ms, success=True)
        logger.info("Payment agent successfully executed deterministic analytics.")
    except Exception as e:
        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("payment_agent", dur_ms, success=False)
        record_error("analytics_error")
        logger.error(f"Error in payment_agent_node: {e}")
        state["errors"] = state.get("errors", []) + [f"Payment Agent error: {str(e)}"]

    return state
