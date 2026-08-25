# Phase 20 — Production Configuration & Secrets Management Specification

---

## 1. Overview & Architecture Goals

PayPilot Phase 20 establishes an enterprise-grade **Production Configuration & Secrets Management** system.

### Primary Question
> *"How does PayPilot reliably enforce environment profile segregation (`development`, `test`, `staging`, `production`), validate configuration types and cross-backend compatibility fail-fast at startup, and guarantee that secrets never leak into source code, logs, traces, metrics, audit events, exception strings, API payloads, benchmarks, or Docker images?"*

```
                     ┌──────────────────────────────────────────────┐
                     │          PAYPILOT_ENV (Profile)              │
                     │  development | test | staging | production   │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │         SecretProvider Abstraction           │
                     │     - EnvironmentSecretProvider              │
                     │     - (Future: Vault / AWS / GCP / K8s)      │
                     └──────────────────────┬───────────────────────┘
                                            │
                                            ▼
                     ┌──────────────────────────────────────────────┐
                     │          PayPilotSettings (Typed)            │
                     │   - Schema Version: CONFIG_SCHEMA_VERSION=1  │
                     │   - Strict Type & Range Validation           │
                     │   - Cross-Backend Compatibility Rules        │
                     │   - Safe Repr / String Masks                 │
                     └──────────────────────┬───────────────────────┘
                                            │
               ┌────────────────────────────┼────────────────────────────┐
               ▼                            ▼                            ▼
   ┌──────────────────────┐    ┌────────────────────────┐   ┌────────────────────────┐
   │ Startup Fail-Fast    │    │ Safe Admin Diagnostics │   │ Redaction & Masking    │
   │ Validation Engine    │    │ GET /admin/config      │   │ mask_secret, URLs      │
   └──────────────────────┘    └────────────────────────┘   └────────────────────────┘
```

---

## 2. Environment Profiles

| Profile Name | Target Environment | Security & Validation Posture |
| :--- | :--- | :--- |
| **`development`** | Local Workstations | Convenient safe defaults; CSV/SQLite allowed; in-memory queues & stores allowed; unauthenticated dev mode permitted. |
| **`test`** | Automated CI / Pytest | 100% offline deterministic execution; zero cloud API keys required (`NVIDIA_API_KEY=""`); mock LLM synthesis. |
| **`staging`** | Pre-production Testing | Production-like configuration; secrets injected via container environment; SQL/Redis topology supported. |
| **`production`** | Live Cluster Ingress | Strict fail-fast startup validation; requires valid authentication credentials, secure database connections, and valid distributed topologies. |

---

## 3. Configuration Precedence

PayPilot evaluates configuration parameters in strict order of authority:
1. **Explicit Environment Variables** (e.g. `os.environ["JOB_MAX_WORKERS"]`)
2. **Local Environment File** (`.env` if present on disk)
3. **Environment Profile Defaults** (e.g. `PAYPILOT_ENV=production` sets `require_auth=True` by default)
4. **Safe Non-Secret Default Values** (defined in `PayPilotSettings`)

> [!IMPORTANT]
> **No Hardcoded Secrets**: Under no circumstances are API keys, database passwords, or bearer tokens hardcoded in source code or defaults.

---

## 4. Secret Classification

Configuration parameters are classified into three security tiers:

| Tier | Description | Key Examples | Redaction Policy |
| :--- | :--- | :--- | :--- |
| **`SECRET`** | High-risk private authentication keys and tokens. | `NVIDIA_API_KEY`, `PAYPILOT_API_KEY`, `PAYPILOT_ADMIN_KEY`, database passwords. | Masked completely (`********` or `[REDACTED]`). Never output in snapshots, logs, or traces. |
| **`SENSITIVE`** | Infrastructure connection strings that may embed hostnames, ports, and credentials. | `DATABASE_URL`, `REDIS_URL`. | URL password masked (`postgresql://user:***@host:5432/db`). |
| **`NON_SECRET`** | Safe operational settings, timeouts, limits, and boolean flags. | `PAYPILOT_ENV`, `FASTAPI_PORT`, `DATA_BACKEND`, `JOB_MAX_WORKERS`, `SLO_ANALYZE_P95_MS`. | Visible in administrative snapshots and diagnostics. |

---

## 5. SecretProvider Abstraction

The `SecretProvider` abstract interface isolates application logic from how secrets are retrieved:

```python
class SecretProvider(ABC):
    @abstractmethod
    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]: ...

    @abstractmethod
    def has_secret(self, key: str) -> bool: ...

    @abstractmethod
    def list_configured_secret_keys(self) -> List[str]: ...
```

- **`EnvironmentSecretProvider`**: The default implementation reading from system environment variables.
- **Future Extensibility**: Cloud secret managers (AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, HashiCorp Vault) can be integrated by implementing `SecretProvider` and calling `set_secret_provider(custom_provider)`.

---

## 6. Secret Redaction & Zero-Leakage Guarantees

PayPilot enforces zero-leakage across all observation vectors:

1. **`mask_secret(value)`**: Returns `********` (or empty string for empty inputs).
2. **`mask_database_url(url)`**: Strips passwords from connection strings (`postgresql://admin:***@db:5432/paypilot`).
3. **`PayPilotSettings.__repr__()` & `__str__()`**: Overridden so inspecting settings instances never prints credentials.
4. **`safe_config_snapshot()`**: Emits structured configuration where secrets are represented solely as booleans (`api_key_configured: true/false`).
5. **Exceptions & Logging**: Global exception handlers and structured log formatters sanitize text via `redact_sensitive_text()`.
6. **Canary Token Testing**: Verified via automated regression tests asserting canary tokens (`nvapi-CANARY-...`, `CANARY_DB_PASS_...`) never appear in logs, traces, audit events, or API responses.

---

## 7. Fail-Fast Startup & Cross-Backend Validation

On application startup, `validate_startup_config()` checks all range bounds, enum types, and cross-backend compatibility:

1. **Integer & Float Bounds**:
   - `FASTAPI_PORT`: $1 \le \text{port} \le 65535$
   - `JOB_MAX_WORKERS`: $1 \le \text{workers} \le 64$
   - `LLM_REQUEST_TIMEOUT`: $> 0.0\text{ s}$
2. **Cross-Backend Compatibility Rules**:
   - `JOB_STORE_BACKEND="sql"` $\implies$ Requires configured `DATABASE_URL`.
   - `AUDIT_STORE_BACKEND="sql"` $\implies$ Requires configured `DATABASE_URL`.
   - `DATA_BACKEND="postgres"` $\implies$ Requires `postgresql://` connection URL.
   - `RATE_LIMIT_BACKEND="redis"` $\implies$ Requires configured `REDIS_URL`.
   - `PERSISTENCE_BACKEND="redis"` $\implies$ Requires configured `REDIS_URL`.
3. **Production Mode Enforcement**:
   - In `PAYPILOT_ENV=production`, `require_auth=True` is enforced and aborts startup if neither `PAYPILOT_ADMIN_KEY` nor `PAYPILOT_API_KEY` is provided.

---

## 8. Production Configuration Matrix

| Configuration Attribute | Classification | Development | Test | Staging | Production |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`PAYPILOT_ENV`** | `NON_SECRET` | `development` (DEFAULT) | `test` (REQUIRED) | `staging` (REQUIRED) | `production` (REQUIRED) |
| **`LLM_PROVIDER`** | `NON_SECRET` | `nvidia` (DEFAULT) | `nvidia` (MOCKED) | `nvidia` (DEFAULT) | `nvidia` (REQUIRED) |
| **`NVIDIA_API_KEY`** | `SECRET` | OPTIONAL | NOT REQUIRED (`""`) | REQUIRED | REQUIRED |
| **`PAYPILOT_API_KEY`** | `SECRET` | OPTIONAL | OPTIONAL | REQUIRED | REQUIRED |
| **`PAYPILOT_ADMIN_KEY`** | `SECRET` | OPTIONAL | OPTIONAL | REQUIRED | REQUIRED |
| **`REQUIRE_AUTH`** | `NON_SECRET` | `false` (DEFAULT) | `false` (DEFAULT) | `true` (DEFAULT) | `true` (REQUIRED) |
| **`DATA_BACKEND`** | `NON_SECRET` | `csv` (DEFAULT) | `csv` (DEFAULT) | `sqlite` / `postgres` | `postgres` / `sqlite` |
| **`DATABASE_URL`** | `SENSITIVE` | SQLite (DEFAULT) | SQLite (DEFAULT) | PostgreSQL / SQLite | PostgreSQL (REQUIRED if postgres) |
| **`JOB_STORE_BACKEND`**| `NON_SECRET` | `memory` (DEFAULT) | `memory` (DEFAULT) | `sql` / `memory` | `sql` (REQUIRED for multi-worker) |
| **`RATE_LIMIT_BACKEND`**| `NON_SECRET`| `memory` (DEFAULT) | `memory` (DEFAULT) | `redis` / `memory` | `redis` (REQUIRED for multi-worker) |
| **`PERSISTENCE_BACKEND`**| `NON_SECRET`| `memory` (DEFAULT) | `memory` (DEFAULT) | `redis` / `memory` | `redis` (RECOMMENDED) |
| **`AUDIT_STORE_BACKEND`**| `NON_SECRET`| `memory` (DEFAULT) | `memory` (DEFAULT) | `sql` / `memory` | `sql` (RECOMMENDED) |
| **`TRACING_ENABLED`** | `NON_SECRET` | `true` (DEFAULT) | `true` (DEFAULT) | `true` (DEFAULT) | `true` (DEFAULT) |

---

## 9. Secret Rotation Operational Procedure

1. **Provision New Secret**: Generate and register new API key / token in secret manager or credential store.
2. **Deploy Dual Authentication / Update Staging**: Verify connectivity and authentication in staging environment.
3. **Rolling Production Update**: Inject new secret into production environment variables / container environment via rolling pod restart.
4. **Verify Application Readiness**: Check `GET /ready` and `GET /admin/config` (confirming `secrets_status` indicates `configured`).
5. **Revoke Old Secret**: Revoke previous credential in upstream identity provider / NVIDIA console.

---

## 10. Container & CI/CD Security

1. **Docker Security (`Dockerfile` & `.dockerignore`)**:
   - Multi-stage build running as non-root user `paypilot:paypilot`.
   - `.env` and local credential files strictly excluded via `.dockerignore`.
   - Runtime configuration injected via container environment variables (`docker-compose.yml`).
2. **CI/CD Security (`.github/workflows/ci.yml`)**:
   - Automated testing executes with `NVIDIA_API_KEY=""` to guarantee 100% offline test execution with zero credential leakage.
   - Build step compiles and verifies containers without baking secrets into image layers.

---

## 11. Configuration Microbenchmark Results

```
==========================================================================================
         PAYPILOT CONFIGURATION & SECRETS BENCHMARK (PHASE 20)
==========================================================================================
Configuration Loading (1,000 iterations):
  - Mean: 0.0584 ms | Median: 0.0506 ms | P95: 0.0935 ms

Strong Typing & Compatibility Validation (1,000 iterations):
  - Mean: 0.0013 ms | Median: 0.0013 ms | P95: 0.0017 ms

SecretProvider Lookups (1,000 iterations):
  - Mean: 0.0017 ms | Median: 0.0016 ms | P95: 0.0018 ms

Sanitized Snapshot Generation (1,000 iterations):
  - Mean: 0.0079 ms | Median: 0.0076 ms | P95: 0.0087 ms
==========================================================================================
```

---

## 12. Limitations & Production Considerations

1. **Environment-Based Secret Resolution**: Current `EnvironmentSecretProvider` loads from OS environment variables. For Kubernetes clusters or cloud infrastructure, a dedicated Vault or AWS/GCP/Azure `SecretProvider` plugin can be registered.
2. **No Unsafe In-Memory Dynamic Hot-Reload**: Configuration is effectively immutable after startup to prevent race conditions during request execution; secret changes require a graceful rolling process restart.
