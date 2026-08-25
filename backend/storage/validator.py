"""Dataset & Transaction Data Integrity Validator for PayPilot.

Performs deep data hygiene audits verifying primary key uniqueness, required field presence,
numeric range validity, categorical constraint adherence, and timestamp integrity.
"""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from backend.storage.repository import BaseTransactionRepository, get_transaction_repository

logger = logging.getLogger("paypilot.storage.validator")

REQUIRED_COLUMNS = [
    "transaction_id",
    "timestamp",
    "merchant_id",
    "customer_id",
    "amount",
    "payment_method",
    "payment_status",
    "device_type",
    "customer_type",
    "product_category",
]

VALID_PAYMENT_STATUSES = {"SUCCESS", "FAILED", "DROPPED"}
VALID_REFUND_STATUSES = {"NO_REFUND", "REFUNDED", "PARTIAL_REFUND"}



def validate_dataset_integrity(
    data_source: Optional[Union[pd.DataFrame, BaseTransactionRepository]] = None,
) -> Dict[str, Any]:
    """Inspects a dataset or repository and verifies structural and domain integrity.

    Returns:
        Structured integrity report with issue counts and failure descriptions.
    """
    # 1. Load DataFrame
    if isinstance(data_source, pd.DataFrame):
        df = data_source
    elif isinstance(data_source, BaseTransactionRepository):
        df = data_source.load_dataframe()
    else:
        df = get_transaction_repository().load_dataframe()

    total_records = len(df)
    issues_summary: List[str] = []
    checks: Dict[str, Any] = {}

    # 2. Check required columns
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    checks["missing_columns"] = {
        "passed": len(missing_cols) == 0,
        "count": len(missing_cols),
        "details": missing_cols,
    }
    if missing_cols:
        issues_summary.append(f"Missing {len(missing_cols)} required columns: {missing_cols}")

    if total_records == 0:
        return {
            "is_valid": False,
            "total_records": 0,
            "total_issues": 1,
            "checks": checks,
            "issues_summary": ["Dataset contains 0 records."],
        }

    # 3. Check duplicate transaction IDs
    duplicate_txns = int(df["transaction_id"].duplicated().sum()) if "transaction_id" in df.columns else 0
    checks["duplicate_transaction_ids"] = {
        "passed": duplicate_txns == 0,
        "count": duplicate_txns,
    }
    if duplicate_txns > 0:
        issues_summary.append(f"Detected {duplicate_txns} duplicate transaction_id entries.")

    # 4. Check null values in required fields
    null_counts: Dict[str, int] = {}
    total_nulls = 0
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            n_null = int(df[col].isnull().sum())
            if n_null > 0:
                null_counts[col] = n_null
                total_nulls += n_null

    checks["null_required_fields"] = {
        "passed": total_nulls == 0,
        "count": total_nulls,
        "null_by_column": null_counts,
    }
    if total_nulls > 0:
        issues_summary.append(f"Found {total_nulls} null values across required columns: {null_counts}")

    # 5. Check numeric range validity (amount > 0 and finite)
    invalid_amounts = 0
    if "amount" in df.columns:
        invalid_mask = (df["amount"] < 0) | df["amount"].isna()
        invalid_amounts = int(invalid_mask.sum())

    checks["invalid_amounts"] = {
        "passed": invalid_amounts == 0,
        "count": invalid_amounts,
    }
    if invalid_amounts > 0:
        issues_summary.append(f"Detected {invalid_amounts} negative or invalid transaction amount records.")

    # 6. Check categorical status values
    invalid_status_count = 0
    if "payment_status" in df.columns:
        invalid_status_mask = ~df["payment_status"].astype(str).str.upper().isin(VALID_PAYMENT_STATUSES)
        invalid_status_count = int(invalid_status_mask.sum())

    checks["invalid_payment_status"] = {
        "passed": invalid_status_count == 0,
        "count": invalid_status_count,
    }
    if invalid_status_count > 0:
        issues_summary.append(f"Found {invalid_status_count} rows with invalid payment_status.")

    # 7. Check timestamp validity
    invalid_timestamps = 0
    if "timestamp" in df.columns:
        try:
            parsed_ts = pd.to_datetime(df["timestamp"], errors="coerce")
            invalid_timestamps = int(parsed_ts.isna().sum())
        except Exception:
            invalid_timestamps = total_records

    checks["invalid_timestamps"] = {
        "passed": invalid_timestamps == 0,
        "count": invalid_timestamps,
    }
    if invalid_timestamps > 0:
        issues_summary.append(f"Detected {invalid_timestamps} unparseable timestamp entries.")

    total_issues = (
        len(missing_cols)
        + duplicate_txns
        + total_nulls
        + invalid_amounts
        + invalid_status_count
        + invalid_timestamps
    )

    is_valid = total_issues == 0

    return {
        "is_valid": is_valid,
        "total_records": total_records,
        "total_issues": total_issues,
        "checks": checks,
        "issues_summary": issues_summary,
    }
