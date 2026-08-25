# PayPilot Phase 24 — Kubernetes, High Availability & Cloud-Native Orchestration

## 1. Executive Summary & Architecture Overview

Phase 24 establishes a production-grade Kubernetes orchestration topology for PayPilot, enabling horizontal scaling across stateless API replicas and asynchronous background workers while maintaining strict transactional consistency, distributed cache coordination, and zero-downtime rolling updates.

```
                               Kubernetes Cluster (Namespace: paypilot)
                                                  |
                     +----------------------------+----------------------------+
                     |                                                         |
          PayPilot API Deployment                                   PayPilot Worker Deployment
          (Replicas: 2, RollingUpdate)                              (Replicas: 2, Scalable)
                     |                                                         |
                     |--- PodDisruptionBudget (minAvailable: 1)                |--- PodDisruptionBudget (minAvailable: 1)
                     |--- Probes (/health, /ready, startup)                    |--- SQL Job Store Backend
                     |--- Non-Root Container (UID 10001)                       |--- Shared Distributed Lock / Leases
                     |                                                         |
                     +----------------------------+----------------------------+
                                                  |
                     +----------------------------+----------------------------+
                     |                                                         |
           PostgreSQL StatefulSet                                   Redis Deployment
           (Durable Relational State)                               (Distributed Rate Limit & Quotas)
           - VolumeClaimTemplate (10Gi)                             - ClusterIP Service: 6379
           - ClusterIP Service: 5432                                - Probes (redis-cli ping)
           - Checksums & Migration Ledger                           - In-Memory Fallback Resilience
           [LOCAL DATABASE SIMULATION]                               [LOCAL SIMULATION]
                     |                                                         |
                     +----------------------------+----------------------------+
                                                  |
                                   Observability & OpenTelemetry
                               (Tracing / Prometheus Metrics / SLOs)
```

---

## 2. Kubernetes Manifest Hierarchy (`k8s/`)

| Manifest | Kind | Purpose & Key Configurations |
| :--- | :--- | :--- |
| [`namespace.yaml`](file:///e:/paypilot/k8s/namespace.yaml) | `Namespace` | Dedicated `paypilot` namespace for multi-tenant isolation. |
| [`configmap.yaml`](file:///e:/paypilot/k8s/configmap.yaml) | `ConfigMap` | Non-sensitive runtime settings (`PAYPILOT_ENV=production`, backends, timeouts, pool limits). |
| [`secrets.example.yaml`](file:///e:/paypilot/k8s/secrets.example.yaml) | `Secret` | Placeholder template for external secret injection (`<inject-at-deployment-time>`). Zero plaintext credentials. |
| [`api-deployment.yaml`](file:///e:/paypilot/k8s/api-deployment.yaml) | `Deployment` | 2 API replicas, rolling update strategy (`maxSurge: 1`, `maxUnavailable: 0`), non-root UID 10001, `/health` and `/ready` probes. |
| [`api-service.yaml`](file:///e:/paypilot/k8s/api-service.yaml) | `Service` | Internal `ClusterIP` exposing port 8000. |
| [`worker-deployment.yaml`](file:///e:/paypilot/k8s/worker-deployment.yaml) | `Deployment` | 2 Background worker replicas with shared SQL job persistence and distributed lease recovery. |
| [`postgres-statefulset.yaml`](file:///e:/paypilot/k8s/postgres-statefulset.yaml) | `StatefulSet` | `postgres:16-alpine` with 10Gi persistent volume claim template (`LOCAL DATABASE SIMULATION`). |
| [`postgres-service.yaml`](file:///e:/paypilot/k8s/postgres-service.yaml) | `Service` | Internal `ClusterIP` on port 5432. |
| [`redis-deployment.yaml`](file:///e:/paypilot/k8s/redis-deployment.yaml) | `Deployment` | `redis:7-alpine` for distributed rate limiting & quotas (`LOCAL SIMULATION`). |
| [`redis-service.yaml`](file:///e:/paypilot/k8s/redis-service.yaml) | `Service` | Internal `ClusterIP` on port 6379. |
| [`api-pdb.yaml`](file:///e:/paypilot/k8s/api-pdb.yaml) | `PodDisruptionBudget` | Guarantees `minAvailable: 1` during voluntary node drain/disruption. |
| [`worker-pdb.yaml`](file:///e:/paypilot/k8s/worker-pdb.yaml) | `PodDisruptionBudget` | Guarantees `minAvailable: 1` during voluntary worker disruption. |
| [`api-hpa.yaml`](file:///e:/paypilot/k8s/api-hpa.yaml) | `HorizontalPodAutoscaler` | Autoscaling from 2 to 10 replicas targeting 75% average CPU utilization. |
| [`migration-job.yaml`](file:///e:/paypilot/k8s/migration-job.yaml) | `Job` | Pre-deployment versioned database migration runner (`python -m backend.storage.migrator`). |
| [`kustomization.yaml`](file:///e:/paypilot/k8s/kustomization.yaml) | `Kustomization` | Unified deployment bundle and namespace targeting. |

---

## 3. Database Connection Pool Math & Safety Boundaries

In a distributed Kubernetes deployment, multiple API and Worker pods connect concurrently to PostgreSQL. The total theoretical peak connection count is governed by:

$$\text{Total DB Connections} \approx \text{API Replicas} \times (\text{DB\_POOL\_SIZE} + \text{DB\_MAX\_OVERFLOW}) + \text{Worker Replicas} \times (\text{JOB\_MAX\_WORKERS})$$

### Reference Cluster Capacity (2 API + 2 Worker Replicas)
- **API Pods (2 replicas)**: $2 \times (5 + 10) = 30\text{ connections}$
- **Worker Pods (2 replicas)**: $2 \times 3 = 6\text{ connections}$
- **Total Required Peak**: $36\text{ connections}$
- **PostgreSQL Max Connections**: $100$
- **Utilization**: $36.0\%$ (Well within the $80\%$ safe ceiling)

Safety validation helper in `backend/config.py`:
- `calculate_total_db_connections(api_replicas, worker_replicas)`
- `validate_cluster_db_capacity(max_db_server_connections, api_replicas, worker_replicas)`

---

## 4. Lifecycle, Probes & Graceful Termination

```
[Kubernetes Kubelet]                          [PayPilot API Pod]
        |                                             |
        | --- SIGTERM (Pre-stop / De-registration) --> |
        |                                             | ---> sets _shutting_down = True
        |                                             |
        | --- GET /ready (Readiness Probe) ---------> |
        | <--- 503 Service Unavailable -------------- | (Traffic stops arriving at pod)
        |                                             |
        | --- GET /health (Liveness Probe) ----------> |
        | <--- 200 OK ------------------------------- | (Kubelet does not SIGKILL pod)
        |                                             |
        |                                             | ---> In-flight HTTP requests complete
        |                                             | ---> Active background jobs drain cleanly
        |                                             | ---> Database & Redis connections close
        |                                             |
        | <--- Process exits cleanly (code 0) ------- |
```

---

## 5. Worker Failure Recovery & Lease Guarantees

Under Kubernetes pod preemption or ungraceful node crash:
1. **Worker A** claims a queued job with an explicit lease (`started_at = now`).
2. **Worker A crashes**: The job remains in `RUNNING` status with `worker_id = worker_a`.
3. **During Lease Window**: Other workers are locked out (**At-most-one active claim guarantee**).
4. **Lease Expiration**: Once `(now - started_at) >= JOB_LEASE_TIMEOUT_SECONDS`, the lease is considered stale.
5. **Worker B recovers**: Atomically acquires the stale lease, increments `retry_count`, and executes the job to completion (**At-least-once recovery guarantee**).

---

## 6. Scope & Production Limitations

| Subsystem / Capability | Verification Status | Operational Reality & Scope |
| :--- | :--- | :--- |
| **Kubernetes Manifests** | **KUBERNETES MANIFEST VALIDATED** | All 15 YAML manifests syntactically valid, schema-compliant, and secure. |
| **Local Failure Simulation** | **LOCAL FAILURE SIMULATION** | Worker crash lease recovery, probe transitions, and connection pool safety verified. |
| **PostgreSQL StatefulSet** | **LOCAL DATABASE SIMULATION** | Single-pod StatefulSet with PVC for local validation. Production requires cloud-managed RDS or HA Operator (Patroni/CloudNativePG). |
| **Redis Deployment** | **LOCAL SIMULATION** | Single-pod Redis with in-memory fallback. Production requires AWS ElastiCache, GCP Memorystore, or Redis Sentinel. |
| **Cloud Multi-Cluster** | **NOT CLOUD PRODUCTION VALIDATED** | Multi-region routing, global load balancing, and production SLA validation require live cloud deployment. |
