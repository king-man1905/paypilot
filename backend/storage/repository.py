"""Transaction Repository Pattern Implementation for PayPilot.

Decouples deterministic business analytics from physical storage technologies.
Provides unified data access whether backed by CSV files or relational SQL databases.
"""

import abc
import logging
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional, Union
import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.config import (
    DATA_BACKEND,
    DATA_PATH,
    get_data_backend,
)
from backend.storage.connection import get_db_engine, get_db_session
from backend.storage.models import Base, TransactionModel, TransactionRecord

logger = logging.getLogger("paypilot.storage.repository")

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


class BaseTransactionRepository(abc.ABC):
    """Abstract interface defining transaction data retrieval and query operations."""

    @abc.abstractmethod
    def load_dataframe(self, force_reload: bool = False) -> pd.DataFrame:
        """Loads and returns the complete validated DataFrame of transactions."""
        pass

    @abc.abstractmethod
    def count(self) -> int:
        """Returns the total number of transactions in the dataset."""
        pass

    @abc.abstractmethod
    def get_by_id(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Retrieves a single transaction by its unique ID."""
        pass

    @abc.abstractmethod
    def query_filtered(
        self,
        payment_method: Optional[str] = None,
        payment_status: Optional[str] = None,
        device_type: Optional[str] = None,
        product_category: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Returns a filtered slice of transactions matching specified dimensions."""
        pass

    @abc.abstractmethod
    def clear_cache(self) -> None:
        """Clears any in-memory DataFrame cache."""
        pass

    @property
    @abc.abstractmethod
    def backend_type(self) -> str:
        """Returns the storage backend identifier string."""
        pass


class CSVTransactionRepository(BaseTransactionRepository):
    """CSV-backed transaction repository with thread-safe in-memory caching."""

    def __init__(self, csv_path: Optional[Union[str, Path]] = None) -> None:
        self.csv_path = Path(csv_path) if csv_path else Path(DATA_PATH)
        self._cached_df: Optional[pd.DataFrame] = None
        self._lock = threading.Lock()

    @property
    def backend_type(self) -> str:
        return "csv"

    def clear_cache(self) -> None:
        with self._lock:
            self._cached_df = None

    def load_dataframe(self, force_reload: bool = False) -> pd.DataFrame:
        if not force_reload and self._cached_df is not None:
            return self._cached_df

        if not self.csv_path.exists():
            raise FileNotFoundError(f"Transaction CSV not found at: {self.csv_path}")

        with self._lock:
            if not force_reload and self._cached_df is not None:
                return self._cached_df

            df = pd.read_csv(self.csv_path)

            if df.empty:
                raise ValueError(f"Transaction dataset at {self.csv_path} is empty.")

            missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                raise ValueError(f"Dataset is missing required columns: {missing}")

            # Clean and normalize types
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df["payment_status"] = df["payment_status"].astype(str).str.strip().str.upper()
            df["payment_method"] = df["payment_method"].astype(str).str.strip()
            df["device_type"] = df["device_type"].astype(str).str.strip()
            df["customer_type"] = df["customer_type"].astype(str).str.strip().str.upper()
            df["product_category"] = df["product_category"].astype(str).str.strip()
            df["failure_reason"] = df["failure_reason"].fillna("None").astype(str).str.strip()
            df["refund_status"] = df["refund_status"].fillna("NO_REFUND").astype(str).str.strip().str.upper()
            df["checkout_step_reached"] = df["checkout_step_reached"].fillna("PAYMENT_COMPLETED").astype(str).str.strip()

            self._cached_df = df
            return df

    def count(self) -> int:
        df = self.load_dataframe()
        return len(df)

    def get_by_id(self, transaction_id: str) -> Optional[TransactionRecord]:
        df = self.load_dataframe()
        matches = df[df["transaction_id"] == transaction_id]
        if matches.empty:
            return None
        row = matches.iloc[0].to_dict()
        return TransactionRecord.from_dict(row)

    def query_filtered(
        self,
        payment_method: Optional[str] = None,
        payment_status: Optional[str] = None,
        device_type: Optional[str] = None,
        product_category: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        df = self.load_dataframe()
        filtered = df

        if payment_method:
            filtered = filtered[filtered["payment_method"] == payment_method]
        if payment_status:
            filtered = filtered[filtered["payment_status"] == payment_status.upper()]
        if device_type:
            filtered = filtered[filtered["device_type"] == device_type]
        if product_category:
            filtered = filtered[filtered["product_category"] == product_category]

        if limit is not None and limit > 0:
            filtered = filtered.head(limit)

        return filtered.copy()


class SQLTransactionRepository(BaseTransactionRepository):
    """Relational SQL-backed transaction repository (SQLite / PostgreSQL)."""

    def __init__(
        self,
        engine=None,
        db_url: Optional[str] = None,
        database_url: Optional[str] = None,
    ) -> None:
        target_url = db_url or database_url
        self._engine = engine or get_db_engine(db_url=target_url)
        self._cached_df: Optional[pd.DataFrame] = None
        self._lock = threading.Lock()
        self._ensure_schema()


    def _ensure_schema(self) -> None:
        """Ensures table schemas and indexes exist in the target database."""
        try:
            Base.metadata.create_all(self._engine)
        except Exception as e:
            logger.warning(f"Notice ensuring SQL schema: {e}")

    @property
    def backend_type(self) -> str:
        url_str = str(self._engine.url)
        return "sqlite" if "sqlite" in url_str else "postgres"

    def clear_cache(self) -> None:
        with self._lock:
            self._cached_df = None

    def load_dataframe(self, force_reload: bool = False) -> pd.DataFrame:
        if not force_reload and self._cached_df is not None:
            return self._cached_df

        with self._lock:
            if not force_reload and self._cached_df is not None:
                return self._cached_df

            query = "SELECT * FROM merchant_transactions ORDER BY timestamp ASC"
            with self._engine.connect() as conn:
                df = pd.read_sql_query(sql=text(query), con=conn)

            if df.empty:
                logger.warning("SQL merchant_transactions table is empty.")
                return df

            # Parse and clean types to guarantee identical pandas behavior
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df["payment_status"] = df["payment_status"].astype(str).str.strip().str.upper()
            df["payment_method"] = df["payment_method"].astype(str).str.strip()
            df["device_type"] = df["device_type"].astype(str).str.strip()
            df["customer_type"] = df["customer_type"].astype(str).str.strip().str.upper()
            df["product_category"] = df["product_category"].astype(str).str.strip()
            df["failure_reason"] = df["failure_reason"].fillna("None").astype(str).str.strip()
            df["refund_status"] = df["refund_status"].fillna("NO_REFUND").astype(str).str.strip().str.upper()
            df["checkout_step_reached"] = df["checkout_step_reached"].fillna("PAYMENT_COMPLETED").astype(str).str.strip()

            self._cached_df = df
            return df

    def count(self) -> int:
        with self._engine.connect() as conn:
            res = conn.execute(text("SELECT COUNT(*) FROM merchant_transactions"))
            row = res.fetchone()
            return int(row[0]) if row else 0

    def get_by_id(self, transaction_id: str) -> Optional[TransactionRecord]:
        session = Session(bind=self._engine)
        try:
            model = session.get(TransactionModel, transaction_id)
            if not model:
                return None
            return TransactionRecord.from_dict(model.to_dict())
        finally:
            session.close()

    def query_filtered(
        self,
        payment_method: Optional[str] = None,
        payment_status: Optional[str] = None,
        device_type: Optional[str] = None,
        product_category: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        clauses = []
        params = {}

        if payment_method:
            clauses.append("payment_method = :payment_method")
            params["payment_method"] = payment_method
        if payment_status:
            clauses.append("payment_status = :payment_status")
            params["payment_status"] = payment_status.upper()
        if device_type:
            clauses.append("device_type = :device_type")
            params["device_type"] = device_type
        if product_category:
            clauses.append("product_category = :product_category")
            params["product_category"] = product_category

        where_str = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit_str = f"LIMIT {int(limit)}" if limit else ""
        query = f"SELECT * FROM merchant_transactions {where_str} ORDER BY timestamp ASC {limit_str}"

        with self._engine.connect() as conn:
            df = pd.read_sql_query(sql=text(query), con=conn, params=params)

        if not df.empty:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

        return df


# Singleton repository manager
_REPO_LOCK = threading.Lock()
_GLOBAL_REPOSITORY: Optional[BaseTransactionRepository] = None


def reset_transaction_repository() -> None:
    """Resets the singleton repository instance (used for test isolation)."""
    global _GLOBAL_REPOSITORY
    with _REPO_LOCK:
        if _GLOBAL_REPOSITORY is not None:
            _GLOBAL_REPOSITORY.clear_cache()
        _GLOBAL_REPOSITORY = None


def get_transaction_repository(
    backend: Optional[str] = None,
    csv_path: Optional[Union[str, Path]] = None,
    engine=None,
    force_new: bool = False,
) -> BaseTransactionRepository:
    """Factory accessing or initializing the singleton TransactionRepository."""
    global _GLOBAL_REPOSITORY
    target_backend = (backend or get_data_backend() or DATA_BACKEND).strip().lower()
    is_sql = target_backend in ("sqlite", "postgres", "postgresql", "database", "sql")

    with _REPO_LOCK:
        if _GLOBAL_REPOSITORY is not None and not force_new:
            current_is_sql = _GLOBAL_REPOSITORY.backend_type in ("sqlite", "postgres")
            if current_is_sql == is_sql:
                return _GLOBAL_REPOSITORY

        if is_sql:
            _GLOBAL_REPOSITORY = SQLTransactionRepository(engine=engine)
        else:
            _GLOBAL_REPOSITORY = CSVTransactionRepository(csv_path=csv_path)

        return _GLOBAL_REPOSITORY


def set_transaction_repository(repo: BaseTransactionRepository) -> None:
    """Explicitly sets the active repository instance (for testing/mocking)."""
    global _GLOBAL_REPOSITORY
    with _REPO_LOCK:
        _GLOBAL_REPOSITORY = repo
