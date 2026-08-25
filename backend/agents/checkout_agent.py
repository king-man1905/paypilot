"""Checkout Specialist Agent.

Investigates device conversion rates, mobile vs desktop checkout drop-offs,
and multi-dimensional failure leaks using deterministic analytics tools.
"""

import logging
import time
from backend.graph.state import PayPilotState
from backend.tools.analytics import (
    get_conversion_by_device,
    get_top_revenue_leaks,
)
from backend.observability.metrics import record_agent_execution, record_error
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


@trace_span("agent.checkout", component="checkout_agent")
def checkout_agent_node(state: PayPilotState) -> PayPilotState:
    """Checkout agent execution node.

    Calls deterministic device and checkout analytics tools to compile evidence.
    """
    if "checkout_agent" not in state.get("required_agents", []):
        return state

    t_start = time.perf_counter()
    try:
        device_perf = get_conversion_by_device()
        top_leaks = get_top_revenue_leaks(limit=5)

        # Identify lowest converting device
        lowest_converting_device = min(
            device_perf.items(),
            key=lambda x: x[1]["conversion_rate_pct"],
        ) if device_perf else ("None", {"conversion_rate_pct": 0.0})

        # Calculate mobile vs desktop disparity
        mobile_txns = 0
        mobile_success = 0
        desktop_txns = 0
        desktop_success = 0

        for dev, stats in device_perf.items():
            if "Mobile" in dev:
                mobile_txns += stats["total_attempts"]
                mobile_success += stats["successful_count"]
            elif dev == "Desktop":
                desktop_txns += stats["total_attempts"]
                desktop_success += stats["successful_count"]

        mobile_conversion = round((mobile_success / mobile_txns) * 100, 2) if mobile_txns > 0 else 0.0
        desktop_conversion = round((desktop_success / desktop_txns) * 100, 2) if desktop_txns > 0 else 0.0

        evidence_payload = {
            "device_performance": device_perf,
            "lowest_converting_device": {
                "device": lowest_converting_device[0],
                "conversion_rate_pct": lowest_converting_device[1].get("conversion_rate_pct", 0.0),
                "failure_rate_pct": lowest_converting_device[1].get("failure_rate_pct", 0.0),
            },
            "mobile_conversion_rate_pct": mobile_conversion,
            "desktop_conversion_rate_pct": desktop_conversion,
            "mobile_desktop_conversion_gap_pct": round(desktop_conversion - mobile_conversion, 2),
            "top_multidimensional_leaks": top_leaks,
        }

        # Store in state
        if "evidence" not in state or state["evidence"] is None:
            state["evidence"] = {}
        if "tool_results" not in state or state["tool_results"] is None:
            state["tool_results"] = {}
        if "executed_agents" not in state or state["executed_agents"] is None:
            state["executed_agents"] = []

        state["evidence"]["checkout"] = evidence_payload
        state["tool_results"]["checkout_analysis"] = evidence_payload
        state["executed_agents"].append("checkout_agent")

        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("checkout_agent", dur_ms, success=True)
        logger.info("Checkout agent successfully executed deterministic analytics.")
    except Exception as e:
        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("checkout_agent", dur_ms, success=False)
        record_error("analytics_error")
        logger.error(f"Error in checkout_agent_node: {e}")
        state["errors"] = state.get("errors", []) + [f"Checkout Agent error: {str(e)}"]

    return state
