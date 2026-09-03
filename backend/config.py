"""PayPilot Production Configuration & Secrets Management Module (Phase 20).

Provides a single authoritative, strongly typed, environment-profiled configuration system
with secret classification, zero-leakage redaction, cross-backend compatibility validation,
and fail-fast startup checks.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, Generator, List, Optional, Set, Union
from dotenv import dotenv_values, load_dotenv

logger = logging.getLogger("paypilot.config")

# Configuration Schema Version
CONFIG_SCHEMA_VERSION: int = 1

# Paths
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
ENV_PATH: Path = ROOT_DIR / ".env"

# Load local .env if present (non-empty .env values override empty env placeholders)
if ENV_PATH.exists():
    file_vars = dotenv_values(ENV_PATH)
    for k, v in file_vars.items():
        if v is not None and v.strip() != "":
            os.environ[k] = v.strip()
    load_dotenv(ENV_PATH)

DATA_DIR: Path = ROOT_DIR / "data"
RAW_DATA_DIR: Path = DATA_DIR / "raw"
PROCESSED_DATA_DIR: Path = DATA_DIR / "processed"
RAG_DIR: Path = ROOT_DIR / "backend" / "rag" / "documents"


# ============================================================================
# 1. Environment Profiles & Secret Classification
# ============================================================================

class PayPilotEnv(str, Enum):
    """Supported PayPilot deployment environment profiles."""
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class SecretClassification(str, Enum):
    """Security classification tiers for configuration attributes."""
    SECRET = "SECRET"          # High-risk credentials (API keys, passwords, bearer tokens)
    SENSITIVE = "SENSITIVE"    # Infrastructure connection strings containing hosts/dbs
    NON_SECRET = "NON_SECRET"  # Safe operational settings, timeouts, flags


# Centralized registry of classified configuration fields
SECRET_CLASSIFICATIONS: Dict[str, SecretClassification] = {
    # High-Risk Secrets
    "nvidia_api_key": SecretClassification.SECRET,
    "paypilot_api_key": SecretClassification.SECRET,
    "paypilot_admin_key": SecretClassification.SECRET,
    "NVIDIA_API_KEY": SecretClassification.SECRET,
    "PAYPILOT_API_KEY": SecretClassification.SECRET,
    "PAYPILOT_ADMIN_KEY": SecretClassification.SECRET,

    # Sensitive URLs (may embed credentials or internal network topologies)
    "database_url": SecretClassification.SENSITIVE,
    "redis_url": SecretClassification.SENSITIVE,
    "DATABASE_URL": SecretClassification.SENSITIVE,
    "REDIS_URL": SecretClassification.SENSITIVE,

    # Non-Secret Operational Configuration
    "env": SecretClassification.NON_SECRET,
    "llm_provider": SecretClassification.NON_SECRET,
    "nvidia_model": SecretClassification.NON_SECRET,
    "nvidia_base_url": SecretClassification.NON_SECRET,
    "fastapi_host": SecretClassification.NON_SECRET,
    "fastapi_port": SecretClassification.NON_SECRET,
    "data_backend": SecretClassification.NON_SECRET,
    "job_store_backend": SecretClassification.NON_SECRET,
    "rate_limit_backend": SecretClassification.NON_SECRET,
    "persistence_backend": SecretClassification.NON_SECRET,
    "audit_store_backend": SecretClassification.NON_SECRET,
    "tracing_enabled": SecretClassification.NON_SECRET,
}


# ============================================================================
# 2. Secret Provider Abstraction
# ============================================================================

class SecretProvider(ABC):
    """Abstract interface for external secret retrieval."""

    @abstractmethod
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieves secret by key name."""
        raise NotImplementedError

    @abstractmethod
    def has_secret(self, key: str) -> bool:
        """Checks if secret is present and non-empty."""
        raise NotImplementedError

    @abstractmethod
    def list_configured_secret_keys(self) -> List[str]:
        """Lists keys of configured secrets without exposing values."""
        raise NotImplementedError


class EnvironmentSecretProvider(SecretProvider):
    """Local environment-variable based secret provider."""

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        full_key = f"{self._prefix}{key}" if self._prefix else key
        val = os.getenv(full_key)
        if val is not None and val.strip() != "":
            return val.strip()
        return default

    def has_secret(self, key: str) -> bool:
        val = self.get_secret(key)
        return val is not None and len(val.strip()) > 0

    def list_configured_secret_keys(self) -> List[str]:
        known_secret_keys = [
            "NVIDIA_API_KEY",
            "PAYPILOT_API_KEY",
            "PAYPILOT_ADMIN_KEY",
            "DATABASE_URL",
            "REDIS_URL",
        ]
        return [k for k in known_secret_keys if self.has_secret(k)]


# Default secret provider singleton
_DEFAULT_SECRET_PROVIDER: SecretProvider = EnvironmentSecretProvider()


def get_secret_provider() -> SecretProvider:
    """Retrieves the active SecretProvider instance."""
    return _DEFAULT_SECRET_PROVIDER


def set_secret_provider(provider: SecretProvider) -> None:
    """Sets the active SecretProvider instance."""
    global _DEFAULT_SECRET_PROVIDER
    _DEFAULT_SECRET_PROVIDER = provider


# ============================================================================
# 3. Secret Redaction & Sanitization Helpers
# ============================================================================

def mask_secret(value: Optional[str], visible_chars: int = 0) -> str:
    """Masks a secret string completely or preserving only a bounded prefix.

    Args:
        value: Sensitive string to mask.
        visible_chars: Number of prefix characters to reveal (0 for total mask).

    Returns:
        str: Redacted string '********' or 'nvap...****'.
    """
    if not value or not str(value).strip():
        return ""
    val_str = str(value).strip()
    if visible_chars <= 0 or len(val_str) <= visible_chars + 4:
        return "********"
    prefix = val_str[:visible_chars]
    return f"{prefix}...****"


def mask_database_url(url: Optional[str]) -> str:
    """Safely masks username:password credentials in database and redis URLs.

    Example: postgresql://admin:secret123@db.host/prod -> postgresql://admin:***@db.host/prod
    """
    if not url or not str(url).strip():
        return ""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", str(url).strip())


# ============================================================================
# 4. Strongly Typed Configuration Model & Validation
# ============================================================================

class ConfigValidationError(ValueError):
    """Raised when configuration validation or cross-backend compatibility fails."""
    pass


@dataclass
class PayPilotSettings:
    """Authoritative, strongly-typed PayPilot application configuration."""

    # Schema & Profile
    config_schema_version: int = CONFIG_SCHEMA_VERSION
    env: PayPilotEnv = PayPilotEnv.DEVELOPMENT

    # Paths & Datasets
    data_path: Path = field(default_factory=lambda: ROOT_DIR / "data" / "processed" / "merchant_transactions.csv")
    data_seed: int = 42

    # LLM & Inference Engine
    llm_provider: str = "nvidia"
    nvidia_api_key: str = ""
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    supervisor_model: str = "nvidia/nemotron-3-super-120b-a12b"
    aggregator_model: str = "nvidia/nemotron-3-super-120b-a12b"
    recovery_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    llm_request_timeout: float = 60.0

    # Resilience & Circuit Breaker
    llm_max_retries: int = 1
    llm_retry_base_delay: float = 0.5
    llm_retry_max_delay: float = 4.0
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_recovery_time: float = 30.0

    # Server & Concurrency
    fastapi_host: str = "0.0.0.0"
    fastapi_port: int = 8000
    backend_url: str = "http://localhost:8000"
    max_query_length: int = 1000
    max_concurrent_requests: int = 10
    app_workers: int = 1
    shutdown_timeout_seconds: float = 15.0

    # Security & Rate Limiting
    paypilot_api_key: str = ""
    paypilot_admin_key: str = ""
    require_auth: bool = False
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_backend: str = "memory"

    # Transaction Storage & Database
    data_backend: str = "csv"
    database_url: str = "sqlite:///data/processed/paypilot_transactions.db"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_pre_ping: bool = True

    # Persistence & Redis
    persistence_backend: str = "memory"
    redis_url: str = ""

    # Background Jobs & Worker Fleet
    job_max_workers: int = 3
    job_max_queue_size: int = 50
    job_max_retained_jobs: int = 200
    job_lease_timeout_seconds: int = 300
    job_store_backend: str = "memory"

    # Disaster Recovery & Backups
    backup_dir: Path = field(default_factory=lambda: ROOT_DIR / "data" / "backups")
    backup_retention_days: int = 7
    backup_verify_enabled: bool = True
    audit_store_backend: str = "memory"
    audit_max_events: int = 1000
    audit_log_enabled: bool = True

    # Distributed Tracing & Observability
    tracing_enabled: bool = True
    trace_max_events: int = 5000
    trace_max_traces: int = 1000

    # Service Level Objectives (SLO) Targets
    slo_analyze_p95_ms: float = 1500.0
    slo_error_rate_percent: float = 1.0
    slo_job_success_percent: float = 99.0
    slo_alert_cooldown_seconds: float = 60.0

    # API Traffic Management, Tenant Quotas & Idempotency (Phase 21)
    analyze_rate_limit_per_minute: int = 60
    job_rate_limit_per_minute: int = 30
    tenant_max_concurrent_jobs: int = 5
    tenant_daily_analyze_quota: int = 1000
    tenant_daily_job_quota: int = 500
    idempotency_ttl_seconds: int = 86400
    idempotency_max_records: int = 5000
    idempotency_store_backend: str = "memory"

    @classmethod
    def load_from_environment(cls, provider: Optional[SecretProvider] = None) -> "PayPilotSettings":
        """Loads configuration with precedence: explicit env vars -> profile defaults -> safe defaults."""
        sec = provider or get_secret_provider()

        raw_env = os.getenv("PAYPILOT_ENV", "development").strip().lower()
        try:
            env_enum = PayPilotEnv(raw_env)
        except ValueError:
            logger.warning(f"Invalid PAYPILOT_ENV '{raw_env}'. Defaulting to 'development'.")
            env_enum = PayPilotEnv.DEVELOPMENT

        # Retrieve secrets through SecretProvider
        nv_key = sec.get_secret("NVIDIA_API_KEY", "") or ""
        app_key = sec.get_secret("PAYPILOT_API_KEY", "") or ""
        adm_key = sec.get_secret("PAYPILOT_ADMIN_KEY", "") or ""
        db_url = sec.get_secret("DATABASE_URL", "sqlite:///data/processed/paypilot_transactions.db") or ""
        rd_url = sec.get_secret("REDIS_URL", "") or ""

        # Default auth requirement based on environment & key presence
        auth_req_env = os.getenv("REQUIRE_AUTH")
        if auth_req_env is not None and auth_req_env.strip() != "":
            req_auth = auth_req_env.strip().lower() in ("true", "1", "yes")
        elif env_enum == PayPilotEnv.PRODUCTION:
            req_auth = True
        else:
            req_auth = bool(app_key or adm_key)

        raw_data_path = os.getenv("DATA_PATH", "data/processed/merchant_transactions.csv")
        p_data = Path(raw_data_path)
        data_path = p_data if p_data.is_absolute() else (ROOT_DIR / p_data)

        raw_backup_dir = os.getenv("BACKUP_DIR", "data/backups")
        p_backup = Path(raw_backup_dir)
        backup_dir = p_backup if p_backup.is_absolute() else (ROOT_DIR / p_backup)

        settings = cls(
            env=env_enum,
            data_path=data_path,
            data_seed=int(os.getenv("DATA_SEED", "42")),
            llm_provider=os.getenv("LLM_PROVIDER", "nvidia").strip().lower(),
            nvidia_api_key=nv_key,
            nvidia_model=os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip(),
            supervisor_model=os.getenv("SUPERVISOR_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip(),
            aggregator_model=os.getenv("AGGREGATOR_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip(),
            recovery_model=os.getenv("RECOVERY_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip(),
            nvidia_base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip(),
            llm_request_timeout=float(os.getenv("LLM_REQUEST_TIMEOUT", "60.0")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
            llm_retry_base_delay=float(os.getenv("LLM_RETRY_BASE_DELAY", "0.5")),
            llm_retry_max_delay=float(os.getenv("LLM_RETRY_MAX_DELAY", "4.0")),
            circuit_breaker_failure_threshold=int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3")),
            circuit_breaker_recovery_time=float(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIME", "30.0")),
            fastapi_host=os.getenv("FASTAPI_HOST", "0.0.0.0").strip(),
            fastapi_port=int(os.getenv("PORT", os.getenv("FASTAPI_PORT", "8000"))),
            backend_url=os.getenv("BACKEND_URL", "http://localhost:8000").strip(),
            max_query_length=int(os.getenv("MAX_QUERY_LENGTH", "1000")),
            max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "10")),
            app_workers=int(os.getenv("APP_WORKERS", "1")),
            paypilot_api_key=app_key,
            paypilot_admin_key=adm_key,
            require_auth=req_auth,
            rate_limit_enabled=os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() in ("true", "1", "yes"),
            rate_limit_requests=int(os.getenv("RATE_LIMIT_REQUESTS", "60")),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            rate_limit_backend=os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower(),
            data_backend=os.getenv("DATA_BACKEND", "csv").strip().lower(),
            database_url=db_url,
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            db_pool_timeout=float(os.getenv("DB_POOL_TIMEOUT", "30.0")),
            db_pool_pre_ping=os.getenv("DB_POOL_PRE_PING", "true").strip().lower() in ("true", "1", "yes"),
            persistence_backend=os.getenv("PERSISTENCE_BACKEND", "memory").strip().lower(),
            redis_url=rd_url,
            job_max_workers=int(os.getenv("JOB_MAX_WORKERS", "3")),
            job_max_queue_size=int(os.getenv("JOB_MAX_QUEUE_SIZE", "50")),
            job_max_retained_jobs=int(os.getenv("JOB_MAX_RETAINED_JOBS", "200")),
            job_lease_timeout_seconds=int(os.getenv("JOB_LEASE_TIMEOUT_SECONDS", "300")),
            job_store_backend=os.getenv("JOB_STORE_BACKEND", "memory").strip().lower(),
            backup_dir=backup_dir,
            backup_retention_days=int(os.getenv("BACKUP_RETENTION_DAYS", "7")),
            backup_verify_enabled=os.getenv("BACKUP_VERIFY_ENABLED", "true").strip().lower() in ("true", "1", "yes"),
            audit_store_backend=os.getenv("AUDIT_STORE_BACKEND", "memory").strip().lower(),
            audit_max_events=int(os.getenv("AUDIT_MAX_EVENTS", "1000")),
            audit_log_enabled=os.getenv("AUDIT_LOG_ENABLED", "true").strip().lower() in ("true", "1", "yes"),
            tracing_enabled=os.getenv("TRACING_ENABLED", "true").strip().lower() in ("true", "1", "yes"),
            trace_max_events=int(os.getenv("TRACE_MAX_EVENTS", "5000")),
            trace_max_traces=int(os.getenv("TRACE_MAX_TRACES", "1000")),
            slo_analyze_p95_ms=float(os.getenv("SLO_ANALYZE_P95_MS", "1500.0")),
            slo_error_rate_percent=float(os.getenv("SLO_ERROR_RATE_PERCENT", "1.0")),
            slo_job_success_percent=float(os.getenv("SLO_JOB_SUCCESS_PERCENT", "99.0")),
            slo_alert_cooldown_seconds=float(os.getenv("SLO_ALERT_COOLDOWN_SECONDS", "60.0")),
            analyze_rate_limit_per_minute=int(os.getenv("ANALYZE_RATE_LIMIT_PER_MINUTE", "60")),
            job_rate_limit_per_minute=int(os.getenv("JOB_RATE_LIMIT_PER_MINUTE", "30")),
            tenant_max_concurrent_jobs=int(os.getenv("TENANT_MAX_CONCURRENT_JOBS", "5")),
            tenant_daily_analyze_quota=int(os.getenv("TENANT_DAILY_ANALYZE_QUOTA", "1000")),
            tenant_daily_job_quota=int(os.getenv("TENANT_DAILY_JOB_QUOTA", "500")),
            idempotency_ttl_seconds=int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400")),
            idempotency_max_records=int(os.getenv("IDEMPOTENCY_MAX_RECORDS", "5000")),
            idempotency_store_backend=os.getenv("IDEMPOTENCY_STORE_BACKEND", "memory").strip().lower(),
            shutdown_timeout_seconds=float(os.getenv("SHUTDOWN_TIMEOUT_SECONDS", "15.0")),
        )
        return settings

    def validate(self) -> None:
        """Validates typed settings, range bounds, and cross-backend compatibility.

        Raises:
            ConfigValidationError: On invalid range, unallowed enum value, or incompatible backend topology.
        """
        # 1. Range Validations
        if not (1 <= self.fastapi_port <= 65535):
            raise ConfigValidationError(f"Invalid FASTAPI_PORT: {self.fastapi_port}. Must be between 1 and 65535.")
        if not (1 <= self.job_max_workers <= 64):
            raise ConfigValidationError(f"Invalid JOB_MAX_WORKERS: {self.job_max_workers}. Must be between 1 and 64.")
        if not (1 <= self.job_max_queue_size <= 10000):
            raise ConfigValidationError(f"Invalid JOB_MAX_QUEUE_SIZE: {self.job_max_queue_size}. Must be between 1 and 10000.")
        if not (1 <= self.app_workers <= 32):
            raise ConfigValidationError(f"Invalid APP_WORKERS: {self.app_workers}. Must be between 1 and 32.")
        if self.llm_request_timeout <= 0.0:
            raise ConfigValidationError(f"Invalid LLM_REQUEST_TIMEOUT: {self.llm_request_timeout}. Must be > 0.0.")
        if not (1.0 <= self.shutdown_timeout_seconds <= 300.0):
            raise ConfigValidationError(f"Invalid SHUTDOWN_TIMEOUT_SECONDS: {self.shutdown_timeout_seconds}. Must be between 1.0 and 300.0.")
        if not (1 <= self.rate_limit_requests <= 100000):
            raise ConfigValidationError(f"Invalid RATE_LIMIT_REQUESTS: {self.rate_limit_requests}. Must be between 1 and 100000.")
        if not (1 <= self.rate_limit_window_seconds <= 86400):
            raise ConfigValidationError(f"Invalid RATE_LIMIT_WINDOW_SECONDS: {self.rate_limit_window_seconds}. Must be between 1 and 86400.")
        if not (1 <= self.backup_retention_days <= 3650):
            raise ConfigValidationError(f"Invalid BACKUP_RETENTION_DAYS: {self.backup_retention_days}. Must be between 1 and 3650.")
        if not (1 <= self.analyze_rate_limit_per_minute <= 100000):
            raise ConfigValidationError(f"Invalid ANALYZE_RATE_LIMIT_PER_MINUTE: {self.analyze_rate_limit_per_minute}. Must be between 1 and 100000.")
        if not (1 <= self.job_rate_limit_per_minute <= 100000):
            raise ConfigValidationError(f"Invalid JOB_RATE_LIMIT_PER_MINUTE: {self.job_rate_limit_per_minute}. Must be between 1 and 100000.")
        if not (1 <= self.tenant_max_concurrent_jobs <= 100):
            raise ConfigValidationError(f"Invalid TENANT_MAX_CONCURRENT_JOBS: {self.tenant_max_concurrent_jobs}. Must be between 1 and 100.")
        if not (1 <= self.idempotency_ttl_seconds <= 604800):
            raise ConfigValidationError(f"Invalid IDEMPOTENCY_TTL_SECONDS: {self.idempotency_ttl_seconds}. Must be between 1 and 604800.")

        # 2. Enum & Backend Type Validations
        valid_data_backends = {"csv", "sqlite", "postgres", "postgresql"}
        if self.data_backend not in valid_data_backends:
            raise ConfigValidationError(f"Invalid DATA_BACKEND: '{self.data_backend}'. Allowed: {valid_data_backends}")

        valid_job_backends = {"memory", "sql"}
        if self.job_store_backend not in valid_job_backends:
            raise ConfigValidationError(f"Invalid JOB_STORE_BACKEND: '{self.job_store_backend}'. Allowed: {valid_job_backends}")

        valid_rate_backends = {"memory", "redis"}
        if self.rate_limit_backend not in valid_rate_backends:
            raise ConfigValidationError(f"Invalid RATE_LIMIT_BACKEND: '{self.rate_limit_backend}'. Allowed: {valid_rate_backends}")

        valid_audit_backends = {"memory", "sql"}
        if self.audit_store_backend not in valid_audit_backends:
            raise ConfigValidationError(f"Invalid AUDIT_STORE_BACKEND: '{self.audit_store_backend}'. Allowed: {valid_audit_backends}")

        valid_persistence_backends = {"memory", "redis"}
        if self.persistence_backend not in valid_persistence_backends:
            raise ConfigValidationError(f"Invalid PERSISTENCE_BACKEND: '{self.persistence_backend}'. Allowed: {valid_persistence_backends}")

        valid_idempotency_backends = {"memory", "redis"}
        if self.idempotency_store_backend not in valid_idempotency_backends:
            raise ConfigValidationError(f"Invalid IDEMPOTENCY_STORE_BACKEND: '{self.idempotency_store_backend}'. Allowed: {valid_idempotency_backends}")

        # 3. Cross-Configuration Compatibility Validations
        # SQL Job Store requires Database URL
        if self.job_store_backend == "sql" and not self.database_url:
            raise ConfigValidationError("Incompatible configuration: JOB_STORE_BACKEND='sql' requires a configured DATABASE_URL.")

        # SQL Audit Store requires Database URL
        if self.audit_store_backend == "sql" and not self.database_url:
            raise ConfigValidationError("Incompatible configuration: AUDIT_STORE_BACKEND='sql' requires a configured DATABASE_URL.")

        # Postgres Data Backend requires PostgreSQL-compatible DATABASE_URL
        if self.data_backend in ("postgres", "postgresql"):
            if not self.database_url:
                raise ConfigValidationError("Incompatible configuration: DATA_BACKEND='postgres' requires DATABASE_URL.")
            low_url = self.database_url.lower()
            if not (low_url.startswith("postgres://") or low_url.startswith("postgresql://") or low_url.startswith("postgresql+")):
                raise ConfigValidationError("Incompatible configuration: DATA_BACKEND='postgres' requires a postgresql:// connection URL.")

        # Redis Backends require REDIS_URL
        if self.rate_limit_backend == "redis" and not self.redis_url:
            raise ConfigValidationError("Incompatible configuration: RATE_LIMIT_BACKEND='redis' requires a configured REDIS_URL.")

        if self.persistence_backend == "redis" and not self.redis_url:
            raise ConfigValidationError("Incompatible configuration: PERSISTENCE_BACKEND='redis' requires a configured REDIS_URL.")

        if self.idempotency_store_backend == "redis" and not self.redis_url:
            raise ConfigValidationError("Incompatible configuration: IDEMPOTENCY_STORE_BACKEND='redis' requires a configured REDIS_URL.")

        # 4. Production Profile Strict Validation
        if self.env == PayPilotEnv.PRODUCTION:
            if self.require_auth and not (self.paypilot_api_key or self.paypilot_admin_key):
                raise ConfigValidationError(
                    "Production configuration validation failed: REQUIRE_AUTH is enabled but neither PAYPILOT_API_KEY nor PAYPILOT_ADMIN_KEY is configured."
                )

    def __repr__(self) -> str:
        """Returns safe string representation with all secrets masked."""
        return (
            f"PayPilotSettings(env='{self.env.value}', "
            f"llm_provider='{self.llm_provider}', "
            f"data_backend='{self.data_backend}', "
            f"database_url='{mask_database_url(self.database_url)}', "
            f"job_store_backend='{self.job_store_backend}', "
            f"rate_limit_backend='{self.rate_limit_backend}', "
            f"nvidia_api_key='{mask_secret(self.nvidia_api_key)}', "
            f"paypilot_api_key='{mask_secret(self.paypilot_api_key)}', "
            f"paypilot_admin_key='{mask_secret(self.paypilot_admin_key)}')"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ============================================================================
# 5. Global Settings Singleton & Safe Diagnostics
# ============================================================================

_GLOBAL_SETTINGS: Optional[PayPilotSettings] = None


def get_settings() -> PayPilotSettings:
    """Retrieves or initializes the active PayPilotSettings singleton."""
    global _GLOBAL_SETTINGS
    if _GLOBAL_SETTINGS is None:
        _GLOBAL_SETTINGS = PayPilotSettings.load_from_environment()
        _GLOBAL_SETTINGS.validate()
    return _GLOBAL_SETTINGS


def set_settings(settings: PayPilotSettings) -> None:
    """Sets the active PayPilotSettings singleton (primarily for testing)."""
    global _GLOBAL_SETTINGS
    settings.validate()
    _GLOBAL_SETTINGS = settings


def reset_settings() -> None:
    """Reloads settings directly from the current environment."""
    global _GLOBAL_SETTINGS
    _GLOBAL_SETTINGS = PayPilotSettings.load_from_environment()
    _GLOBAL_SETTINGS.validate()


@contextmanager
def override_settings(**kwargs) -> Generator[PayPilotSettings, None, None]:
    """Context manager for safely overriding configuration settings in tests."""
    current = get_settings()
    # Create copy with overrides
    attrs = {
        "env": current.env,
        "data_path": current.data_path,
        "data_seed": current.data_seed,
        "llm_provider": current.llm_provider,
        "nvidia_api_key": current.nvidia_api_key,
        "nvidia_model": current.nvidia_model,
        "nvidia_base_url": current.nvidia_base_url,
        "llm_request_timeout": current.llm_request_timeout,
        "llm_max_retries": current.llm_max_retries,
        "llm_retry_base_delay": current.llm_retry_base_delay,
        "llm_retry_max_delay": current.llm_retry_max_delay,
        "circuit_breaker_failure_threshold": current.circuit_breaker_failure_threshold,
        "circuit_breaker_recovery_time": current.circuit_breaker_recovery_time,
        "fastapi_host": current.fastapi_host,
        "fastapi_port": current.fastapi_port,
        "backend_url": current.backend_url,
        "max_query_length": current.max_query_length,
        "max_concurrent_requests": current.max_concurrent_requests,
        "app_workers": current.app_workers,
        "paypilot_api_key": current.paypilot_api_key,
        "paypilot_admin_key": current.paypilot_admin_key,
        "require_auth": current.require_auth,
        "rate_limit_enabled": current.rate_limit_enabled,
        "rate_limit_requests": current.rate_limit_requests,
        "rate_limit_window_seconds": current.rate_limit_window_seconds,
        "rate_limit_backend": current.rate_limit_backend,
        "data_backend": current.data_backend,
        "database_url": current.database_url,
        "db_pool_size": current.db_pool_size,
        "db_max_overflow": current.db_max_overflow,
        "db_pool_timeout": current.db_pool_timeout,
        "db_pool_pre_ping": current.db_pool_pre_ping,
        "persistence_backend": current.persistence_backend,
        "redis_url": current.redis_url,
        "job_max_workers": current.job_max_workers,
        "job_max_queue_size": current.job_max_queue_size,
        "job_max_retained_jobs": current.job_max_retained_jobs,
        "job_lease_timeout_seconds": current.job_lease_timeout_seconds,
        "job_store_backend": current.job_store_backend,
        "backup_dir": current.backup_dir,
        "backup_retention_days": current.backup_retention_days,
        "backup_verify_enabled": current.backup_verify_enabled,
        "audit_store_backend": current.audit_store_backend,
        "audit_max_events": current.audit_max_events,
        "audit_log_enabled": current.audit_log_enabled,
        "tracing_enabled": current.tracing_enabled,
        "trace_max_events": current.trace_max_events,
        "trace_max_traces": current.trace_max_traces,
        "slo_analyze_p95_ms": current.slo_analyze_p95_ms,
        "slo_error_rate_percent": current.slo_error_rate_percent,
        "slo_job_success_percent": current.slo_job_success_percent,
        "slo_alert_cooldown_seconds": current.slo_alert_cooldown_seconds,
        "analyze_rate_limit_per_minute": current.analyze_rate_limit_per_minute,
        "job_rate_limit_per_minute": current.job_rate_limit_per_minute,
        "tenant_max_concurrent_jobs": current.tenant_max_concurrent_jobs,
        "tenant_daily_analyze_quota": current.tenant_daily_analyze_quota,
        "tenant_daily_job_quota": current.tenant_daily_job_quota,
        "idempotency_ttl_seconds": current.idempotency_ttl_seconds,
        "idempotency_max_records": current.idempotency_max_records,
        "idempotency_store_backend": current.idempotency_store_backend,
    }
    attrs.update(kwargs)
    new_settings = PayPilotSettings(**attrs)
    new_settings.validate()
    set_settings(new_settings)
    try:
        yield new_settings
    finally:
        set_settings(current)


def safe_config_snapshot(settings: Optional[PayPilotSettings] = None) -> Dict[str, Any]:
    """Generates a complete, non-secret configuration snapshot safe for telemetry and APIs."""
    s = settings or get_settings()
    return {
        "schema_version": s.config_schema_version,
        "environment": s.env.value,
        "server": {
            "host": s.fastapi_host,
            "port": s.fastapi_port,
            "backend_url": s.backend_url,
            "app_workers": s.app_workers,
            "max_concurrent_requests": s.max_concurrent_requests,
            "max_query_length": s.max_query_length,
        },
        "llm": {
            "provider": s.llm_provider,
            "model": s.nvidia_model,
            "supervisor_model": s.supervisor_model,
            "aggregator_model": s.aggregator_model,
            "recovery_model": s.recovery_model,
            "base_url": s.nvidia_base_url,
            "timeout_seconds": s.llm_request_timeout,
            "max_retries": s.llm_max_retries,
            "circuit_breaker_threshold": s.circuit_breaker_failure_threshold,
            "api_key_configured": bool(s.nvidia_api_key),
        },
        "storage": {
            "data_backend": s.data_backend,
            "database_configured": bool(s.database_url),
            "masked_database_url": mask_database_url(s.database_url),
            "persistence_backend": s.persistence_backend,
            "redis_configured": bool(s.redis_url),
            "dataset_exists": s.data_path.exists(),
        },
        "security": {
            "auth_required": s.require_auth,
            "analyst_key_configured": bool(s.paypilot_api_key),
            "admin_key_configured": bool(s.paypilot_admin_key),
            "rate_limit_enabled": s.rate_limit_enabled,
            "rate_limit_backend": s.rate_limit_backend,
            "rate_limit_requests": s.rate_limit_requests,
            "rate_limit_window_seconds": s.rate_limit_window_seconds,
        },
        "traffic": {
            "analyze_rate_limit_per_minute": s.analyze_rate_limit_per_minute,
            "job_rate_limit_per_minute": s.job_rate_limit_per_minute,
            "tenant_max_concurrent_jobs": s.tenant_max_concurrent_jobs,
            "tenant_daily_analyze_quota": s.tenant_daily_analyze_quota,
            "tenant_daily_job_quota": s.tenant_daily_job_quota,
            "idempotency_store_backend": s.idempotency_store_backend,
            "idempotency_ttl_seconds": s.idempotency_ttl_seconds,
            "idempotency_max_records": s.idempotency_max_records,
        },
        "jobs": {
            "job_store_backend": s.job_store_backend,
            "max_workers": s.job_max_workers,
            "max_queue_size": s.job_max_queue_size,
            "max_retained_jobs": s.job_max_retained_jobs,
            "lease_timeout_seconds": s.job_lease_timeout_seconds,
        },
        "backups": {
            "backup_dir": str(s.backup_dir),
            "retention_days": s.backup_retention_days,
            "verify_enabled": s.backup_verify_enabled,
            "audit_store_backend": s.audit_store_backend,
            "audit_max_events": s.audit_max_events,
        },
        "observability": {
            "tracing_enabled": s.tracing_enabled,
            "trace_max_events": s.trace_max_events,
            "trace_max_traces": s.trace_max_traces,
            "slo_p95_target_ms": s.slo_analyze_p95_ms,
            "slo_error_rate_target_pct": s.slo_error_rate_percent,
            "slo_job_success_target_pct": s.slo_job_success_percent,
        },
    }


def get_config_diagnostics(settings: Optional[PayPilotSettings] = None) -> Dict[str, Any]:
    """Provides high-level operational diagnostics without exposing secret values."""
    s = settings or get_settings()
    sec = get_secret_provider()
    return {
        "status": "VALID",
        "environment": s.env.value,
        "llm_provider": s.llm_provider,
        "model": s.nvidia_model,
        "database_backend": s.data_backend,
        "job_store": s.job_store_backend,
        "rate_limit_backend": s.rate_limit_backend,
        "tracing": "enabled" if s.tracing_enabled else "disabled",
        "secrets_status": {
            "NVIDIA_API_KEY": "configured" if sec.has_secret("NVIDIA_API_KEY") else "not_configured",
            "PAYPILOT_API_KEY": "configured" if sec.has_secret("PAYPILOT_API_KEY") else "not_configured",
            "PAYPILOT_ADMIN_KEY": "configured" if sec.has_secret("PAYPILOT_ADMIN_KEY") else "not_configured",
            "DATABASE_URL": "configured" if sec.has_secret("DATABASE_URL") else "not_configured",
            "REDIS_URL": "configured" if sec.has_secret("REDIS_URL") else "not_configured",
        },
        "snapshot": safe_config_snapshot(s),
    }


def validate_startup_config(settings: Optional[PayPilotSettings] = None) -> None:
    """Performs fail-fast validation on application startup.

    Raises:
        ConfigValidationError: If startup configuration violates requirements.
    """
    s = settings or get_settings()
    try:
        s.validate()
        logger.info(
            f"Configuration initialized successfully for profile='{s.env.value}' "
            f"(schema_v{s.config_schema_version}, data_backend='{s.data_backend}', "
            f"job_store='{s.job_store_backend}', rate_limiter='{s.rate_limit_backend}')"
        )
    except ConfigValidationError as e:
        logger.error(f"Startup configuration validation failed for environment '{s.env.value}': {e}")
        raise


# ============================================================================
# 6. Backward Compatibility Constants & Dynamic Getters
# ============================================================================

# Constants reflecting current loaded settings
DATA_PATH = ROOT_DIR / os.getenv("DATA_PATH", "data/processed/merchant_transactions.csv")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "nvidia").strip().lower()
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip()
SUPERVISOR_MODEL = os.getenv("SUPERVISOR_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip()
AGGREGATOR_MODEL = os.getenv("AGGREGATOR_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip()
RECOVERY_MODEL = os.getenv("RECOVERY_MODEL", "nvidia/nemotron-3-super-120b-a12b").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
FASTAPI_HOST = os.getenv("FASTAPI_HOST", "0.0.0.0")
FASTAPI_PORT = int(os.getenv("PORT", os.getenv("FASTAPI_PORT", 8000)))
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MAX_QUERY_LENGTH = int(os.getenv("MAX_QUERY_LENGTH", 1000))
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", 10))
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", 60.0))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", 1))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", 0.5))
LLM_RETRY_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", 4.0))
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 3))
CIRCUIT_BREAKER_RECOVERY_TIME = float(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIME", 30.0))
PAYPILOT_API_KEY = os.getenv("PAYPILOT_API_KEY", "").strip()
PAYPILOT_ADMIN_KEY = os.getenv("PAYPILOT_ADMIN_KEY", "").strip()
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true" if (PAYPILOT_API_KEY or PAYPILOT_ADMIN_KEY) else "false").strip().lower() in ("true", "1", "yes")
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() in ("true", "1", "yes")
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", 60))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", 60))
AUDIT_MAX_EVENTS = int(os.getenv("AUDIT_MAX_EVENTS", 1000))
AUDIT_LOG_ENABLED = os.getenv("AUDIT_LOG_ENABLED", "true").strip().lower() in ("true", "1", "yes")
PERSISTENCE_BACKEND = os.getenv("PERSISTENCE_BACKEND", "memory").strip().lower()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
DATA_BACKEND = os.getenv("DATA_BACKEND", "csv").strip().lower()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/processed/paypilot_transactions.db").strip()
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30.0"))
DB_POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").strip().lower() in ("true", "1", "yes")
JOB_MAX_WORKERS = int(os.getenv("JOB_MAX_WORKERS", "3"))
JOB_MAX_QUEUE_SIZE = int(os.getenv("JOB_MAX_QUEUE_SIZE", "50"))
JOB_MAX_RETAINED_JOBS = int(os.getenv("JOB_MAX_RETAINED_JOBS", "200"))
JOB_LEASE_TIMEOUT_SECONDS = int(os.getenv("JOB_LEASE_TIMEOUT_SECONDS", "300"))
APP_WORKERS = int(os.getenv("APP_WORKERS", "1"))
JOB_STORE_BACKEND = os.getenv("JOB_STORE_BACKEND", "memory").strip().lower()
RATE_LIMIT_BACKEND = os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()
BACKUP_DIR = ROOT_DIR / os.getenv("BACKUP_DIR", "data/backups")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))
BACKUP_VERIFY_ENABLED = os.getenv("BACKUP_VERIFY_ENABLED", "true").strip().lower() in ("true", "1", "yes")
AUDIT_STORE_BACKEND = os.getenv("AUDIT_STORE_BACKEND", "memory").strip().lower()
TRACING_ENABLED = os.getenv("TRACING_ENABLED", "true").strip().lower() in ("true", "1", "yes")
TRACE_MAX_EVENTS = int(os.getenv("TRACE_MAX_EVENTS", 5000))
TRACE_MAX_TRACES = int(os.getenv("TRACE_MAX_TRACES", 1000))
SLO_ANALYZE_P95_MS = float(os.getenv("SLO_ANALYZE_P95_MS", 1500.0))
SLO_ERROR_RATE_PERCENT = float(os.getenv("SLO_ERROR_RATE_PERCENT", 1.0))
SLO_JOB_SUCCESS_PERCENT = float(os.getenv("SLO_JOB_SUCCESS_PERCENT", 99.0))
SLO_ALERT_COOLDOWN_SECONDS = float(os.getenv("SLO_ALERT_COOLDOWN_SECONDS", 60.0))
DATA_SEED = int(os.getenv("DATA_SEED", 42))

# CORS: explicit allowlist, comma-separated. Defaults cover the deployed frontend + local dev.
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://paypilot-frontend-cptp.onrender.com,http://localhost:5173,http://localhost:3000",
    ).split(",")
    if o.strip()
]

DISCLAIMER_TEXT = (
    "⚠️ Safety & Data Honesty: This prototype uses synthetic merchant/payment data "
    "and is not connected to live Razorpay merchant data. Built independently for "
    "the Razorpay AI Builder competition."
)


# Dynamic getters delegating to SecretProvider and active settings
def get_paypilot_api_key() -> str:
    return get_secret_provider().get_secret("PAYPILOT_API_KEY", "") or ""


def get_paypilot_admin_key() -> str:
    return get_secret_provider().get_secret("PAYPILOT_ADMIN_KEY", "") or ""


def is_auth_required() -> bool:
    env_val = os.getenv("REQUIRE_AUTH")
    if env_val is not None and env_val.strip() != "":
        return env_val.strip().lower() in ("true", "1", "yes")
    return bool(get_paypilot_api_key() or get_paypilot_admin_key())


def is_rate_limiting_enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "true").strip().lower() in ("true", "1", "yes")


def get_audit_max_events() -> int:
    return int(os.getenv("AUDIT_MAX_EVENTS", 1000))


def is_audit_enabled() -> bool:
    return os.getenv("AUDIT_LOG_ENABLED", "true").strip().lower() in ("true", "1", "yes")


def get_data_backend() -> str:
    return os.getenv("DATA_BACKEND", "csv").strip().lower()


def get_database_url() -> str:
    return get_secret_provider().get_secret("DATABASE_URL", "sqlite:///data/processed/paypilot_transactions.db") or ""


def get_job_max_workers() -> int:
    return int(os.getenv("JOB_MAX_WORKERS", 3))


def get_job_max_queue_size() -> int:
    return int(os.getenv("JOB_MAX_QUEUE_SIZE", 50))


def get_job_max_retained_jobs() -> int:
    return int(os.getenv("JOB_MAX_RETAINED_JOBS", 200))


def get_job_lease_timeout() -> int:
    return int(os.getenv("JOB_LEASE_TIMEOUT_SECONDS", 300))


def get_app_workers() -> int:
    return int(os.getenv("APP_WORKERS", 1))


def get_job_store_backend() -> str:
    return os.getenv("JOB_STORE_BACKEND", "memory").strip().lower()


def get_rate_limit_backend() -> str:
    return os.getenv("RATE_LIMIT_BACKEND", "memory").strip().lower()


def get_backup_dir() -> Path:
    raw = os.getenv("BACKUP_DIR", "data/backups")
    p = Path(raw)
    return p if p.is_absolute() else (ROOT_DIR / p)


def get_backup_retention_days() -> int:
    return int(os.getenv("BACKUP_RETENTION_DAYS", 7))


def is_backup_verify_enabled() -> bool:
    return os.getenv("BACKUP_VERIFY_ENABLED", "true").strip().lower() in ("true", "1", "yes")


def get_audit_store_backend() -> str:
    return os.getenv("AUDIT_STORE_BACKEND", "memory").strip().lower()


def is_tracing_enabled() -> bool:
    return os.getenv("TRACING_ENABLED", "true").strip().lower() in ("true", "1", "yes")


def get_trace_max_events() -> int:
    return int(os.getenv("TRACE_MAX_EVENTS", 5000))


def get_trace_max_traces() -> int:
    return int(os.getenv("TRACE_MAX_TRACES", 1000))


def get_slo_analyze_p95_ms() -> float:
    return float(os.getenv("SLO_ANALYZE_P95_MS", 1500.0))


def get_slo_error_rate_percent() -> float:
    return float(os.getenv("SLO_ERROR_RATE_PERCENT", 1.0))


def get_slo_job_success_percent() -> float:
    return float(os.getenv("SLO_JOB_SUCCESS_PERCENT", 99.0))


def get_slo_alert_cooldown_seconds() -> float:
    return float(os.getenv("SLO_ALERT_COOLDOWN_SECONDS", 60.0))


def get_analyze_rate_limit_per_minute() -> int:
    return get_settings().analyze_rate_limit_per_minute


def get_job_rate_limit_per_minute() -> int:
    return get_settings().job_rate_limit_per_minute


def get_tenant_max_concurrent_jobs() -> int:
    return get_settings().tenant_max_concurrent_jobs


def get_tenant_daily_analyze_quota() -> int:
    return get_settings().tenant_daily_analyze_quota


def get_tenant_daily_job_quota() -> int:
    return get_settings().tenant_daily_job_quota


def get_idempotency_ttl_seconds() -> int:
    return get_settings().idempotency_ttl_seconds


def get_idempotency_max_records() -> int:
    return get_settings().idempotency_max_records


def get_idempotency_store_backend() -> str:
    return get_settings().idempotency_store_backend


def get_shutdown_timeout_seconds() -> float:
    return get_settings().shutdown_timeout_seconds


def get_supervisor_model() -> str:
    return get_settings().supervisor_model


def get_aggregator_model() -> str:
    return get_settings().aggregator_model


def get_recovery_model() -> str:
    return get_settings().recovery_model


def validate_config() -> dict:
    """Validates configuration parameters without leaking secret values."""
    status = {
        "dataset_exists": DATA_PATH.exists(),
        "data_path": str(DATA_PATH),
        "data_backend": get_data_backend(),
        "database_configured": bool(get_database_url()),
        "llm_provider": LLM_PROVIDER,
        "model": NVIDIA_MODEL,
        "supervisor_model": get_supervisor_model(),
        "aggregator_model": get_aggregator_model(),
        "recovery_model": get_recovery_model(),
        "has_api_key": bool(os.getenv("NVIDIA_API_KEY", "").strip()),
        "request_timeout_sec": LLM_REQUEST_TIMEOUT,
        "max_query_length": MAX_QUERY_LENGTH,
        "max_concurrency": MAX_CONCURRENT_REQUESTS,
        "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
        "persistence_backend": PERSISTENCE_BACKEND,
        "redis_configured": bool(REDIS_URL),
        "auth_required": is_auth_required(),
        "has_paypilot_api_key": bool(get_paypilot_api_key()),
        "has_admin_api_key": bool(get_paypilot_admin_key()),
        "rate_limiting_enabled": is_rate_limiting_enabled(),
        "rate_limit_backend": get_rate_limit_backend(),
        "audit_enabled": is_audit_enabled(),
        "audit_max_events": get_audit_max_events(),
        "audit_store_backend": get_audit_store_backend(),
        "job_max_workers": get_job_max_workers(),
        "job_max_queue_size": get_job_max_queue_size(),
        "job_max_retained_jobs": get_job_max_retained_jobs(),
        "job_store_backend": get_job_store_backend(),
        "app_workers": get_app_workers(),
        "shutdown_timeout_seconds": get_shutdown_timeout_seconds(),
        "backup_dir": str(get_backup_dir()),
        "backup_retention_days": get_backup_retention_days(),
        "backup_verify_enabled": is_backup_verify_enabled(),
        "tracing_enabled": is_tracing_enabled(),
        "trace_max_events": get_trace_max_events(),
        "trace_max_traces": get_trace_max_traces(),
        "slo_analyze_p95_ms": get_slo_analyze_p95_ms(),
        "slo_error_rate_percent": get_slo_error_rate_percent(),
        "slo_job_success_percent": get_slo_job_success_percent(),
        "slo_alert_cooldown_seconds": get_slo_alert_cooldown_seconds(),
        "analyze_rate_limit_per_minute": get_analyze_rate_limit_per_minute(),
        "job_rate_limit_per_minute": get_job_rate_limit_per_minute(),
        "tenant_max_concurrent_jobs": get_tenant_max_concurrent_jobs(),
        "tenant_daily_analyze_quota": get_tenant_daily_analyze_quota(),
        "tenant_daily_job_quota": get_tenant_daily_job_quota(),
        "idempotency_store_backend": get_idempotency_store_backend(),
        "cluster_db_connections_peak": calculate_total_db_connections(),
    }
    return status


def calculate_total_db_connections(
    api_replicas: int = 2,
    worker_replicas: int = 2,
    db_pool_size: Optional[int] = None,
    db_max_overflow: Optional[int] = None,
    worker_pool_size: Optional[int] = None,
) -> int:
    """Calculates theoretical peak database connections across all cluster pods.

    Formula:
        API Max Connections = api_replicas * (db_pool_size + db_max_overflow)
        Worker Max Connections = worker_replicas * (worker_pool_size)
        Total = API Max Connections + Worker Max Connections
    """
    settings = get_settings()
    pool = db_pool_size if db_pool_size is not None else settings.db_pool_size
    overflow = db_max_overflow if db_max_overflow is not None else settings.db_max_overflow
    w_pool = worker_pool_size if worker_pool_size is not None else settings.job_max_workers

    api_total = max(1, api_replicas) * (max(1, pool) + max(0, overflow))
    worker_total = max(1, worker_replicas) * max(1, w_pool)
    return api_total + worker_total


def validate_cluster_db_capacity(
    max_db_server_connections: int = 100,
    api_replicas: int = 2,
    worker_replicas: int = 2,
    safety_margin_percent: float = 20.0,
) -> Dict[str, Any]:
    """Validates that configured cluster connection pools do not exceed database server limits."""
    total_required = calculate_total_db_connections(
        api_replicas=api_replicas,
        worker_replicas=worker_replicas,
    )
    effective_max = max_db_server_connections * (1.0 - (safety_margin_percent / 100.0))
    is_safe = total_required <= effective_max

    return {
        "is_safe": is_safe,
        "total_required_connections": total_required,
        "max_db_server_connections": max_db_server_connections,
        "effective_safe_limit": round(effective_max, 1),
        "api_replicas": api_replicas,
        "worker_replicas": worker_replicas,
        "utilization_percent": round((total_required / max_db_server_connections) * 100.0, 1),
    }
