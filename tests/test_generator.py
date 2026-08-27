"""Unit tests for synthetic dataset generator."""

import pytest
import pandas as pd
from data.generator import generate_synthetic_transactions


def test_dataset_generation_deterministic():
    """Verify that dataset generation with same seed produces identical results."""
    df1 = generate_synthetic_transactions(num_records=500, seed=42)
    df2 = generate_synthetic_transactions(num_records=500, seed=42)

    pd.testing.assert_frame_equal(df1, df2)


def test_dataset_schema_and_integrity():
    """Verify all required columns, types, and value constraints exist."""
    df = generate_synthetic_transactions(num_records=1000, seed=42)

    expected_cols = [
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

    for col in expected_cols:
        assert col in df.columns, f"Missing expected column: {col}"

    assert len(df) == 1000
    assert (df["amount"] > 0).all()
    assert set(df["payment_status"].unique()).issubset({"SUCCESS", "FAILED", "DROPPED"})
    assert set(df["refund_status"].unique()).issubset({"NO_REFUND", "FULL_REFUND", "PARTIAL_REFUND"})
    assert set(df["customer_type"].unique()).issubset({"NEW", "RETURNING", "VIP"})


def test_injected_leakage_patterns():
    """Verify that synthetic dataset contains the injected business problems."""
    df = generate_synthetic_transactions(num_records=5000, seed=42)

    # Fashion refund rate should be significantly higher than overall non-fashion
    fashion_refund_rate = (df[df["product_category"] == "Fashion"]["refund_status"] != "NO_REFUND").mean()
    non_fashion_refund_rate = (df[df["product_category"] != "Fashion"]["refund_status"] != "NO_REFUND").mean()
    assert fashion_refund_rate > non_fashion_refund_rate * 2

    # UPI transactions exist and have valid failure reasons
    upi_failed = df[(df["payment_method"] == "UPI") & (df["payment_status"] == "FAILED")]
    assert len(upi_failed) > 0
    assert "UPI_APP_NOT_RESPONDING" in list(upi_failed["failure_reason"])
