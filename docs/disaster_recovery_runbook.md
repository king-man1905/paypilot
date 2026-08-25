# PayPilot Disaster Recovery & Incident Response Runbook

---

## 1. Scope & Objective
This Standard Operating Procedure (SOP) governs disaster recovery (DR), database restoration, and data resilience for the PayPilot payment diagnostic platform. It outlines the end-to-end procedure for recovering from database corruption, node/container failures, Redis outages, and hardware crashes while guaranteeing financial data integrity and numerical truth.

---

## 2. Environment Architecture Overview

| Dimension | Local Development / Test | Production Multi-Worker |
| :--- | :--- | :--- |
| **Database Engine** | SQLite (`sqlite:///data/processed/paypilot_transactions.db`) | PostgreSQL 15+ (`postgresql://user:pass@host:5432/db`) |
| **Transaction Repository** | `CSVTransactionRepository` / `SQLTransactionRepository` | `SQLTransactionRepository` |
| **Job Store** | `InMemoryJobStore` / `SQLJobStore` | `SQLJobStore` (Shared relational table) |
| **Rate Limiter & Cache** | `InMemoryRateLimiter` | `RedisRateLimiter` (Redis cluster) |
| **Audit Repository** | `InMemoryAuditStore` | `SQLAuditStore` (`paypilot_audit_events`) |
| **Backup Storage** | Local directory (`data/backups/`) | Object Storage (AWS S3 / GCP GCS) + Local Volume |

---

## 3. Disaster Recovery Execution Steps

### Step 1: Detect Failure & Alert Triage
- Monitor `/health` and `/ready` probes.
- Identify failure symptoms:
  - `503 Service Unavailable` on `/ready` $\rightarrow$ Database unreachable or dataset missing.
  - Rate limiting failures or connection timeouts $\rightarrow$ Redis unreachable.
  - Upstream LLM timeouts $\rightarrow$ Circuit breaker active in deterministic fallback mode.

### Step 2: Identify Affected Subsystem
Run system configuration check and diagnostics:
```bash
# Check connectivity and storage health
python -c "from backend.storage import check_database_connection; print(check_database_connection())"
```

### Step 3: Stop Unsafe Writers (Drain Traffic)
Before restoring a database snapshot, prevent partial in-flight writes:
- Set load balancer / ingress traffic to `DRAIN` mode.
- In multi-worker deployments, scale application replicas to 0 or suspend worker background threads.

### Step 4: Restore Database

#### Local SQLite Environment
```python
from backend.storage.restore import restore_database_from_backup, list_backups

# Find latest verified backup
backups = list_backups()
latest_backup = backups[0]

# Restore with automated SHA-256 verification
restore_database_from_backup(latest_backup)
```

#### Production PostgreSQL Environment
```bash
# 1. Terminate existing connections to target database
psql -h $DB_HOST -U $DB_USER -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'paypilot_db' AND pid <> pg_backend_pid();"

# 2. Verify SHA-256 checksum of backup archive
sha256sum -c paypilot_db_backup_20260824.sql.gz.sha256

# 3. Restore database via pg_restore
pg_restore --clean --if-exists -h $DB_HOST -U $DB_USER -d paypilot_db -j 4 paypilot_db_backup_20260824.dump
```

### Step 5: Validate Restored Schema & Indexes
Verify that required tables (`merchant_transactions`, `paypilot_jobs`, `paypilot_audit_events`) and composite B-tree indexes exist:
```python
from backend.storage.connection import get_db_engine
from backend.storage.models import Base

# Ensure all relational tables and indexes are bound
Base.metadata.create_all(bind=get_db_engine())
```


### Step 6: Validate Row Counts & Primary Key Uniqueness
Verify that primary key duplicates $= 0$ and expected rows match backup manifest:
```python
from backend.storage.restore import validate_restore_integrity

result = validate_restore_integrity(expected_txns=15000)
assert result["valid"] is True
assert result["duplicate_primary_keys"] == 0
```

### Step 7: Validate Core Financial Parity (100.0% Numerical Truth)
Verify that all 12 analytical financial metrics match pre-backup baseline values:
```python
from backend.storage.restore import validate_restore_integrity, compute_core_financial_metrics

# Compute and compare
metrics = compute_core_financial_metrics()
print("Post-Restore Metrics Parity:", result["metrics_parity_pct"], "%")
assert result["metrics_parity_pct"] == 100.0
```

### Step 8: Recover Stale / Orphaned Jobs
If background workers were interrupted during failure, recover abandoned running jobs:
```python
from backend.jobs.store import SQLJobStore

store = SQLJobStore()
recovered_count = store.recover_stale_jobs(lease_timeout_seconds=300)
print(f"Successfully recovered {recovered_count} orphaned background jobs.")
```

### Step 9: Invalidate In-Memory Dataset Cache
Ensure worker processes discard stale in-memory cached frames:
```python
from backend.data.loader import clear_dataset_cache

clear_dataset_cache()
```

### Step 10: Re-enable Ingress Traffic & Verify Endpoints

1. **Verify `/health`**:
   ```bash
   curl -s http://localhost:8000/health | jq .
   # Expected: {"status": "ok"}
   ```

2. **Verify `/ready`**:
   ```bash
   curl -s http://localhost:8000/ready | jq .
   # Expected: {"status": "ready", "checks": {"database": "connected", "dataset": "ready"}}
   ```

3. **Verify `/metrics`**:
   ```bash
   curl -s http://localhost:8000/metrics | jq .
   ```

4. **Verify `/api/v1/analyze`**:
   ```bash
   curl -X POST http://localhost:8000/api/v1/analyze \
     -H "Content-Type: application/json" \
     -H "X-API-Key: test_analyst_key" \
     -d '{"query": "What is my total realized revenue?"}'
   ```

5. **Verify Security & Redaction**:
   - Ensure response headers contain `X-Request-ID`.
   - Ensure logs and audit tables do not contain API keys or raw tokens.

### Step 11: Record Post-Incident Report (PIR)
Document:
1. Incident start and resolution timestamps.
2. Root cause (hardware failure, network split, corrupt migration).
3. Data recovered and verified RPO/RTO achieved.
4. Corrective actions to prevent reoccurrence.

---

## 4. Subsystem Failure & Degradation Matrix

| Component | Failure Mode | Impact | Fallback / Recovery Strategy | Data Loss (RPO) | Recovery Time (RTO) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Primary Database** | Disk corruption / host crash | Read/write failure | Restore latest verified backup + WAL replay | $< 1\text{ hour}$ (Prod) / 0 (Local) | $< 15\text{ mins}$ |
| **Redis** | Node crash / network partition | Rate limiter & cache fail | Automatic fallback to process-local sliding window | Temporary shared rate quotas | $< 100\text{ ms}$ (auto-fallback) |
| **Worker Process** | OOM kill / segfault | In-flight job stalled | Surviving worker re-claims job after lease timeout (300s) | 0 (Job re-executed) | $< 300\text{ s}$ |
| **NVIDIA API** | Upstream 5xx / outage | LLM generation failure | Process-local circuit breaker engages deterministic heuristic engine | 0 (Deterministic fallback) | $< 500\text{ ms}$ |
