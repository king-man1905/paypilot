# PayPilot Phase 22 — Production Deployment, Containerization & Graceful Shutdown

## 1. Architectural Overview & Objective

Phase 22 prepares PayPilot for enterprise production deployment without altering established multi-agent LangGraph workflows, dataset analytics, or security perimeter abstractions.

```
+-----------------------------------------------------------------------------------------+
|                                PayPilot Docker Environment                              |
|                                                                                         |
|  +---------------------+      +------------------------+      +----------------------+  |
|  |    PostgreSQL 16    |      |      PayPilot API      |      |       Redis 7        |  |
|  |  (Transactions,     |<---->|    (FastAPI Lifespan,  |<---->|  (Sliding Rate Limit,|  |
|  |   Jobs, Audit Store)|      |     Bounded Semaphore, |      |   Quotas, Idempotency|  |
|  |  Named Vol: pgdata  |      |     JobRunner Fleet)   |      |   Named Vol: data)   |  |
|  +---------------------+      +------------------------+      +----------------------+  |
|             ^                              |                             ^              |
|             |                              v                             |              |
|      [pg_isready check]            [/health & /ready]             [redis-cli ping]      |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Status Demarcation & Production Scope

| Subsystem / Capability | Verification Status | Operational Reality & Scope |
| :--- | :--- | :--- |
| **Multi-Stage Dockerfile** | **CONTAINER TESTED** | Multi-stage build with Python 3.13-slim, non-root user `paypilot` (UID 10001), healthcheck. |
| **Docker Compose Environment** | **LOCAL PRODUCTION-LIKE SIMULATION** | 3-tier topology (API, PostgreSQL, Redis) with persistent named volumes and healthcheck readiness. |
| **FastAPI Lifespan Lifecycle** | **TESTED LOCALLY** | Clean `@asynccontextmanager` startup validation and shutdown teardown. |
| **Graceful Shutdown & Drain** | **TESTED LOCALLY** | Orderly drain sequence: stop traffic admission, drain active tasks, close DB pools, flush buffers. |
| **JobRunner Drain Lifecycle** | **TESTED LOCALLY** | States: `RUNNING` -> `DRAINING` -> `STOPPED`. Rejects new jobs with 503 while allowing running jobs to finish. |
| **Lease Crash Recovery** | **TESTED LOCALLY** | Interrupted jobs remain recoverable via Phase 17 lease timeout claims. |
| **Database Migrations** | **TESTED LOCALLY** | Idempotent schema creation (`Base.metadata.create_all`) and deterministic CSV seeding. |
| **Multi-Node Cloud / K8s** | **NOT PRODUCTION VALIDATED** | True distributed HA, auto-scaling, and cloud ingress require live Kubernetes cluster validation. |

---

## 3. Containerization Architecture

### Multi-Stage Dockerfile (`Dockerfile`)
- **Stage 1 (Builder)**: Compiles wheels and installs build dependencies cleanly into `/install`.
- **Stage 2 (Runner)**: Minimal `python:3.13-slim` base image.
- **Security & Privileges**:
  - Non-root user: `paypilot:paypilot` (UID:GID `10001:10001`).
  - No secret variables baked into container filesystem.
  - `.dockerignore` excludes `.env`, `*.db`, `data/backups/`, `.git/`, and caches.
- **Healthcheck**: Configured to probe `http://localhost:8000/health` with 30s interval, 5s timeout, 3 retries.
- **Entrypoint**: `CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]`.

---

## 4. Docker Compose Simulation (`docker-compose.yml`)

The compose stack operates as a **LOCAL PRODUCTION-LIKE SIMULATION** containing:
1. **`postgres`**: PostgreSQL 16 Alpine container with named persistent volume `paypilot_postgres_data` and healthcheck `pg_isready`.
2. **`redis`**: Redis 7 Alpine container with persistent AOF storage `paypilot_redis_data` and healthcheck `redis-cli ping`.
3. **`paypilot-api`**: FastAPI application service waiting on `postgres` and `redis` health before launching.

---

## 5. Graceful Shutdown & Drain Semantics

### Lifecycle Sequence on SIGTERM / SIGINT
1. **Initiation**: FastAPI `lifespan` triggers `execute_graceful_shutdown()`.
2. **Readiness Probe Transition**: `set_shutting_down(True)` causes `/ready` to return **HTTP 503 Service Unavailable** immediately, alerting ingress routers to divert new traffic.
3. **Liveness Probe**: `/health` continues returning HTTP 200 OK during the drain window so orchestrators do not kill the container prematurely.
4. **Job Admission Rejection**: Incoming job submissions trigger `JobRunnerDrainingError` -> **HTTP 503 Service Unavailable** with `Retry-After: 15`.
5. **Worker Task Drain**: `runner.drain(timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS)` awaits in-flight tasks until completed or timeout expires.
6. **Crash & Recovery Guarantee**: Any uncompleted or interrupted tasks retain their `QUEUED` / `RUNNING` lease record, allowing a subsequent or peer worker to reclaim them via Phase 17 lease recovery.
7. **Resource Disposal**: SQLAlchemy connection pool (`engine.dispose()`) and Redis connections are closed cleanly.
8. **Logging & Metric Flush**: Summary of drain duration and job completion is emitted to stdout.

---

## 6. Resource Configuration & Connection Math

### Environment Variables & Settings Reference
| Variable | Default | Type | Description |
| :--- | :--- | :--- | :--- |
| `PAYPILOT_ENV` | `development` | String | Environment profile (`development`, `test`, `staging`, `production`) |
| `APP_WORKERS` | `1` | Integer | Number of Uvicorn ASGI application worker processes (1–32) |
| `JOB_MAX_WORKERS` | `3` | Integer | Thread worker pool size per application process (1–64) |
| `JOB_MAX_QUEUE_SIZE` | `50` | Integer | Maximum pending/running background jobs before backpressure 429 |
| `SHUTDOWN_TIMEOUT_SECONDS` | `15.0` | Float | Maximum drain duration allowed before process exits (1.0–300.0s) |
| `DB_POOL_SIZE` | `5` | Integer | SQLAlchemy connection pool size per application process |
| `DB_MAX_OVERFLOW` | `10` | Integer | Maximum burst connection overflow per process |
| `DATA_BACKEND` | `csv` | String | Data storage backend (`csv`, `sqlite`, `postgres`) |
| `JOB_STORE_BACKEND` | `memory` | String | Job persistence backend (`memory`, `sql`) |
| `RATE_LIMIT_BACKEND` | `memory` | String | Rate limiting store (`memory`, `redis`) |
| `IDEMPOTENCY_STORE_BACKEND` | `memory` | String | Idempotency record backend (`memory`, `redis`) |

### Database Connection Sizing Formula
$$\text{Total DB Connections} \approx \text{APP\_WORKERS} \times (\text{DB\_POOL\_SIZE} + \text{DB\_MAX\_OVERFLOW})$$
*Example*: With `APP_WORKERS=4`, `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=10`, the maximum concurrent database connections required on PostgreSQL is $4 \times (5 + 10) = 60$ connections. PostgreSQL's `max_connections` setting must be configured to at least $60 + 10 = 70$.

---

## 7. Database Migration Lifecycle

The migration system (`backend/storage/migrator.py`) provides safe, idempotent schema provisioning:
- **Idempotent DDL**: `Base.metadata.create_all(engine)` creates missing tables (`merchant_transactions`, `paypilot_jobs`, `paypilot_audit_events`, `paypilot_backup_metadata`) without modifying existing data.
- **Conditional Seeding**: Inspects `SELECT COUNT(*)` on `merchant_transactions` and only populates data if the table is empty (unless explicit `overwrite=True` is provided).
- **CLI Migration**: Can be run via `python -m backend.storage.migrator` during pre-deployment CI/CD jobs.
