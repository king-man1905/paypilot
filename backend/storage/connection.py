"""Database Connection & Connection Pool Management for PayPilot.

Configures thread-safe SQLAlchemy database engines with connection pooling,
pre-ping health checks, credential masking, and lifecycle disposal.
"""

import logging
import re
import threading
from typing import Optional
from sqlalchemy import create_engine, Engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool, QueuePool, StaticPool

from backend.config import (
    DATABASE_URL,
    DB_MAX_OVERFLOW,
    DB_POOL_PRE_PING,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
    get_database_url,
)

logger = logging.getLogger("paypilot.storage.connection")

_ENGINE_LOCK = threading.Lock()
_GLOBAL_ENGINE: Optional[Engine] = None
_GLOBAL_SESSION_FACTORY: Optional[sessionmaker] = None


def mask_database_url(db_url: str) -> str:
    """Masks database passwords in connection URLs for safe logging and metrics.

    Example:
        'postgresql://admin:supersecret@localhost:5432/paypilot'
        -> 'postgresql://admin:***@localhost:5432/paypilot'
    """
    if not db_url:
        return ""
    # Regex to match :password@ in connection URLs
    return re.sub(r":([^/@]+)@", r":***@", db_url)


def create_db_engine(
    db_url: Optional[str] = None,
    pool_size: Optional[int] = None,
    max_overflow: Optional[int] = None,
    timeout: Optional[float] = None,
    pre_ping: Optional[bool] = None,
) -> Engine:
    """Creates a configured SQLAlchemy Engine instance with appropriate pooling."""
    target_url = db_url or get_database_url() or DATABASE_URL
    masked_url = mask_database_url(target_url)

    logger.info(f"Initializing database engine for: {masked_url}")

    if target_url.startswith("sqlite"):
        # SQLite connection handling
        if ":memory:" in target_url:
            engine = create_engine(
                target_url,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            engine = create_engine(
                target_url,
                connect_args={"check_same_thread": False},
                poolclass=NullPool,
            )
    else:
        # PostgreSQL / Relational production pooling
        p_size = pool_size if pool_size is not None else DB_POOL_SIZE
        m_overflow = max_overflow if max_overflow is not None else DB_MAX_OVERFLOW
        p_timeout = timeout if timeout is not None else DB_POOL_TIMEOUT
        p_pre_ping = pre_ping if pre_ping is not None else DB_POOL_PRE_PING

        engine = create_engine(
            target_url,
            poolclass=QueuePool,
            pool_size=p_size,
            max_overflow=m_overflow,
            pool_timeout=p_timeout,
            pool_pre_ping=p_pre_ping,
        )

    return engine


def get_db_engine(force_new: bool = False, db_url: Optional[str] = None) -> Engine:
    """Singleton accessor for active database Engine."""
    global _GLOBAL_ENGINE, _GLOBAL_SESSION_FACTORY
    with _ENGINE_LOCK:
        if _GLOBAL_ENGINE is not None and not force_new and db_url is None:
            return _GLOBAL_ENGINE

        if _GLOBAL_ENGINE is not None:
            try:
                _GLOBAL_ENGINE.dispose()
            except Exception as e:
                logger.warning(f"Notice disposing previous engine: {e}")

        _GLOBAL_ENGINE = create_db_engine(db_url=db_url)
        _GLOBAL_SESSION_FACTORY = sessionmaker(bind=_GLOBAL_ENGINE, autocommit=False, autoflush=False)
        return _GLOBAL_ENGINE


def get_db_session() -> Session:
    """Returns a new SQLAlchemy Session attached to the global engine."""
    global _GLOBAL_SESSION_FACTORY
    if _GLOBAL_SESSION_FACTORY is None:
        get_db_engine()
    if _GLOBAL_SESSION_FACTORY is None:
        raise RuntimeError("Database session factory failed to initialize.")
    return _GLOBAL_SESSION_FACTORY()


def dispose_engine() -> None:
    """Disposes active database engine connection pools."""
    global _GLOBAL_ENGINE, _GLOBAL_SESSION_FACTORY
    with _ENGINE_LOCK:
        if _GLOBAL_ENGINE is not None:
            try:
                _GLOBAL_ENGINE.dispose()
            except Exception as e:
                logger.warning(f"Error during engine disposal: {e}")
            _GLOBAL_ENGINE = None
            _GLOBAL_SESSION_FACTORY = None


def check_database_connection(engine: Optional[Engine] = None) -> bool:
    """Executes a lightweight heartbeat query (SELECT 1) to test database reachability."""
    eng = engine or get_db_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"Database heartbeat failed: {type(e).__name__}: {e}")
        return False
