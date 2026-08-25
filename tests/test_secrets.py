"""Comprehensive Security and Secrets Management Tests for PayPilot (Phase 20).

Validates:
- Secret classification registry
- SecretProvider abstraction and environment resolution
- Masking helpers (API keys, database credentials)
- Settings string representation (__repr__ and __str__) security
- Admin /admin/config endpoint RBAC and zero-leakage guarantee
- Comprehensive Secret Leakage Regression Test using canary tokens
"""

import json
import os
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.config import (
    SECRET_CLASSIFICATIONS,
    EnvironmentSecretProvider,
    PayPilotEnv,
    PayPilotSettings,
    SecretClassification,
    get_config_diagnostics,
    mask_database_url,
    mask_secret,
    override_settings,
    safe_config_snapshot,
)
from backend.observability.audit import get_audit_store, record_audit_event
from backend.observability.tracing import get_trace_store, trace_span


def test_secret_classification_tiers():
    """Validates that all critical credentials are categorized into appropriate security tiers."""
    assert SECRET_CLASSIFICATIONS["nvidia_api_key"] == SecretClassification.SECRET
    assert SECRET_CLASSIFICATIONS["paypilot_api_key"] == SecretClassification.SECRET
    assert SECRET_CLASSIFICATIONS["paypilot_admin_key"] == SecretClassification.SECRET
    assert SECRET_CLASSIFICATIONS["database_url"] == SecretClassification.SENSITIVE
    assert SECRET_CLASSIFICATIONS["redis_url"] == SecretClassification.SENSITIVE
    assert SECRET_CLASSIFICATIONS["fastapi_port"] == SecretClassification.NON_SECRET


def test_environment_secret_provider(monkeypatch):
    """Validates secret retrieval and key listing via EnvironmentSecretProvider."""
    provider = EnvironmentSecretProvider()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-provider-key-123")
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", "test-admin-secret-456")

    assert provider.has_secret("NVIDIA_API_KEY") is True
    assert provider.get_secret("NVIDIA_API_KEY") == "nvapi-test-provider-key-123"

    configured = provider.list_configured_secret_keys()
    assert "NVIDIA_API_KEY" in configured
    assert "PAYPILOT_ADMIN_KEY" in configured


def test_mask_secret_helper():
    """Validates masking helper logic."""
    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("nvapi-super-secret-key-12345") == "********"
    assert mask_secret("nvapi-super-secret-key-12345", visible_chars=4) == "nvap...****"


def test_mask_database_url_helper():
    """Validates password removal from connection strings."""
    url = "postgresql://paypilot_user:super_secret_password_123@db.prod.internal:5432/paypilot"
    masked = mask_database_url(url)
    assert "super_secret_password_123" not in masked
    assert masked == "postgresql://paypilot_user:***@db.prod.internal:5432/paypilot"

    # SQLite URLs without credentials remain clean
    sqlite_url = "sqlite:///data/processed/paypilot_transactions.db"
    assert mask_database_url(sqlite_url) == sqlite_url


def test_settings_repr_and_str_masking():
    """Validates that printing or inspecting PayPilotSettings never leaks credentials."""
    s = PayPilotSettings(
        nvidia_api_key="nvapi-super-confidential-key",
        paypilot_api_key="analyst-private-token",
        paypilot_admin_key="admin-root-token",
        database_url="postgresql://admin:mypassword@localhost/db",
    )
    repr_str = repr(s)
    str_str = str(s)

    for sensitive_val in ("nvapi-super-confidential-key", "analyst-private-token", "admin-root-token", "mypassword"):
        assert sensitive_val not in repr_str
        assert sensitive_val not in str_str

    assert "********" in repr_str
    assert "admin:***@localhost/db" in repr_str


def test_safe_config_snapshot_structure():
    """Validates that safe_config_snapshot produces non-secret structured data."""
    s = PayPilotSettings(
        nvidia_api_key="nvapi-test-snapshot-secret",
        database_url="postgresql://user:dbpass@host/db",
    )
    snapshot = safe_config_snapshot(s)

    assert snapshot["schema_version"] == 1
    assert snapshot["llm"]["api_key_configured"] is True
    assert snapshot["storage"]["database_configured"] is True
    assert snapshot["storage"]["masked_database_url"] == "postgresql://user:***@host/db"

    # Raw secrets must not exist anywhere in snapshot
    snap_str = json.dumps(snapshot)
    assert "nvapi-test-snapshot-secret" not in snap_str
    assert "dbpass" not in snap_str


def test_admin_config_endpoint_rbac(monkeypatch):
    """Validates RBAC protection for GET /admin/config."""
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", "admin-auth-key-789")
    monkeypatch.setenv("PAYPILOT_API_KEY", "analyst-auth-key-123")
    monkeypatch.setenv("REQUIRE_AUTH", "true")

    client = TestClient(app)

    # 1. Unauthenticated request -> 401
    r_unauth = client.get("/admin/config")
    assert r_unauth.status_code == 401

    # 2. Analyst request -> 403 Forbidden
    r_analyst = client.get("/admin/config", headers={"X-API-Key": "analyst-auth-key-123"})
    assert r_analyst.status_code == 403

    # 3. Admin request -> 200 OK with sanitized diagnostics
    r_admin = client.get("/admin/config", headers={"X-API-Key": "admin-auth-key-789"})
    assert r_admin.status_code == 200
    data = r_admin.json()
    assert data["status"] == "VALID"
    assert "secrets_status" in data
    assert "snapshot" in data
    assert "admin-auth-key-789" not in r_admin.text


def test_canary_secret_leakage_regression(monkeypatch):
    """Strict canary secret leakage test.

    Injects known unique canary tokens:
    - 'nvapi-CANARY-SECRET-KEY-9999'
    - 'CANARY_DB_PASSWORD_8888'
    and verifies they NEVER appear in any API response, telemetry snapshot,
    diagnostics, logs, traces, or audit trails.
    """
    CANARY_LLM_KEY = "nvapi-CANARY-SECRET-KEY-9999"
    CANARY_DB_PASS = "CANARY_DB_PASSWORD_8888"
    CANARY_ADMIN_KEY = "admin-CANARY-TOKEN-7777"
    CANARY_DB_URL = f"postgresql://paypilot_user:{CANARY_DB_PASS}@cluster.internal:5432/paypilot"

    monkeypatch.setenv("NVIDIA_API_KEY", CANARY_LLM_KEY)
    monkeypatch.setenv("DATABASE_URL", CANARY_DB_URL)
    monkeypatch.setenv("PAYPILOT_ADMIN_KEY", CANARY_ADMIN_KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "true")

    client = TestClient(app)

    # 1. Check Diagnostics & Snapshots
    diag = get_config_diagnostics()
    diag_str = json.dumps(diag)
    assert CANARY_LLM_KEY not in diag_str
    assert CANARY_DB_PASS not in diag_str
    assert CANARY_ADMIN_KEY not in diag_str

    # 2. Check /health
    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert CANARY_LLM_KEY not in r_health.text
    assert CANARY_DB_PASS not in r_health.text

    # 3. Check /ready
    r_ready = client.get("/ready")
    assert CANARY_LLM_KEY not in r_ready.text
    assert CANARY_DB_PASS not in r_ready.text

    # 4. Check /metrics
    r_metrics = client.get("/metrics", headers={"X-API-Key": CANARY_ADMIN_KEY})
    assert r_metrics.status_code == 200
    assert CANARY_LLM_KEY not in r_metrics.text
    assert CANARY_DB_PASS not in r_metrics.text
    assert CANARY_ADMIN_KEY not in r_metrics.text

    # 5. Check /admin/config
    r_cfg = client.get("/admin/config", headers={"X-API-Key": CANARY_ADMIN_KEY})
    assert r_cfg.status_code == 200
    assert CANARY_LLM_KEY not in r_cfg.text
    assert CANARY_DB_PASS not in r_cfg.text
    assert CANARY_ADMIN_KEY not in r_cfg.text

    # 6. Check Audit Trail
    record_audit_event(
        event_type="test_canary_event",
        client_id="canary_tester",
        role="admin",
        query_summary=f"Connecting to database {CANARY_DB_URL} with key {CANARY_LLM_KEY}",
    )
    audit_events = get_audit_store().get_events(limit=5)
    audit_str = json.dumps([e.to_dict() for e in audit_events])
    assert CANARY_LLM_KEY not in audit_str
    assert CANARY_DB_PASS not in audit_str

    # 7. Check Traces
    with trace_span("canary_operation", component="test", metadata={"secret_ref": CANARY_LLM_KEY}):
        pass

    trace_store = get_trace_store()
    recent_traces = trace_store.list_traces(limit=5)
    trace_str = json.dumps(recent_traces)
    assert CANARY_LLM_KEY not in trace_str
    assert CANARY_DB_PASS not in trace_str
