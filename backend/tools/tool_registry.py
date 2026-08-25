"""Tool Registry for PayPilot Agents.

Exposes deterministic Python analytics functions as structured LangChain tools.
Agents invoke these tools to fetch numerical evidence.
"""

from typing import Any, Dict, List, Optional
from langchain_core.tools import tool

from backend.tools.analytics import (
    get_business_health_summary,
    get_total_revenue,
    get_payment_success_rate,
    get_revenue_by_payment_method,
    get_failure_rate_by_payment_method,
    get_failure_reasons,
    get_conversion_by_device,
    get_conversion_by_customer_type,
    get_category_performance,
    get_revenue_trend,
    get_revenue_lost_by_failure,
    get_top_revenue_leaks,
    get_what_if_success_rate,
)


@tool
def tool_get_business_health_summary() -> Dict[str, Any]:
    """Fetches high-level executive KPIs including total revenue, success rate, failed payment value, and recoverable technical loss."""
    return get_business_health_summary()


@tool
def tool_get_payment_method_analysis() -> Dict[str, Dict[str, Any]]:
    """Analyzes performance metrics for all payment methods (UPI, Cards, Netbanking, Wallet), including volume, success rates, and lost revenue."""
    return get_revenue_by_payment_method()


@tool
def tool_get_failure_rate_by_payment_method() -> Dict[str, float]:
    """Returns the failure rate percentage for each payment method, ranked from highest failure rate to lowest."""
    return get_failure_rate_by_payment_method()


@tool
def tool_get_failure_reasons(
    payment_method: Optional[str] = None,
    device_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves top technical and user failure reasons (e.g. BANK_SERVER_TIMEOUT, UPI_APP_NOT_RESPONDING, USER_ABORTED) with counts and lost revenue.

    Args:
        payment_method: Optional filter (e.g. 'UPI', 'Credit_Card', 'Netbanking').
        device_type: Optional filter (e.g. 'Mobile_Android', 'Desktop').
    """
    return get_failure_reasons(payment_method=payment_method, device_type=device_type)


@tool
def tool_get_conversion_by_device() -> Dict[str, Dict[str, Any]]:
    """Analyzes checkout conversion rates, successful orders, drop-offs, and failure rates segmented by device type (Mobile_Android, Mobile_iOS, Desktop, Tablet)."""
    return get_conversion_by_device()


@tool
def tool_get_customer_analysis() -> Dict[str, Dict[str, Any]]:
    """Analyzes customer cohort behaviors and conversion performance across NEW, RETURNING, and VIP customer segments."""
    return get_conversion_by_customer_type()


@tool
def tool_get_category_performance() -> Dict[str, Dict[str, Any]]:
    """Analyzes product categories (Electronics, Fashion, Grocery, etc.) for revenue, failure rates, and refund rates."""
    return get_category_performance()


@tool
def tool_get_revenue_trend(frequency: str = "W") -> List[Dict[str, Any]]:
    """Retrieves time-series revenue and success rate trends.

    Args:
        frequency: Time grouping ('W' for weekly, 'M' for monthly).
    """
    return get_revenue_trend(frequency=frequency)


@tool
def tool_get_revenue_lost_by_failure() -> Dict[str, Any]:
    """Calculates total lost revenue split by technical gateway failures vs user drop-offs, and estimates recoverable revenue."""
    return get_revenue_lost_by_failure()


@tool
def tool_get_top_revenue_leaks(limit: int = 5) -> List[Dict[str, Any]]:
    """Identifies the top multidimensional revenue leakage hotspots (Method × Device × Failure Reason) ranked by INR lost."""
    return get_top_revenue_leaks(limit=limit)


@tool
def tool_get_what_if_success_rate(target_success_rate: float) -> Dict[str, Any]:
    """Calculates the estimated additional transactions and revenue recovered if payment success rate improves.

    Args:
        target_success_rate: Target success rate percentage (e.g. 85.0) or uplift delta (e.g. 3.0 for +3%).
    """
    return get_what_if_success_rate(target_success_rate=target_success_rate)


# Public list of all tools
ALL_TOOLS = [
    tool_get_business_health_summary,
    tool_get_payment_method_analysis,
    tool_get_failure_rate_by_payment_method,
    tool_get_failure_reasons,
    tool_get_conversion_by_device,
    tool_get_customer_analysis,
    tool_get_category_performance,
    tool_get_revenue_trend,
    tool_get_revenue_lost_by_failure,
    tool_get_top_revenue_leaks,
    tool_get_what_if_success_rate,
]
