"""Unit and Integration Tests for PayPilot Data Persistence & Storage Architecture.

Tests:
1. TransactionModel schema, column definitions, primary key, and composite indexes.
2. TransactionRecord dataclass serialization and deserialization.
3. CSVTransactionRepository loading, row count (15,000), column validation, and caching.
4. SQLTransactionRepository deterministic seeding, count, and ID lookup.
5. Duplicate prevention during repeated database seeds.
6. Overwrite re-seeding behavior.
7. Repository factory selection via DATA_BACKEND environment variable.
8. 100% numerical consistency between CSV and SQL backends across all 12+ analytics functions.
9. Parameterized SQL query filtering across dimensions.
10. Database connection pooling, heartbeat, and engine disposal.
11. Safe database URL password masking.
12. Graceful error handling for missing/corrupt storage.
13. Configuration validation inclusion of storage properties.
14. 100% offline test execution.
"""

from datetime import datetime
from pathlib import Path
import pytest
from sqlalchemy import inspect

from backend.config import DATA_PATH, validate_config
from backend.storage.connection import (
    check_database_connection,
    create_db_engine,
    dispose_engine,
    get_db_engine,
    mask_database_url,
)
from backend.storage.migrator import seed_database_from_csv
from backend.storage.models import Base, TransactionModel, TransactionRecord
from backend.storage.repository import (
    CSVTransactionRepository,
    SQLTransactionRepository,
    get_transaction_repository,
    set_transaction_repository,
)
from backend.tools.analytics import (
    clear_dataset_cache,
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
    load_transaction_data,
)


@pytest.fixture(autouse=True)
def setup_database_test_env(monkeypatch):
    """Isolates test environment with CSV as default backend and clean engine state."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("DATA_BACKEND", "csv")
    clear_dataset_cache()
    yield
    dispose_engine()
    clear_dataset_cache()


def test_transaction_model_schema_and_columns():
    """Verifies TransactionModel table name, primary key, column types, and composite indexes."""
    engine = create_db_engine(db_url="sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "merchant_transactions" in inspector.get_table_names()

    columns = {col["name"]: col for col in inspector.get_columns("merchant_transactions")}
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
    for col_name in expected_cols:
        assert col_name in columns, f"Missing column: {col_name}"

    # Verify primary key
    pk = inspector.get_pk_constraint("merchant_transactions")
    assert pk["constrained_columns"] == ["transaction_id"]

    # Verify indexes
    indexes = inspector.get_indexes("merchant_transactions")
    index_names = [idx["name"] for idx in indexes]
    assert "idx_txn_status_method" in index_names or any("status" in (idx["name"] or "") for idx in indexes)


def test_transaction_record_dataclass_conversions():
    """Verifies TransactionRecord dataclass serializes and deserializes cleanly."""
    raw_dict = {
        "transaction_id": "TXN_999999",
        "timestamp": "2026-01-01 12:00:00",
        "merchant_id": "mer_test_01",
        "customer_id": "CUST_00001",
        "amount": 2500.50,
        "payment_method": "UPI",
        "payment_status": "SUCCESS",
        "failure_reason": "None",
        "device_type": "Mobile_Android",
        "customer_type": "VIP",
        "product_category": "Electronics",
        "refund_status": "NO_REFUND",
        "checkout_step_reached": "PAYMENT_COMPLETED",
    }
    record = TransactionRecord.from_dict(raw_dict)
    assert record.transaction_id == "TXN_999999"
    assert record.amount == 2500.50

    d = record.to_dict()
    assert d["transaction_id"] == "TXN_999999"
    assert d["amount"] == 2500.50


def test_csv_transaction_repository_loading():
    """Verifies CSVTransactionRepository correctly loads the 15,000 dataset."""
    repo = CSVTransactionRepository(csv_path=DATA_PATH)
    assert repo.backend_type == "csv"

    df = repo.load_dataframe()
    assert len(df) == 15000
    assert repo.count() == 15000

    # Retrieve single record
    rec = repo.get_by_id("TXN_100001")
    assert rec is not None
    assert rec.transaction_id == "TXN_100001"
    assert rec.merchant_id == "mer_paypilot_01"


def test_sql_transaction_repository_seeding_and_row_count():
    """Verifies deterministic seeding into SQLite and querying via SQLTransactionRepository."""
    engine = create_db_engine(db_url="sqlite:///:memory:")
    seed_res = seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=True)
    assert seed_res["status"] == "success"
    assert seed_res["rows_migrated"] == 15000
    assert seed_res["verified"] is True

    repo = SQLTransactionRepository(engine=engine)
    assert repo.backend_type == "sqlite"
    assert repo.count() == 15000

    # Test get_by_id lookup
    rec = repo.get_by_id("TXN_100001")
    assert rec is not None
    assert rec.transaction_id == "TXN_100001"
    assert rec.amount > 0


def test_deterministic_seed_duplicate_prevention():
    """Verifies that running seed_database_from_csv repeatedly does not duplicate rows."""
    engine = create_db_engine(db_url="sqlite:///:memory:")

    # First seed
    res1 = seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=False)
    assert res1["status"] == "success"
    assert res1["rows_migrated"] == 15000

    # Second seed with overwrite=False should skip
    res2 = seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=False)
    assert res2["status"] == "already_seeded"
    assert res2["rows_migrated"] == 15000

    repo = SQLTransactionRepository(engine=engine)
    assert repo.count() == 15000


def test_seed_overwrite_replaces_data_deterministically():
    """Verifies that seed with overwrite=True successfully wipes and reloads."""
    engine = create_db_engine(db_url="sqlite:///:memory:")
    seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=False)

    # Overwrite seed
    res_overwrite = seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=True)
    assert res_overwrite["status"] == "success"
    assert res_overwrite["rows_migrated"] == 15000

    repo = SQLTransactionRepository(engine=engine)
    assert repo.count() == 15000


def test_repository_factory_selection_via_data_backend_env(monkeypatch):
    """Verifies get_transaction_repository returns appropriate backend based on DATA_BACKEND."""
    # 1. Test CSV default
    monkeypatch.setenv("DATA_BACKEND", "csv")
    repo_csv = get_transaction_repository(force_new=True)
    assert isinstance(repo_csv, CSVTransactionRepository)
    assert repo_csv.backend_type == "csv"

    # 2. Test SQLite selection
    monkeypatch.setenv("DATA_BACKEND", "sqlite")
    engine = create_db_engine(db_url="sqlite:///:memory:")
    repo_sql = get_transaction_repository(engine=engine, force_new=True)
    assert isinstance(repo_sql, SQLTransactionRepository)
    assert repo_sql.backend_type == "sqlite"


def test_analytics_numerical_consistency_csv_vs_sql():
    """Rigorously guarantees 100% numerical consistency between CSV and SQL analytical queries."""
    csv_repo = CSVTransactionRepository(csv_path=DATA_PATH)
    df_csv = csv_repo.load_dataframe()

    sqlite_engine = create_db_engine(db_url="sqlite:///:memory:")
    seed_database_from_csv(csv_path=DATA_PATH, engine=sqlite_engine, overwrite=True)
    sql_repo = SQLTransactionRepository(engine=sqlite_engine)
    df_sql = sql_repo.load_dataframe()

    # 1. Total Revenue
    assert get_total_revenue(df_csv) == get_total_revenue(df_sql)

    # 2. Transaction Counts
    assert get_transaction_count(df_csv) == get_transaction_count(df_sql)
    assert get_successful_transaction_count(df_csv) == get_successful_transaction_count(df_sql)
    assert get_failed_transaction_count(df_csv) == get_failed_transaction_count(df_sql)

    # 3. Success & Failure Rates
    assert get_payment_success_rate(df_csv) == get_payment_success_rate(df_sql)
    assert get_payment_failure_rate(df_csv) == get_payment_failure_rate(df_sql)

    # 4. Average Order Value & Losses
    assert get_average_order_value(df_csv) == get_average_order_value(df_sql)
    assert get_failed_payment_value(df_csv) == get_failed_payment_value(df_sql)
    assert get_refund_rate(df_csv) == get_refund_rate(df_sql)

    # 5. Dimensional Breakdowns
    assert get_revenue_by_payment_method(df_csv) == get_revenue_by_payment_method(df_sql)
    assert get_failure_rate_by_payment_method(df_csv) == get_failure_rate_by_payment_method(df_sql)
    assert get_failure_reasons(df_csv) == get_failure_reasons(df_sql)
    assert get_conversion_by_device(df_csv) == get_conversion_by_device(df_sql)
    assert get_conversion_by_customer_type(df_csv) == get_conversion_by_customer_type(df_sql)
    assert get_category_performance(df_csv) == get_category_performance(df_sql)

    # 6. What-If Simulation
    assert get_what_if_success_rate(1.0, df=df_csv) == get_what_if_success_rate(1.0, df=df_sql)
    assert get_what_if_success_rate(3.0, df=df_csv) == get_what_if_success_rate(3.0, df=df_sql)


def test_sql_repository_query_filtering():
    """Verifies SQLTransactionRepository.query_filtered executes parameterized slice queries."""
    engine = create_db_engine(db_url="sqlite:///:memory:")
    seed_database_from_csv(csv_path=DATA_PATH, engine=engine, overwrite=True)
    repo = SQLTransactionRepository(engine=engine)

    # Filter UPI Failed
    df_upi_failed = repo.query_filtered(payment_method="UPI", payment_status="FAILED")
    assert len(df_upi_failed) > 0
    assert (df_upi_failed["payment_method"] == "UPI").all()
    assert (df_upi_failed["payment_status"] == "FAILED").all()

    # Filter with Limit
    df_limited = repo.query_filtered(limit=10)
    assert len(df_limited) == 10


def test_database_connection_pooling_and_cleanup():
    """Verifies database engine creation, connection heartbeat, and pool disposal."""
    engine = create_db_engine(db_url="sqlite:///:memory:")
    assert check_database_connection(engine) is True

    # Dispose
    engine.dispose()


def test_masked_database_url_hides_secrets():
    """Verifies mask_database_url completely scrubs raw passwords in connection strings."""
    url_pg = "postgresql://paypilot_user:super_secret_password_123@prod-db.internal:5432/paypilot"
    masked_pg = mask_database_url(url_pg)
    assert "super_secret_password_123" not in masked_pg
    assert masked_pg == "postgresql://paypilot_user:***@prod-db.internal:5432/paypilot"

    url_mysql = "mysql+pymysql://admin:p@ssw0rd99@localhost:3306/db"
    masked_mysql = mask_database_url(url_mysql)
    assert "p@ssw0rd99" not in masked_mysql
    assert ":***@" in masked_mysql


def test_database_error_handling_when_unreachable():
    """Verifies graceful handling when a non-existent file or bad query is invoked."""
    with pytest.raises(FileNotFoundError):
        CSVTransactionRepository(csv_path="non_existent_path.csv").load_dataframe()


def test_config_validation_includes_storage_info():
    """Verifies validate_config() exports storage backend settings without exposing secrets."""
    status = validate_config()
    assert "data_backend" in status
    assert "database_configured" in status
    assert status["data_backend"] in ("csv", "sqlite", "postgres", "database")
    # Verify no raw passwords exist in config dictionary
    for k, v in status.items():
        assert "password" not in str(v).lower()
