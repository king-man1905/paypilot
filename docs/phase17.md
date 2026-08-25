# PayPilot Phase 17: Distributed Scaling & Multi-Worker Architecture

---

## 1. Executive Summary & Problem Formulation
As PayPilot scales from a single-process service to multi-worker and horizontally-replicated deployments, understanding and managing application state becomes critical. Phase 17 answers the foundational architectural question:

> **"Which PayPilot state is process-local, which state must be shared, and what architecture is required for safe horizontal scaling?"**

Phase 17 audits all state components across PayPilot, analyzes multi-worker failure modes, introduces a shared `SQLJobStore` with atomic job leasing and crash recovery mechanics to prevent duplicate execution across workers, evaluates distributed rate limiting and metrics aggregation, models database connection pooling limits, and proves cluster safety through multi-worker simulation benchmarking.

---

## 2. Distributed State Audit & Classification

| Component / State | Classification | Storage Mechanism | Multi-Worker Behavior | Scaling Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Request Context & Headers** | **A. Stateless** | HTTP Request scope (`request.state`) | Isolated per request; no cross-worker dependency. | Fully horizontally scalable without shared state. |
| **Authentication Keys & RBAC** | **A. Stateless** | Environment variables (`PAYPILOT_API_KEY`) | Identical validation on every worker node. | Stateless validation; no shared session store required. |
| **Concurrency Guard** | **B. Process-Local** | `asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)` | Each worker throttles its own event loop independently. | Cluster capacity $= W \times \text{MAX\_CONCURRENT\_REQUESTS}$. |
| **Rate Limiter (Default)** | **B. Process-Local** | `InMemoryRateLimiter` (`collections.deque`) | Each worker maintains an independent sliding window. | Effective cluster quota $= W \times \text{LIMIT}$. |
| **Rate Limiter (Distributed)** | **C. Shared** | `RedisRateLimiter` (Redis ZSET) | Atomic shared sliding window across all workers. | Activated via `RATE_LIMIT_BACKEND=redis`. |
| **Telemetry Metrics (Default)** | **B. Process-Local** | `InMemoryMetricsStore` (Python Dict) | Worker 1 metrics $\neq$ Worker 2 metrics. | Per-node metrics for local scrape targets. |
| **Telemetry Metrics (Shared)** | **C. Shared** | `RedisMetricsStore` (Redis Hash / Atom) | Cluster-wide consolidated metric aggregation. | Activated via `PERSISTENCE_BACKEND=redis`. |
| **Audit Event Store (Default)** | **B. Process-Local** | `InMemoryAuditStore` (Bounded Ring Buffer) | Worker A holds only events processed on Worker A. | Bounded local buffer for single-node instances. |
| **Audit Event Store (Durable)** | **D. Durable** | Append-only File / SQL Table | Persistent historical compliance records. | Centralized database / log aggregation stream. |
| **Dataset In-Memory Cache** | **B. Process-Local** | Pandas DataFrame cache in RAM | Each worker process holds ~3.8 MB in RAM. | Total memory $= W \times 3.8\text{ MB}$. Invalidation per worker. |
| **Background Job Store (Default)**| **B. Process-Local** | `InMemoryJobStore` (OrderedDict) | Worker A cannot see jobs created on Worker B. | Suitable only for single-worker deployment. |
| **Background Job Store (Shared)** | **C. Shared / Durable**| `SQLJobStore` (SQLAlchemy `paypilot_jobs`) | All workers see all jobs; atomic claim prevents duplicates. | Activated via `JOB_STORE_BACKEND=sql`. |
| **Circuit Breaker** | **B. Process-Local** | `nvidia_circuit_breaker` state machine | Isolated blast radius per worker process. | Prevents cascading cross-node failure propagation. |
| **Transaction Data Layer** | **D. Durable** | PostgreSQL / SQLite (`merchant_transactions`) | Shared relational database with connection pooling. | Sized via $W \times (\text{pool\_size} + \text{max\_overflow})$. |

---

## 3. Job Execution Semantics & Worker Crash Recovery

### A. Atomic Claim vs. Execution Guarantees
- **Atomic Claim (At-Most-Once Active Claim)**:
  $$\text{UPDATE paypilot\_jobs SET status='running', worker\_id=:w, started\_at=:t WHERE job\_id=:id AND (status='queued' OR (status='running' AND started\_at <= :stale\_cutoff))}$$
  During any valid, active lease duration (`JOB_LEASE_TIMEOUT_SECONDS = 300s`), exactly **one** worker holds the active lease. No other worker can claim the job simultaneously.
- **Job Execution Semantics (At-Least-Once Under Failures)**:
  If a worker crashes or encounters a hardware failure while processing a job, the job is not lost forever. Once the lease expires (`now - started_at >= lease_timeout`), another worker can reclaim and re-execute the task. Therefore, system-wide job execution guarantees are **at-least-once**.
- **Important**: PayPilot does **NOT** claim *exactly-once* execution across arbitrary network and node crashes, as exactly-once execution requires end-to-end distributed transaction coordinators and strict two-phase commit mechanisms.

### B. Worker Crash Simulation & Recovery
1. **Normal Lifecycle**:
   `QUEUED` $\rightarrow$ (Atomic Claim by Worker A) $\rightarrow$ `RUNNING` $\rightarrow$ (Completion) $\rightarrow$ `COMPLETED`.
2. **Worker Crash & Recovery**:
   `QUEUED` $\rightarrow$ (Claimed by Worker A) $\rightarrow$ `RUNNING` $\rightarrow$ (Worker A crashes) $\rightarrow$ (Lease Expiration $\ge 300\text{s}$) $\rightarrow$ (Claimed by Worker B) $\rightarrow$ `RUNNING` $\rightarrow$ `COMPLETED`.

---

## 4. Configuration Modes: Local Default vs. Production Distributed

### Default Local Configuration (Offline & Fast Testing)
```ini
APP_WORKERS=1
JOB_STORE_BACKEND=memory
RATE_LIMIT_BACKEND=memory
PERSISTENCE_BACKEND=memory
DATA_BACKEND=csv
DATABASE_URL=sqlite:///data/processed/paypilot_transactions.db
```
- **Characteristics**: Zero external dependencies (no Redis/PostgreSQL needed), deterministic fallback, ultra-fast test execution.

### Production Distributed Configuration (Multi-Worker Scaled)
```ini
APP_WORKERS=4
JOB_STORE_BACKEND=sql
RATE_LIMIT_BACKEND=redis
PERSISTENCE_BACKEND=redis
DATA_BACKEND=postgres
DATABASE_URL=postgresql://paypilot_app:secure_password@postgres.internal:5432/paypilot_db
REDIS_URL=redis://redis.internal:6379/0
JOB_LEASE_TIMEOUT_SECONDS=300
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
```
- **Characteristics**: Shared relational job queue, cross-worker job polling, cluster-wide rate limiting, aggregated telemetry, automated worker crash recovery.

---

## 5. Database Sizing & Connection Pool Scaling

When running $W$ worker processes, each worker initializes its own SQLAlchemy connection pool:

$$\text{Total Maximum DB Connections} = W \times (\text{DB\_POOL\_SIZE} + \text{DB\_MAX\_OVERFLOW})$$

### Sizing Guidelines:
- **Default Settings**: `DB_POOL_SIZE = 5`, `DB_MAX_OVERFLOW = 10`.
- For $W = 4$ workers: Maximum potential database connections $= 4 \times (5 + 10) = 60\text{ connections}$.
- **PostgreSQL Max Connections**: Ensure PostgreSQL `max_connections` (typically 100 by default) exceeds the total pool capacity across all container replicas.
- **Enterprise Recommendation**: Deploy **PgBouncer** in transaction pooling mode when scaling beyond 8 workers to prevent connection exhaustion.

---

## 6. Circuit Breaker Blast Radius & Failure Domains

In PayPilot, the NVIDIA upstream circuit breaker remains **intentionally process-local**:
- **Advantage (Blast Radius Isolation)**: If a specific worker process experiences a transient network glitch or SSL handshake timeout to the NVIDIA API, only that worker trips its circuit breaker into `OPEN` fallback mode. Other healthy workers continue servicing live LLM traffic without cluster-wide degradation.
- **Self-Healing**: Each worker's circuit breaker independently evaluates half-open probe requests after `RECOVERY_TIMEOUT_SECONDS = 30.0s`.

---

## 7. Multi-Worker Simulation Benchmark Results

### Local Multi-Worker Matrix (`evaluation/distributed_benchmark.py`)
> [!NOTE]
> **Simulation Disclaimer**: The measurements below were executed in a local multi-threaded simulation using MockChatNVIDIA. They measure synchronization correctness, atomic lock contention, and worker recovery under varying loads. They do **NOT** represent real-world network capacity or production RPS guarantees.

| Workers | Jobs Batch | Completed | Failed | Duplicates | Recoveries | Mean Latency (ms) | P50 (ms) | P95 (ms) | Throughput (jobs/s) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | 20 | 20 | 0 | **0** | 0 | 111.79 | 45.10 | 379.37 | 7.49 |
| **2** | 50 | 50 | 0 | **0** | 0 | 213.84 | 185.20 | 423.19 | 7.79 |
| **4** | 20 | 20 | 0 | **0** | 0 | 524.71 | 480.12 | 1282.61 | 5.70 |
| **4** | 50 | 50 | 0 | **0** | 0 | 515.93 | 470.50 | 1046.27 | 5.94 |
| **4** | 100 | 100 | 0 | **0** | 0 | 485.15 | 450.30 | 1001.72 | 6.27 |
| **4** | 20 (crash simulation*) | 20 | 0 | **0** | **1** | 524.55 | 490.10 | 1077.86 | 5.83 |

*(\*) Worker crash simulation: A worker claims a job and halts. After lease expiration, a surviving worker claims, recovers, and completes the orphaned job.*

### Key Architectural Findings:
1. **Zero Duplicate Executions**: Across all 260 simulated task executions, duplicate runs remained strictly **0**.
2. **Crash Resilience**: Expired leases from crashed workers were 100% successfully recovered without manual operator intervention.
3. **Active Lease Protection**: While a worker held an active lease, sibling workers were 100% prevented from stealing or corrupting the in-flight job.
4. **Contention Behavior**: As worker count increased from 1 to 4 under single-threaded SQLite in-memory locking, SQLite lock serialization became the primary bottleneck, confirming the production necessity of PostgreSQL with row-level locks.

---

## 8. Production Capacity vs. Local Simulation Distinctions

- **Measured Local Simulation**: Proves algorithmic safety of atomic claims, lease timeouts, crash recovery, and queue isolation in Python memory space.
- **Actual Production Capacity**: Dependent on PostgreSQL connection pool concurrency, network round-trip latency to NVIDIA NIM endpoints, and API Gateway rate quotas (unknown from local offline benchmarks).
- **Production Recommendation**:
  - Deploy **4 Gunicorn/Uvicorn workers per container**.
  - Use **PostgreSQL with composite indexes** (`idx_job_tenant_status`, `idx_job_status_created`).
  - Deploy **PgBouncer** in front of PostgreSQL for connection multiplexing.
  - Use **Redis ZSET** for cluster-wide rate limiting (`RATE_LIMIT_BACKEND=redis`).

---

## 9. Verification & Regression Summary

1. **Pytest Test Suite**: **176/176 passed** (0 failures, 100% pass rate in 23.06s).
2. **Offline Evaluation Suite**: **32/32 benchmark cases passed** (100% pass rate, 0 live API calls).
3. **Performance Benchmark**: **101 requests executed with 0 failures** across sequential and concurrent loads.
4. **Distributed Benchmark Matrix**: **260 total workload jobs executed with 0 duplicate runs across 6 configurations**.
5. **Background Job Benchmark**: **25/25 jobs executed with 0 failures**.

