# PayPilot Production Operations & Deployment Runbook

## 1. Pre-Deployment Validation Checklist

Before initiating deployment to any staging or production-like environment, ensure:
1. **Regression Suite**: Run `python -m pytest -v` (Must report 288+ passed, 0 failed).
2. **Offline Evaluation**: Run `python evaluation/run_evaluation.py` (Must report 32/32 cases passed).
3. **Shutdown Microbenchmark**: Run `python evaluation/shutdown_benchmark.py` (Must report 0 lost jobs, 0 duplicates, 0 leaked slots).
4. **CI-Style Production Script**: Run `python evaluation/validate_production.py` (Must report overall status `PASSED`).
5. **Configuration Verification**: Ensure `PAYPILOT_ENV=production` has `REQUIRE_AUTH=true` and valid API keys configured.

---

## 2. Docker & Docker Compose Startup Sequence

### Option A: Local Production-Like Simulation (Docker Compose)
To start the 3-tier simulated environment (PostgreSQL 16, Redis 7, PayPilot API):

```bash
# 1. Build and launch containers in background
docker compose up --build -d

# 2. Monitor container status and healthchecks
docker compose ps

# 3. View structured application logs
docker compose logs -f paypilot-api
```

### Option B: Standalone Container Execution
```bash
# 1. Build production image
docker build -t paypilot:latest .

# 2. Run container with environment variables injected
docker run -d \
  --name paypilot-api \
  -p 8000:8000 \
  -e PAYPILOT_ENV=production \
  -e REQUIRE_AUTH=true \
  -e PAYPILOT_API_KEY=paypilot-prod-analyst-key \
  -e PAYPILOT_ADMIN_KEY=paypilot-prod-admin-key \
  -e NVIDIA_API_KEY=your_nvidia_api_key \
  paypilot:latest
```

---

## 3. Database Migration Execution

Database migrations and schema creation are idempotent and should be run prior to accepting live customer traffic:

```bash
# Run migration inside the container or via CLI
docker compose exec paypilot-api python -m backend.storage.migrator
```

Expected Output:
```json
{
  "status": "success",
  "schemas_created": ["merchant_transactions", "paypilot_jobs", "paypilot_audit_events", "paypilot_backup_metadata"],
  "transactions_count": 50000,
  "seeded": false
}
```

---

## 4. Health & Readiness Monitoring

| Purpose | Endpoint | Expected Status | Command |
| :--- | :--- | :--- | :--- |
| **Liveness Check** | `GET /health` | `HTTP 200 OK` | `curl -f http://localhost:8000/health` |
| **Readiness Check** | `GET /ready` | `HTTP 200 OK` | `curl -f http://localhost:8000/ready` |
| **Telemetry Metrics** | `GET /metrics` | `HTTP 200 OK` | `curl -H "X-Client-Id: admin" -H "X-Role: admin" http://localhost:8000/metrics` |
| **Diagnostics** | `GET /api/v1/diagnostics` | `HTTP 200 OK` | `curl -H "X-Client-Id: admin" -H "X-Role: admin" http://localhost:8000/api/v1/diagnostics` |

---

## 5. Graceful Teardown & Rolling Deployments

When updating or restarting PayPilot:
1. Send `SIGTERM` to the container (e.g. `docker stop -t 20 paypilot-api`).
2. The application marks itself as unready (`GET /ready` returns 503).
3. The load balancer stops routing new requests to the draining container.
4. Active background tasks drain within `SHUTDOWN_TIMEOUT_SECONDS` (default: 15s).
5. Database and Redis connections are cleanly disposed.
6. The process exits with code 0.

---

## 6. Troubleshooting & Recovery Procedures

### Issue 1: `/ready` returns HTTP 503
- **Cause A**: Dataset file not found at configured `DATA_PATH`. Verify CSV dataset or PostgreSQL table population.
- **Cause B**: Application is in shutdown or drain lifecycle.
- **Resolution**: Check `docker logs paypilot-api` and inspect `details.checks` in the response payload.

### Issue 2: `JobRunner` Queue Full (HTTP 429)
- **Cause**: Influx of background jobs exceeding `JOB_MAX_QUEUE_SIZE` (default: 50).
- **Resolution**: Increase `JOB_MAX_WORKERS` or `JOB_MAX_QUEUE_SIZE`, or scale worker nodes.

### Issue 3: Stale Worker Lease / Crash Recovery
- **Cause**: Node crashed while executing a background job.
- **Resolution**: Peer workers will automatically detect expired leases (`JOB_LEASE_TIMEOUT_SECONDS`, default: 300s) and reclaim them back to `QUEUED` or execute them without job loss.
