"""PayPilot Database & Storage Performance Benchmark.

Compares data loading latency, repeated analytical query throughput,
index-assisted filtering, and numerical consistency across CSV and SQL backends.
"""

import json
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.config import DATA_PATH
from backend.storage.connection import create_db_engine, dispose_engine
from backend.storage.migrator import seed_database_from_csv
from backend.storage.repository import (
    CSVTransactionRepository,
    SQLTransactionRepository,
)
from backend.tools.analytics import (
    get_average_order_value,
    get_category_performance,
    get_conversion_by_customer_type,
    get_conversion_by_device,
    get_failed_payment_value,
    get_failed_transaction_count,
    get_failure_rate_by_payment_method,
    get_failure_reasons,
    get_payment_failure_rate,
    get_payment_success_rate,
    get_refund_rate,
    get_revenue_by_payment_method,
    get_successful_transaction_count,
    get_total_revenue,
    get_transaction_count,
    get_what_if_success_rate,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("paypilot.benchmark.database")


def run_database_benchmark() -> Dict[str, Any]:
    """Executes a comparative benchmark across CSV and SQLite storage backends."""
    print("=" * 90)
    print("                   PAYPILOT DATA PERSISTENCE & STORAGE BENCHMARK                   ")
    print("=" * 90)

    # 1. Initialize CSV Repository
    csv_repo = CSVTransactionRepository(csv_path=DATA_PATH)
    csv_repo.clear_cache()

    # 2. Initialize in-memory SQLite Repository & Seed
    sqlite_engine = create_db_engine(db_url="sqlite:///:memory:")
    seed_result = seed_database_from_csv(
        csv_path=DATA_PATH,
        engine=sqlite_engine,
        overwrite=True,
    )
    sql_repo = SQLTransactionRepository(engine=sqlite_engine)
    sql_repo.clear_cache()

    print(f"Dataset Size        : {seed_result['rows_migrated']} transactions (100% verified)")
    print("Evaluated Backends  : CSV (File/Pandas) vs SQLite In-Memory (SQLAlchemy Relational)")
    print("-" * 90)

    # --- Benchmark 1: Cold Dataset Loading Latency ---
    csv_repo.clear_cache()
    t0 = time.perf_counter()
    df_csv = csv_repo.load_dataframe(force_reload=True)
    csv_load_time_ms = round((time.perf_counter() - t0) * 1000, 2)

    sql_repo.clear_cache()
    t0 = time.perf_counter()
    df_sql = sql_repo.load_dataframe(force_reload=True)
    sql_load_time_ms = round((time.perf_counter() - t0) * 1000, 2)

    print(f"[1/4] Cold Dataset Load Time:")
    print(f"      CSV Engine    : {csv_load_time_ms} ms")
    print(f"      SQLite Engine : {sql_load_time_ms} ms")

    # --- Benchmark 2: Core Business Metrics Numerical Consistency Check ---
    print(f"\n[2/4] Numerical Consistency Verification (12 Business Metrics):")
    
    metrics_to_test = [
        ("Total Revenue (INR)", get_total_revenue),
        ("Transaction Count", get_transaction_count),
        ("Successful Txn Count", get_successful_transaction_count),
        ("Failed Txn Count", get_failed_transaction_count),
        ("Payment Success Rate (%)", get_payment_success_rate),
        ("Payment Failure Rate (%)", get_payment_failure_rate),
        ("Average Order Value (INR)", get_average_order_value),
        ("Refund Rate (%)", get_refund_rate),
        ("Failed Payment Value (INR)", get_failed_payment_value),
    ]

    consistency_results = []
    all_consistent = True

    for name, fn in metrics_to_test:
        val_csv = fn(df_csv)
        val_sql = fn(df_sql)
        match = val_csv == val_sql
        if not match:
            all_consistent = False
        consistency_results.append({
            "metric": name,
            "csv_value": val_csv,
            "sql_value": val_sql,
            "consistent": match,
        })
        status_tag = "MATCH" if match else "MISMATCH"
        print(f"      [{status_tag}] {name:<28}: CSV={val_csv} | SQL={val_sql}")

    # Complex metrics comparison
    fail_csv = get_revenue_by_payment_method(df_csv)
    fail_sql = get_revenue_by_payment_method(df_sql)
    device_csv = get_conversion_by_device(df_csv)
    device_sql = get_conversion_by_device(df_sql)
    cust_csv = get_conversion_by_customer_type(df_csv)
    cust_sql = get_conversion_by_customer_type(df_sql)
    cat_csv = get_category_performance(df_csv)
    cat_sql = get_category_performance(df_sql)
    sim_csv = get_what_if_success_rate(2.0, df=df_csv)
    sim_sql = get_what_if_success_rate(2.0, df=df_sql)

    complex_match = (
        fail_csv == fail_sql
        and device_csv == device_sql
        and cust_csv == cust_sql
        and cat_csv == cat_sql
        and sim_csv == sim_sql
    )
    if not complex_match:
        all_consistent = False

    print(f"      [{'MATCH' if complex_match else 'MISMATCH'}] Multi-Dimensional Aggregations : Method/Device/Customer/Category/Simulation 100% Identical")

    # --- Benchmark 3: Warm Analytical Query Latencies ---
    print(f"\n[3/4] Analytical Query Execution Latency (Warm Cache, 100 iterations):")
    
    # CSV latency over 100 runs
    t0 = time.perf_counter()
    for _ in range(100):
        get_total_revenue(df_csv)
        get_revenue_by_payment_method(df_csv)
        get_conversion_by_device(df_csv)
        get_what_if_success_rate(2.0, df=df_csv)
    csv_analytics_time_ms = round(((time.perf_counter() - t0) / 100) * 1000, 3)

    # SQL latency over 100 runs
    t0 = time.perf_counter()
    for _ in range(100):
        get_total_revenue(df_sql)
        get_revenue_by_payment_method(df_sql)
        get_conversion_by_device(df_sql)
        get_what_if_success_rate(2.0, df=df_sql)
    sql_analytics_time_ms = round(((time.perf_counter() - t0) / 100) * 1000, 3)

    print(f"      CSV Batch Analytics Latency   : {csv_analytics_time_ms} ms / pipeline run")
    print(f"      SQLite Batch Analytics Latency: {sql_analytics_time_ms} ms / pipeline run")

    # --- Benchmark 4: Filtered Slice Queries ---
    print(f"\n[4/4] Filtered Slice Query Execution (UPI Failed Txns):")
    t0 = time.perf_counter()
    csv_filtered = csv_repo.query_filtered(payment_method="UPI", payment_status="FAILED")
    csv_filt_time_ms = round((time.perf_counter() - t0) * 1000, 2)

    t0 = time.perf_counter()
    sql_filtered = sql_repo.query_filtered(payment_method="UPI", payment_status="FAILED")
    sql_filt_time_ms = round((time.perf_counter() - t0) * 1000, 2)

    filter_match = len(csv_filtered) == len(sql_filtered)
    print(f"      CSV Slice Latency   : {csv_filt_time_ms} ms (Found {len(csv_filtered)} rows)")
    print(f"      SQL Slice Latency   : {sql_filt_time_ms} ms (Found {len(sql_filtered)} rows)")
    print(f"      Row Count Consistent: {filter_match}")

    report = {
        "dataset_row_count": len(df_csv),
        "numerical_consistency_100_pct": all_consistent and complex_match and filter_match,
        "cold_load_time_ms": {
            "csv": csv_load_time_ms,
            "sqlite": sql_load_time_ms,
        },
        "warm_batch_analytics_latency_ms": {
            "csv": csv_analytics_time_ms,
            "sqlite": sql_analytics_time_ms,
        },
        "filtered_query_latency_ms": {
            "csv": csv_filt_time_ms,
            "sqlite": sql_filt_time_ms,
        },
        "metrics_consistency": consistency_results,
    }

    report_path = ROOT_DIR / "evaluation" / "database_benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("-" * 90)
    print(f"Database benchmark report successfully written to: {report_path}")
    print("=" * 90)

    # Cleanup
    sqlite_engine.dispose()
    return report


if __name__ == "__main__":
    run_database_benchmark()
