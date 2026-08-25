"""Revenue Specialist Agent.

Investigates macro revenue trends, business health KPIs, periodic degradation,
and what-if simulation impacts using deterministic analytics tools.
"""

import logging
import re
import time
from backend.graph.state import PayPilotState
from backend.tools.analytics import (
    get_business_health_summary,
    get_revenue_trend,
    get_revenue_lost_by_failure,
    get_what_if_success_rate,
)
from backend.observability.metrics import record_agent_execution, record_error
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


def _extract_target_uplift(query: str) -> float:
    """Extracts percentage uplift number from user query if present (e.g. '3%' -> 3.0), else defaults to 3.0."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", query)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 3.0


@trace_span("agent.revenue", component="revenue_agent")
def revenue_agent_node(state: PayPilotState) -> PayPilotState:
    """Revenue agent execution node.

    Calls deterministic revenue analytics tools, trend trackers, and what-if simulation tools.
    """
    if "revenue_agent" not in state.get("required_agents", []):
        return state

    t_start = time.perf_counter()
    try:
        health_summary = get_business_health_summary()
        weekly_trend = get_revenue_trend(frequency="W")
        monthly_trend = get_revenue_trend(frequency="M")
        loss_breakdown = get_revenue_lost_by_failure()

        # Extract target uplift for simulation if query contains what-if request
        uplift_val = _extract_target_uplift(state.get("user_query", ""))
        what_if_baseline = get_what_if_success_rate(target_success_rate=uplift_val)

        # Calculate monthly degradation trend if multi-month data exists
        trend_summary = []
        if len(monthly_trend) >= 2:
            first_m = monthly_trend[0]
            last_m = monthly_trend[-1]
            rev_delta = round(last_m["realized_revenue"] - first_m["realized_revenue"], 2)
            s_rate_delta = round(last_m["success_rate_pct"] - first_m["success_rate_pct"], 2)
            trend_summary = {
                "first_period": first_m["period"],
                "last_period": last_m["period"],
                "revenue_delta_inr": rev_delta,
                "success_rate_delta_pct": s_rate_delta,
            }

        evidence_payload = {
            "business_health": health_summary,
            "monthly_trend": monthly_trend,
            "weekly_trend": weekly_trend,
            "period_degradation": trend_summary,
            "revenue_loss_breakdown": loss_breakdown,
            "what_if_simulation": what_if_baseline,
        }

        # Store in state
        if "evidence" not in state or state["evidence"] is None:
            state["evidence"] = {}
        if "tool_results" not in state or state["tool_results"] is None:
            state["tool_results"] = {}
        if "executed_agents" not in state or state["executed_agents"] is None:
            state["executed_agents"] = []

        state["evidence"]["revenue"] = evidence_payload
        state["tool_results"]["revenue_analysis"] = evidence_payload
        state["executed_agents"].append("revenue_agent")

        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("revenue_agent", dur_ms, success=True)
        logger.info("Revenue agent successfully executed deterministic analytics.")
    except Exception as e:
        dur_ms = round((time.perf_counter() - t_start) * 1000, 2)
        record_agent_execution("revenue_agent", dur_ms, success=False)
        record_error("analytics_error")
        logger.error(f"Error in revenue_agent_node: {e}")
        state["errors"] = state.get("errors", []) + [f"Revenue Agent error: {str(e)}"]

    return state
