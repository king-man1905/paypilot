"""Deterministic Analytics Engine for PayPilot.

This module is the single source of truth for all numerical and business metrics.
LLM agents use these deterministic tools to retrieve verifiable evidence rather than
calculating metrics on their own.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import numpy as np
import pandas as pd

from backend.config import DATA_PATH


# ============================================================================
# 1. DATA LOADING & VALIDATION
# ============================================================================

REQUIRED_COLUMNS = [
    "transaction_id",
    "timestamp",
    "merchant_id",
    "customer_id",
    "amount",
    "payment_method",
    "payment_status",
    "failure_reason",
    "device_type",
    "customer_type",
    "product_category",
    "refund_status",
    "checkout_step_reached",
]


import threading

_CACHED_DF: Optional[pd.DataFrame] = None
_CACHE_LOCK = threading.Lock()


def clear_dataset_cache() -> None:
    """Clears the active dataset repository cache (used for test isolation)."""
    from backend.storage import get_transaction_repository
    try:
        repo = get_transaction_repository()
        repo.clear_cache()
    except Exception:
        pass


def load_transaction_data(
    file_path: Optional[Union[str, Path]] = None,
    force_reload: bool = False,
) -> pd.DataFrame:
    """Loads and validates merchant transaction data via active TransactionRepository.

    Args:
        file_path: Optional path to a specific CSV file. Defaults to active configured repository.
        force_reload: If True, bypasses cache and forces reloading from storage.

    Returns:
        pd.DataFrame: Cleaned and validated transaction DataFrame.

    Raises:
        FileNotFoundError: If target file does not exist.
        ValueError: If required columns are missing or dataset is empty.
    """
    from backend.storage import (
        CSVTransactionRepository,
        get_transaction_repository,
    )

    if file_path is not None:
        repo = CSVTransactionRepository(csv_path=file_path)
        return repo.load_dataframe(force_reload=force_reload)

    repo = get_transaction_repository()
    return repo.load_dataframe(force_reload=force_reload)


def _get_df(df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Helper to return provided DataFrame or load cached default dataset."""
    if df is not None:
        return df
    return load_transaction_data()



# ============================================================================
# 2. CORE BUSINESS METRIC FUNCTIONS
# ============================================================================

def get_total_revenue(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates total realized revenue from successful transactions (in INR)."""
    data = _get_df(df)
    if data.empty:
        return 0.0
    successful = data[data["payment_status"] == "SUCCESS"]
    return round(float(successful["amount"].sum()), 2)


def get_transaction_count(df: Optional[pd.DataFrame] = None) -> int:
    """Returns total number of transaction attempts."""
    data = _get_df(df)
    return int(len(data))


def get_successful_transaction_count(df: Optional[pd.DataFrame] = None) -> int:
    """Returns count of successful transactions."""
    data = _get_df(df)
    if data.empty:
        return 0
    return int((data["payment_status"] == "SUCCESS").sum())


def get_failed_transaction_count(df: Optional[pd.DataFrame] = None) -> int:
    """Returns count of failed (FAILED + DROPPED) transaction attempts."""
    data = _get_df(df)
    if data.empty:
        return 0
    return int((data["payment_status"].isin(["FAILED", "DROPPED"])).sum())


def get_payment_success_rate(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates payment success rate percentage: (successful / total) * 100."""
    data = _get_df(df)
    total = len(data)
    if total == 0:
        return 0.0
    successful = (data["payment_status"] == "SUCCESS").sum()
    return round(float((successful / total) * 100), 2)


def get_payment_failure_rate(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates payment failure rate percentage: (failed+dropped / total) * 100."""
    data = _get_df(df)
    total = len(data)
    if total == 0:
        return 0.0
    failed = data["payment_status"].isin(["FAILED", "DROPPED"]).sum()
    return round(float((failed / total) * 100), 2)


def get_average_order_value(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates Average Order Value (AOV) for successful orders (in INR)."""
    data = _get_df(df)
    successful = data[data["payment_status"] == "SUCCESS"]
    if len(successful) == 0:
        return 0.0
    return round(float(successful["amount"].mean()), 2)


def get_refund_rate(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates refund rate percentage over successful orders: (refunded_orders / successful_orders) * 100."""
    data = _get_df(df)
    successful = data[data["payment_status"] == "SUCCESS"]
    if len(successful) == 0:
        return 0.0
    refunded = (successful["refund_status"] != "NO_REFUND").sum()
    return round(float((refunded / len(successful)) * 100), 2)


def get_failed_payment_value(df: Optional[pd.DataFrame] = None) -> float:
    """Calculates total gross transaction value lost from failed & dropped attempts (in INR)."""
    data = _get_df(df)
    if data.empty:
        return 0.0
    failed = data[data["payment_status"].isin(["FAILED", "DROPPED"])]
    return round(float(failed["amount"].sum()), 2)


# ============================================================================
# 3. BREAKDOWN & DIMENSIONAL ANALYTICS
# ============================================================================

def get_revenue_by_payment_method(df: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
    """Calculates revenue and volume metrics broken down by payment method."""
    data = _get_df(df)
    if data.empty:
        return {}

    results = {}
    for method, group in data.groupby("payment_method"):
        total_txns = len(group)
        successful_group = group[group["payment_status"] == "SUCCESS"]
        failed_group = group[group["payment_status"].isin(["FAILED", "DROPPED"])]
        
        success_count = len(successful_group)
        failed_count = len(failed_group)
        revenue = round(float(successful_group["amount"].sum()), 2)
        failed_value = round(float(failed_group["amount"].sum()), 2)
        success_rate = round(float((success_count / total_txns) * 100), 2) if total_txns > 0 else 0.0

        results[str(method)] = {
            "total_attempts": total_txns,
            "successful_transactions": success_count,
            "failed_transactions": failed_count,
            "success_rate_pct": success_rate,
            "realized_revenue": revenue,
            "lost_failed_value": failed_value,
        }
    return results


def get_failure_rate_by_payment_method(df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
    """Returns failure rate percentage for each payment method."""
    data = _get_df(df)
    if data.empty:
        return {}
    
    rates = {}
    for method, group in data.groupby("payment_method"):
        total = len(group)
        failed = group["payment_status"].isin(["FAILED", "DROPPED"]).sum()
        rate = round(float((failed / total) * 100), 2) if total > 0 else 0.0
        rates[str(method)] = rate
    return dict(sorted(rates.items(), key=lambda x: x[1], reverse=True))


def get_failure_reasons(
    df: Optional[pd.DataFrame] = None,
    payment_method: Optional[str] = None,
    device_type: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Analyzes failure reason distribution and associated lost revenue."""
    data = _get_df(df)
    if data.empty:
        return []

    subset = data[data["payment_status"].isin(["FAILED", "DROPPED"])]
    if payment_method:
        subset = subset[subset["payment_method"] == payment_method]
    if device_type:
        subset = subset[subset["device_type"] == device_type]

    total_failures = len(subset)
    if total_failures == 0:
        return []

    reasons = []
    for reason, group in subset.groupby("failure_reason"):
        count = len(group)
        lost_val = round(float(group["amount"].sum()), 2)
        share_pct = round(float((count / total_failures) * 100), 2)
        reasons.append({
            "failure_reason": str(reason),
            "count": count,
            "share_of_failures_pct": share_pct,
            "lost_revenue_inr": lost_val,
        })

    reasons.sort(key=lambda x: x["count"], reverse=True)
    return reasons[:limit]


def get_conversion_by_device(df: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
    """Calculates checkout conversion and failure rates broken down by device type."""
    data = _get_df(df)
    if data.empty:
        return {}

    results = {}
    for device, group in data.groupby("device_type"):
        total = len(group)
        success = len(group[group["payment_status"] == "SUCCESS"])
        failed = len(group[group["payment_status"] == "FAILED"])
        dropped = len(group[group["payment_status"] == "DROPPED"])
        revenue = round(float(group[group["payment_status"] == "SUCCESS"]["amount"].sum()), 2)
        lost_val = round(float(group[group["payment_status"].isin(["FAILED", "DROPPED"])]["amount"].sum()), 2)

        results[str(device)] = {
            "total_attempts": total,
            "successful_count": success,
            "failed_count": failed,
            "dropped_count": dropped,
            "conversion_rate_pct": round(float((success / total) * 100), 2) if total > 0 else 0.0,
            "failure_rate_pct": round(float(((failed + dropped) / total) * 100), 2) if total > 0 else 0.0,
            "realized_revenue": revenue,
            "lost_failed_value": lost_val,
        }
    return results


def get_conversion_by_customer_type(df: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
    """Calculates metrics across customer cohorts: NEW, RETURNING, VIP."""
    data = _get_df(df)
    if data.empty:
        return {}

    results = {}
    for ctype, group in data.groupby("customer_type"):
        total = len(group)
        successful = group[group["payment_status"] == "SUCCESS"]
        revenue = round(float(successful["amount"].sum()), 2)
        aov = round(float(successful["amount"].mean()), 2) if len(successful) > 0 else 0.0
        success_rate = round(float((len(successful) / total) * 100), 2) if total > 0 else 0.0

        results[str(ctype)] = {
            "total_attempts": total,
            "successful_count": len(successful),
            "success_rate_pct": success_rate,
            "realized_revenue": revenue,
            "average_order_value": aov,
            "unique_customers": int(group["customer_id"].nunique()),
        }
    return results


def get_category_performance(df: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
    """Calculates revenue, failure rate, and refund rate by product category."""
    data = _get_df(df)
    if data.empty:
        return {}

    results = {}
    for cat, group in data.groupby("product_category"):
        total = len(group)
        successful = group[group["payment_status"] == "SUCCESS"]
        refunded = successful[successful["refund_status"] != "NO_REFUND"]
        
        revenue = round(float(successful["amount"].sum()), 2)
        refunded_amount = round(float(refunded["amount"].sum()), 2)
        success_rate = round(float((len(successful) / total) * 100), 2) if total > 0 else 0.0
        refund_rate = round(float((len(refunded) / len(successful)) * 100), 2) if len(successful) > 0 else 0.0

        results[str(cat)] = {
            "total_attempts": total,
            "successful_orders": len(successful),
            "success_rate_pct": success_rate,
            "gross_revenue": revenue,
            "refunded_orders_count": len(refunded),
            "refund_rate_pct": refund_rate,
            "refunded_amount": refunded_amount,
            "net_revenue": round(revenue - refunded_amount, 2),
        }
    return results


def get_revenue_trend(
    df: Optional[pd.DataFrame] = None,
    frequency: str = "W",
) -> List[Dict[str, Any]]:
    """Calculates weekly or monthly revenue, success rate, and failure trends over time.

    Args:
        df: Optional transaction DataFrame.
        frequency: 'W' for weekly, 'M' for monthly, 'D' for daily.

    Returns:
        List of periodic time-bucket metrics.
    """
    data = _get_df(df).copy()
    if data.empty:
        return []

    data["period"] = data["timestamp"].dt.to_period(frequency).astype(str)
    trend = []

    for period_label, group in data.groupby("period"):
        total = len(group)
        successful = group[group["payment_status"] == "SUCCESS"]
        failed = group[group["payment_status"].isin(["FAILED", "DROPPED"])]
        
        revenue = round(float(successful["amount"].sum()), 2)
        failed_val = round(float(failed["amount"].sum()), 2)
        s_rate = round(float((len(successful) / total) * 100), 2) if total > 0 else 0.0

        trend.append({
            "period": period_label,
            "total_attempts": total,
            "successful_orders": len(successful),
            "realized_revenue": revenue,
            "lost_failed_revenue": failed_val,
            "success_rate_pct": s_rate,
            "aov": round(float(successful["amount"].mean()), 2) if len(successful) > 0 else 0.0,
        })

    trend.sort(key=lambda x: x["period"])
    return trend


def get_revenue_lost_by_failure(df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Provides a comprehensive financial impact breakdown of failed payments."""
    data = _get_df(df)
    if data.empty:
        return {
            "total_lost_revenue": 0.0,
            "total_failed_attempts": 0,
            "recoverable_technical_loss": 0.0,
            "user_drop_loss": 0.0,
        }

    failed = data[data["payment_status"].isin(["FAILED", "DROPPED"])]
    total_lost = round(float(failed["amount"].sum()), 2)

    # Technical failures (timeouts, app unresponsive, gateway downtime, latency)
    tech_reasons = ["BANK_SERVER_TIMEOUT", "UPI_APP_NOT_RESPONDING", "GATEWAY_DOWNTIME", "NETWORK_LATENCY"]
    tech_failed = failed[failed["failure_reason"].isin(tech_reasons)]
    tech_loss = round(float(tech_failed["amount"].sum()), 2)

    user_dropped = failed[failed["failure_reason"].isin(["USER_ABORTED", "INVALID_OTP", "INSUFFICIENT_FUNDS"])]
    user_loss = round(float(user_dropped["amount"].sum()), 2)

    return {
        "total_lost_revenue": total_lost,
        "total_failed_attempts": len(failed),
        "recoverable_technical_loss": tech_loss,
        "recoverable_technical_share_pct": round((tech_loss / total_lost) * 100, 2) if total_lost > 0 else 0.0,
        "user_drop_loss": user_loss,
        "recoverable_opportunity_estimate": round(tech_loss * 0.70, 2),  # Conservative 70% technical recovery
    }


def get_top_revenue_leaks(
    df: Optional[pd.DataFrame] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Identifies and ranks top dimensional leakage hotspots (Method × Device × Failure Reason)

    Quantifies the exact lost revenue and failure frequency for each slice.
    """
    data = _get_df(df)
    if data.empty:
        return []

    failed = data[data["payment_status"].isin(["FAILED", "DROPPED"])]
    if failed.empty:
        return []

    grouped = (
        failed.groupby(["payment_method", "device_type", "failure_reason"])
        .agg(
            failed_count=("transaction_id", "count"),
            lost_amount=("amount", "sum"),
        )
        .reset_index()
    )

    # Calculate overall failure count in that method & device for context
    grouped["lost_amount"] = grouped["lost_amount"].round(2)
    grouped = grouped.sort_values(by="lost_amount", ascending=False)

    leaks = []
    total_failed_val = failed["amount"].sum()

    for _, row in grouped.head(limit).iterrows():
        lost_val = float(row["lost_amount"])
        share = round((lost_val / total_failed_val) * 100, 2) if total_failed_val > 0 else 0.0
        leaks.append({
            "payment_method": str(row["payment_method"]),
            "device_type": str(row["device_type"]),
            "failure_reason": str(row["failure_reason"]),
            "failed_transactions": int(row["failed_count"]),
            "lost_revenue_inr": lost_val,
            "share_of_total_leakage_pct": share,
        })

    return leaks


# ============================================================================
# 4. BUSINESS HEALTH SUMMARY & COMPARISON
# ============================================================================

def get_business_health_summary(df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """Generates an executive KPI health summary across all core dimensions."""
    data = _get_df(df)
    if data.empty:
        return {}

    total_rev = get_total_revenue(data)
    total_txns = get_transaction_count(data)
    success_txns = get_successful_transaction_count(data)
    failed_txns = get_failed_transaction_count(data)
    success_rate = get_payment_success_rate(data)
    failure_rate = get_payment_failure_rate(data)
    aov = get_average_order_value(data)
    refund_rate = get_refund_rate(data)
    failed_val = get_failed_payment_value(data)
    lost_breakdown = get_revenue_lost_by_failure(data)

    return {
        "total_realized_revenue_inr": total_rev,
        "total_transaction_attempts": total_txns,
        "successful_transactions": success_txns,
        "failed_transactions": failed_txns,
        "payment_success_rate_pct": success_rate,
        "payment_failure_rate_pct": failure_rate,
        "average_order_value_inr": aov,
        "refund_rate_pct": refund_rate,
        "gross_failed_value_inr": failed_val,
        "recoverable_technical_loss_inr": lost_breakdown["recoverable_technical_loss"],
        "recoverable_opportunity_inr": lost_breakdown["recoverable_opportunity_estimate"],
    }


def get_comparison(period_a: pd.DataFrame, period_b: pd.DataFrame) -> Dict[str, Any]:
    """Compares two transaction periods (e.g. Month 1 vs Month 3) and computes deltas.

    Args:
        period_a: Baseline / earlier period DataFrame.
        period_b: Target / later period DataFrame.

    Returns:
        Dict detailing absolute and percentage changes in core metrics.
    """
    summary_a = get_business_health_summary(period_a)
    summary_b = get_business_health_summary(period_b)

    def _calc_delta(val_b: float, val_a: float) -> Dict[str, float]:
        abs_change = round(val_b - val_a, 2)
        pct_change = round(((val_b - val_a) / val_a) * 100, 2) if val_a != 0 else 0.0
        return {
            "period_a": val_a,
            "period_b": val_b,
            "absolute_delta": abs_change,
            "percentage_delta": pct_change,
        }

    return {
        "revenue_comparison": _calc_delta(
            summary_b.get("total_realized_revenue_inr", 0.0),
            summary_a.get("total_realized_revenue_inr", 0.0),
        ),
        "transaction_volume_comparison": _calc_delta(
            summary_b.get("total_transaction_attempts", 0),
            summary_a.get("total_transaction_attempts", 0),
        ),
        "success_rate_comparison": _calc_delta(
            summary_b.get("payment_success_rate_pct", 0.0),
            summary_a.get("payment_success_rate_pct", 0.0),
        ),
        "failure_rate_comparison": _calc_delta(
            summary_b.get("payment_failure_rate_pct", 0.0),
            summary_a.get("payment_failure_rate_pct", 0.0),
        ),
        "aov_comparison": _calc_delta(
            summary_b.get("average_order_value_inr", 0.0),
            summary_a.get("average_order_value_inr", 0.0),
        ),
        "refund_rate_comparison": _calc_delta(
            summary_b.get("refund_rate_pct", 0.0),
            summary_a.get("refund_rate_pct", 0.0),
        ),
        "failed_value_comparison": _calc_delta(
            summary_b.get("gross_failed_value_inr", 0.0),
            summary_a.get("gross_failed_value_inr", 0.0),
        ),
    }


# ============================================================================
# 5. WHAT-IF SIMULATION CALCULATION
# ============================================================================

def get_what_if_success_rate(
    target_success_rate: float,
    df: Optional[pd.DataFrame] = None,
    relative_uplift: bool = False,
) -> Dict[str, Any]:
    """Calculates transparent, deterministic revenue recovery from improved payment success rates.

    Args:
        target_success_rate: Either absolute target (e.g. 85.0 for 85%) or relative delta (e.g. 3.0 for +3%).
        df: Optional transaction DataFrame.
        relative_uplift: If True, target_success_rate is treated as a delta to add to current rate.

    Returns:
        Dict with current vs simulated metrics, additional transactions, recovered revenue, and assumptions.
    """
    data = _get_df(df)
    total_attempts = get_transaction_count(data)
    current_success_txns = get_successful_transaction_count(data)
    current_revenue = get_total_revenue(data)
    current_success_rate = get_payment_success_rate(data)
    current_aov = get_average_order_value(data)

    if total_attempts == 0:
        return {
            "error": "Cannot run what-if simulation on empty dataset",
            "current_success_rate_pct": 0.0,
            "target_success_rate_pct": 0.0,
            "additional_successful_transactions": 0,
            "estimated_additional_revenue_inr": 0.0,
        }

    # Resolve target success rate
    if relative_uplift:
        target_rate = min(100.0, current_success_rate + target_success_rate)
        uplift_delta = target_success_rate
    elif target_success_rate <= current_success_rate and target_success_rate < 15.0:
        # User passed e.g. 3.0 meaning "+3%"
        uplift_delta = target_success_rate
        target_rate = min(100.0, current_success_rate + target_success_rate)
    else:
        target_rate = min(100.0, max(0.0, target_success_rate))
        uplift_delta = round(target_rate - current_success_rate, 2)

    # Deterministic formula:
    # Target successful transactions = total_attempts * (target_rate / 100)
    target_successful_txns = int(round(total_attempts * (target_rate / 100.0)))
    additional_successful_txns = max(0, target_successful_txns - current_success_txns)
    
    # Additional revenue = additional transactions * current average order value
    estimated_additional_revenue = round(float(additional_successful_txns * current_aov), 2)
    simulated_total_revenue = round(current_revenue + estimated_additional_revenue, 2)
    revenue_growth_pct = round((estimated_additional_revenue / current_revenue) * 100, 2) if current_revenue > 0 else 0.0

    return {
        "total_transaction_attempts": total_attempts,
        "current_successful_transactions": current_success_txns,
        "current_payment_success_rate_pct": current_success_rate,
        "target_payment_success_rate_pct": target_rate,
        "rate_uplift_pct": uplift_delta,
        "current_realized_revenue_inr": current_revenue,
        "average_order_value_inr": current_aov,
        "additional_successful_transactions": additional_successful_txns,
        "estimated_additional_revenue_inr": estimated_additional_revenue,
        "projected_total_revenue_inr": simulated_total_revenue,
        "projected_revenue_growth_pct": revenue_growth_pct,
        "assumptions": [
            f"Average Order Value remains constant at INR {current_aov:,.2f}",
            f"Total transaction attempt volume remains constant at {total_attempts:,}",
            f"Improvement applies uniformly across customer cohorts without inducing additional cart cancellations",
        ],
    }
