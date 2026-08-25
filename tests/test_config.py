"""Comprehensive Unit and Integration Tests for PayPilot Production Configuration (Phase 20).

Validates:
- Environment profiles (development, test, staging, production)
- Strongly typed settings and boundary validation
- Cross-backend compatibility checks
- Fail-fast startup validation
- Setting override test isolation
"""

import os
import pytest
from pathlib import Path

from backend.config import (
    CONFIG_SCHEMA_VERSION,
    ConfigValidationError,
    PayPilotEnv,
    PayPilotSettings,
    get_database_url,
    get_job_max_workers,
    get_paypilot_admin_key,
    get_paypilot_api_key,
    get_settings,
    is_auth_required,
    is_tracing_enabled,
    override_settings,
    set_settings,
    validate_config,
    validate_startup_config,
)


def test_config_schema_version_and_defaults():
    """Validates configuration schema version and baseline default values."""
    assert CONFIG_SCHEMA_VERSION == 1
    settings = PayPilotSettings()
    assert settings.config_schema_version == 1
    assert settings.env == PayPilotEnv.DEVELOPMENT
    assert settings.fastapi_port == 8000
    assert settings.job_max_workers == 3
    assert settings.llm_provider == "nvidia"


def test_environment_profile_resolution(monkeypatch):
    """Validates dynamic loading across environment profiles."""
    # Test profile
    monkeypatch.setenv("PAYPILOT_ENV", "test")
    s_test = PayPilotSettings.load_from_environment()
    assert s_test.env == PayPilotEnv.TEST

    # Staging profile
    monkeypatch.setenv("PAYPILOT_ENV", "staging")
    s_stage = PayPilotSettings.load_from_environment()
    assert s_stage.env == PayPilotEnv.STAGING

    # Production profile
    monkeypatch.setenv("PAYPILOT_ENV", "production")
    s_prod = PayPilotSettings.load_from_environment()
    assert s_prod.env == PayPilotEnv.PRODUCTION

    # Invalid profile falls back safely to development
    monkeypatch.setenv("PAYPILOT_ENV", "invalid_env_name")
    s_fallback = PayPilotSettings.load_from_environment()
    assert s_fallback.env == PayPilotEnv.DEVELOPMENT


def test_integer_range_validations():
    """Validates strict rejection of out-of-range integer parameters."""
    # Invalid port
    s = PayPilotSettings(fastapi_port=0)
    with pytest.raises(ConfigValidationError, match="FASTAPI_PORT"):
        s.validate()

    s = PayPilotSettings(fastapi_port=70000)
    with pytest.raises(ConfigValidationError, match="FASTAPI_PORT"):
        s.validate()

    # Invalid job workers
    s = PayPilotSettings(job_max_workers=0)
    with pytest.raises(ConfigValidationError, match="JOB_MAX_WORKERS"):
        s.validate()

    s = PayPilotSettings(job_max_workers=100)
    with pytest.raises(ConfigValidationError, match="JOB_MAX_WORKERS"):
        s.validate()

    # Invalid queue size
    s = PayPilotSettings(job_max_queue_size=-1)
    with pytest.raises(ConfigValidationError, match="JOB_MAX_QUEUE_SIZE"):
        s.validate()

    # Invalid app workers
    s = PayPilotSettings(app_workers=0)
    with pytest.raises(ConfigValidationError, match="APP_WORKERS"):
        s.validate()


def test_float_range_validations():
    """Validates strict rejection of negative or zero timeout parameters."""
    s = PayPilotSettings(llm_request_timeout=0.0)
    with pytest.raises(ConfigValidationError, match="LLM_REQUEST_TIMEOUT"):
        s.validate()

    s = PayPilotSettings(llm_request_timeout=-5.0)
    with pytest.raises(ConfigValidationError, match="LLM_REQUEST_TIMEOUT"):
        s.validate()


def test_enum_backend_validations():
    """Validates rejection of unknown backend enum values."""
    s = PayPilotSettings(data_backend="unsupported_data_engine")
    with pytest.raises(ConfigValidationError, match="DATA_BACKEND"):
        s.validate()

    s = PayPilotSettings(job_store_backend="oracle_queue")
    with pytest.raises(ConfigValidationError, match="JOB_STORE_BACKEND"):
        s.validate()

    s = PayPilotSettings(rate_limit_backend="memcached")
    with pytest.raises(ConfigValidationError, match="RATE_LIMIT_BACKEND"):
        s.validate()

    s = PayPilotSettings(audit_store_backend="dynamodb")
    with pytest.raises(ConfigValidationError, match="AUDIT_STORE_BACKEND"):
        s.validate()

    s = PayPilotSettings(persistence_backend="cassandra")
    with pytest.raises(ConfigValidationError, match="PERSISTENCE_BACKEND"):
        s.validate()


def test_cross_backend_compatibility_sql_jobs():
    """Validates that SQL job store requires a non-empty database URL."""
    s = PayPilotSettings(job_store_backend="sql", database_url="")
    with pytest.raises(ConfigValidationError, match="JOB_STORE_BACKEND='sql' requires a configured DATABASE_URL"):
        s.validate()


def test_cross_backend_compatibility_sql_audit():
    """Validates that SQL audit store requires a non-empty database URL."""
    s = PayPilotSettings(audit_store_backend="sql", database_url="")
    with pytest.raises(ConfigValidationError, match="AUDIT_STORE_BACKEND='sql' requires a configured DATABASE_URL"):
        s.validate()


def test_cross_backend_compatibility_postgres():
    """Validates that postgres data backend requires a postgresql:// URL."""
    s = PayPilotSettings(data_backend="postgres", database_url="sqlite:///test.db")
    with pytest.raises(ConfigValidationError, match="DATA_BACKEND='postgres' requires a postgresql:// connection URL"):
        s.validate()

    # Valid postgres URL passes
    s_valid = PayPilotSettings(data_backend="postgres", database_url="postgresql://user:pass@localhost:5432/db")
    s_valid.validate()


def test_cross_backend_compatibility_redis():
    """Validates that Redis rate limiter & persistence require REDIS_URL."""
    s_rate = PayPilotSettings(rate_limit_backend="redis", redis_url="")
    with pytest.raises(ConfigValidationError, match="RATE_LIMIT_BACKEND='redis' requires a configured REDIS_URL"):
        s_rate.validate()

    s_persist = PayPilotSettings(persistence_backend="redis", redis_url="")
    with pytest.raises(ConfigValidationError, match="PERSISTENCE_BACKEND='redis' requires a configured REDIS_URL"):
        s_persist.validate()


def test_production_environment_strict_auth_validation():
    """Validates that production environment requires API keys when authentication is enabled."""
    s = PayPilotSettings(
        env=PayPilotEnv.PRODUCTION,
        require_auth=True,
        paypilot_api_key="",
        paypilot_admin_key="",
    )
    with pytest.raises(ConfigValidationError, match="Production configuration validation failed"):
        s.validate()


def test_startup_validation_hook():
    """Validates startup hook execution without errors for valid configuration."""
    s = PayPilotSettings(env=PayPilotEnv.TEST)
    validate_startup_config(s)


def test_override_settings_context_manager():
    """Validates test setting override isolation and automatic cleanup."""
    orig_port = get_settings().fastapi_port
    with override_settings(fastapi_port=9999) as overridden:
        assert overridden.fastapi_port == 9999
        assert get_settings().fastapi_port == 9999

    assert get_settings().fastapi_port == orig_port


def test_backward_compatibility_getters():
    """Validates that legacy getters continue returning correct configuration."""
    assert isinstance(get_database_url(), str)
    assert isinstance(get_job_max_workers(), int)
    assert isinstance(is_tracing_enabled(), bool)
    assert isinstance(validate_config(), dict)
