"""Unit tests for the Deterministic Analytics Engine."""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from backend.tools.analytics import (
    load_transaction_data,
    get_total_revenue,
    get_transaction_count,
    get_successful_transaction_count,
    get_failed_transaction_count,
    get_payment_success_rate,
    get_payment_failure_rate,
    get_average_order_value,
    get_refund_rate,
    get_failed_payment_value,
    get_revenue_by_payment_method,
    get_failure_rate_by_payment_method,
    get_failure_reasons,
    get_conversion_by_device,
    get_conversion_by_customer_type,
    get_category_performance,
    get_revenue_trend,
    get_revenue_lost_by_failure,
    get_top_revenue_leaks,
    get_business_health_summary,
    get_comparison,
    get_what_if_success_rate,
)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """Creates a small, deterministic 10-row dataset for exact hand-verified assertions.

    Summary of this fixture:
    Total: 10 transactions
    Success: 7 (amounts: 1000, 2000, 3000, 4000, 500, 1500, 2000) -> Sum = 14000.0, AOV = 2000.0
    Failed: 2 (amounts: 1000, 3000) -> Sum = 4000.0
    Dropped: 1 (amount: 2000) -> Sum = 2000.0
    Total Failed & Dropped = 3 -> Sum = 6000.0
    Success rate = (7 / 10) * 100 = 70.0%
    Failure rate = (3 / 10) * 100 = 30.0%
    Refunds among successful: 1 FULL_REFUND (TXN_002), 1 PARTIAL_REFUND (TXN_005) -> (2 / 7) * 100 = 28.57%
    """
    data = [
        {
            "transaction_id": "TXN_001",
            "timestamp": "2026-01-01 10:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_01",
            "amount": 1000.0,
            "payment_method": "UPI",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Mobile_Android",
            "customer_type": "NEW",
            "product_category": "Electronics",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_002",
            "timestamp": "2026-01-02 11:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_02",
            "amount": 2000.0,
            "payment_method": "UPI",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Mobile_Android",
            "customer_type": "RETURNING",
            "product_category": "Fashion",
            "refund_status": "FULL_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_003",
            "timestamp": "2026-01-03 12:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_03",
            "amount": 3000.0,
            "payment_method": "Credit_Card",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Desktop",
            "customer_type": "VIP",
            "product_category": "Electronics",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_004",
            "timestamp": "2026-01-04 13:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_01",
            "amount": 4000.0,
            "payment_method": "Credit_Card",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Desktop",
            "customer_type": "RETURNING",
            "product_category": "Electronics",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_005",
            "timestamp": "2026-01-05 14:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_04",
            "amount": 500.0,
            "payment_method": "UPI",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Mobile_iOS",
            "customer_type": "NEW",
            "product_category": "Grocery",
            "refund_status": "PARTIAL_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_006",
            "timestamp": "2026-01-06 15:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_05",
            "amount": 1500.0,
            "payment_method": "Debit_Card",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Mobile_Android",
            "customer_type": "NEW",
            "product_category": "Fashion",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_007",
            "timestamp": "2026-01-07 16:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_06",
            "amount": 2000.0,
            "payment_method": "Netbanking",
            "payment_status": "SUCCESS",
            "failure_reason": "None",
            "device_type": "Desktop",
            "customer_type": "RETURNING",
            "product_category": "Home_Kitchen",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_COMPLETED",
        },
        {
            "transaction_id": "TXN_008",
            "timestamp": "2026-01-08 17:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_07",
            "amount": 1000.0,
            "payment_method": "UPI",
            "payment_status": "FAILED",
            "failure_reason": "UPI_APP_NOT_RESPONDING",
            "device_type": "Mobile_Android",
            "customer_type": "NEW",
            "product_category": "Fashion",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_INITIATED",
        },
        {
            "transaction_id": "TXN_009",
            "timestamp": "2026-01-09 18:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_08",
            "amount": 3000.0,
            "payment_method": "UPI",
            "payment_status": "FAILED",
            "failure_reason": "BANK_SERVER_TIMEOUT",
            "device_type": "Mobile_Android",
            "customer_type": "RETURNING",
            "product_category": "Electronics",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_INITIATED",
        },
        {
            "transaction_id": "TXN_010",
            "timestamp": "2026-01-10 19:00:00",
            "merchant_id": "mer_01",
            "customer_id": "CUST_09",
            "amount": 2000.0,
            "payment_method": "Netbanking",
            "payment_status": "DROPPED",
            "failure_reason": "USER_ABORTED",
            "device_type": "Mobile_Android",
            "customer_type": "NEW",
            "product_category": "Fashion",
            "refund_status": "NO_REFUND",
            "checkout_step_reached": "PAYMENT_INITIATED",
        },
    ]
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def test_core_kpis(sample_df):
    """Test all fundamental KPI calculation functions against hand-verified fixture."""
    assert get_total_revenue(sample_df) == 14000.0
    assert get_transaction_count(sample_df) == 10
    assert get_successful_transaction_count(sample_df) == 7
    assert get_failed_transaction_count(sample_df) == 3
    assert get_payment_success_rate(sample_df) == 70.0
    assert get_payment_failure_rate(sample_df) == 30.0
    assert get_average_order_value(sample_df) == 2000.0
    assert get_refund_rate(sample_df) == 28.57
    assert get_failed_payment_value(sample_df) == 6000.0


def test_payment_method_breakdown(sample_df):
    """Test payment method performance breakdown."""
    method_perf = get_revenue_by_payment_method(sample_df)
    
    # UPI: 5 total (TXN 1, 2, 5 success = 3500, TXN 8, 9 failed = 4000)
    assert "UPI" in method_perf
    assert method_perf["UPI"]["total_attempts"] == 5
    assert method_perf["UPI"]["successful_transactions"] == 3
    assert method_perf["UPI"]["failed_transactions"] == 2
    assert method_perf["UPI"]["success_rate_pct"] == 60.0
    assert method_perf["UPI"]["realized_revenue"] == 3500.0
    assert method_perf["UPI"]["lost_failed_value"] == 4000.0

    failure_rates = get_failure_rate_by_payment_method(sample_df)
    # Netbanking: 1 success, 1 dropped -> 50%
    assert failure_rates["Netbanking"] == 50.0
    # UPI: 2/5 -> 40%
    assert failure_rates["UPI"] == 40.0
    # Credit Card: 0/2 -> 0%
    assert failure_rates["Credit_Card"] == 0.0


def test_failure_reasons(sample_df):
    """Test failure reason breakdown and filters."""
    reasons = get_failure_reasons(sample_df)
    assert len(reasons) == 3
    
    # Check top reason
    reason_names = [r["failure_reason"] for r in reasons]
    assert "BANK_SERVER_TIMEOUT" in reason_names
    assert "UPI_APP_NOT_RESPONDING" in reason_names
    assert "USER_ABORTED" in reason_names

    # Filtered by UPI
    upi_reasons = get_failure_reasons(sample_df, payment_method="UPI")
    assert len(upi_reasons) == 2
    assert all(r["failure_reason"] in ["UPI_APP_NOT_RESPONDING", "BANK_SERVER_TIMEOUT"] for r in upi_reasons)


def test_conversion_by_device(sample_df):
    """Test device-level conversion metrics."""
    devices = get_conversion_by_device(sample_df)
    # Mobile_Android has 6 txns (TXN 1, 2, 6 success = 3; TXN 8, 9 failed = 2; TXN 10 dropped = 1)
    assert devices["Mobile_Android"]["total_attempts"] == 6
    assert devices["Mobile_Android"]["successful_count"] == 3
    assert devices["Mobile_Android"]["conversion_rate_pct"] == 50.0
    assert devices["Mobile_Android"]["dropped_count"] == 1


def test_customer_segments_and_categories(sample_df):
    """Test customer cohort and product category metrics."""
    cust_perf = get_conversion_by_customer_type(sample_df)
    assert "NEW" in cust_perf
    assert "RETURNING" in cust_perf
    assert "VIP" in cust_perf

    cat_perf = get_category_performance(sample_df)
    assert "Fashion" in cat_perf
    assert "Electronics" in cat_perf
    # Fashion has 1 refund out of 2 successful orders -> 50% refund rate
    assert cat_perf["Fashion"]["refund_rate_pct"] == 50.0


def test_revenue_lost_by_failure(sample_df):
    """Test technical vs user failure loss classification."""
    lost = get_revenue_lost_by_failure(sample_df)
    assert lost["total_lost_revenue"] == 6000.0
    assert lost["total_failed_attempts"] == 3
    # Technical failures: BANK_SERVER_TIMEOUT (3000) + UPI_APP_NOT_RESPONDING (1000) = 4000.0
    assert lost["recoverable_technical_loss"] == 4000.0
    assert lost["recoverable_opportunity_estimate"] == 2800.0  # 70% of 4000


def test_top_revenue_leaks(sample_df):
    """Test top revenue leak ranking."""
    leaks = get_top_revenue_leaks(sample_df, limit=2)
    assert len(leaks) == 2
    # The largest leak is UPI + Mobile_Android + BANK_SERVER_TIMEOUT (3000)
    assert leaks[0]["lost_revenue_inr"] == 3000.0
    assert leaks[0]["payment_method"] == "UPI"
    assert leaks[0]["failure_reason"] == "BANK_SERVER_TIMEOUT"


def test_what_if_simulation(sample_df):
    """Test what-if simulation calculation and assumptions."""
    # Current success rate: 70%, 10 attempts, 7 successful, AOV = 2000
    # Simulate improving to 80% (+10% absolute or target 80.0)
    sim = get_what_if_success_rate(target_success_rate=80.0, df=sample_df)
    
    assert sim["current_payment_success_rate_pct"] == 70.0
    assert sim["target_payment_success_rate_pct"] == 80.0
    assert sim["rate_uplift_pct"] == 10.0
    # Target successful: round(10 * 0.8) = 8. Additional txns = 8 - 7 = 1
    assert sim["additional_successful_transactions"] == 1
    # Additional revenue = 1 * 2000.0 = 2000.0
    assert sim["estimated_additional_revenue_inr"] == 2000.0
    assert sim["projected_total_revenue_inr"] == 16000.0
    assert len(sim["assumptions"]) > 0


def test_period_comparison(sample_df):
    """Test comparison between two periods."""
    # Period A: first 5 txns (Rev = 1000+2000+3000+4000+500 = 10500, all 5 success -> 100% success rate)
    # Period B: next 5 txns (Rev = 1500+2000 = 3500, 2 success out of 5 -> 40% success rate)
    df_a = sample_df.iloc[:5]
    df_b = sample_df.iloc[5:]

    comp = get_comparison(df_a, df_b)
    assert "revenue_comparison" in comp
    assert comp["revenue_comparison"]["period_a"] == 10500.0
    assert comp["revenue_comparison"]["period_b"] == 3500.0
    assert comp["revenue_comparison"]["absolute_delta"] == -7000.0
    assert comp["success_rate_comparison"]["period_a"] == 100.0
    assert comp["success_rate_comparison"]["period_b"] == 40.0
    assert comp["success_rate_comparison"]["absolute_delta"] == -60.0


def test_empty_dataframe_handling():
    """Verify robust handling of empty datasets without crashes."""
    empty_df = pd.DataFrame(columns=[
        "transaction_id", "timestamp", "merchant_id", "customer_id",
        "amount", "payment_method", "payment_status", "failure_reason",
        "device_type", "customer_type", "product_category", "refund_status",
        "checkout_step_reached",
    ])

    assert get_total_revenue(empty_df) == 0.0
    assert get_transaction_count(empty_df) == 0
    assert get_payment_success_rate(empty_df) == 0.0
    assert get_payment_failure_rate(empty_df) == 0.0
    assert get_average_order_value(empty_df) == 0.0
    assert get_revenue_by_payment_method(empty_df) == {}
    assert get_failure_reasons(empty_df) == []
    assert get_top_revenue_leaks(empty_df) == []


def test_production_dataset_analytics():
    """Verify that analytics engine runs smoothly against full 15,000-row production dataset."""
    df_prod = load_transaction_data()
    assert len(df_prod) == 15000

    summary = get_business_health_summary(df_prod)
    assert summary["total_transaction_attempts"] == 15000
    assert summary["total_realized_revenue_inr"] > 40_000_000.0
    assert summary["payment_success_rate_pct"] > 70.0
    assert summary["gross_failed_value_inr"] > 8_000_000.0
