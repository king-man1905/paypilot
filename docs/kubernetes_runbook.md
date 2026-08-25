# PayPilot Kubernetes Operations & Cloud-Native Runbook

## 1. Prerequisites & Cluster Setup

### Recommended Local Cluster Tools
- **Kind**: `kind create cluster --name paypilot`
- **Minikube**: `minikube start --cpus 4 --memory 8192`

---

## 2. Namespace & Secret Provisioning

### Step 1: Create Namespace
```bash
kubectl apply -f k8s/namespace.yaml
```

### Step 2: Inject Deployment Secrets
Copy the template and replace placeholders with production secrets:
```bash
cp k8s/secrets.example.yaml k8s/secrets.prod.yaml
# Edit k8s/secrets.prod.yaml to insert real keys
kubectl apply -f k8s/secrets.prod.yaml
```

---

## 3. Database Migration Execution

Always run the versioned schema migration job before updating or promoting API/worker deployments:
```bash
# Run migration job
kubectl apply -f k8s/migration-job.yaml

# Wait for completion
kubectl wait --for=condition=complete --timeout=120s job/paypilot-migration-job -n paypilot
```

---

## 4. Deploying the Application Stack

### Deploying with Kustomize
```bash
kubectl apply -k k8s/
```

### Verifying Rollout Status
```bash
kubectl rollout status deployment/paypilot-api -n paypilot
kubectl rollout status deployment/paypilot-worker -n paypilot
kubectl rollout status statefulset/postgres -n paypilot
kubectl rollout status deployment/redis -n paypilot
```

---

## 5. Rolling Updates & Zero-Downtime Releases

To update the container image without downtime:
```bash
# Update API and Worker images
kubectl set image deployment/paypilot-api paypilot-api=paypilot:v1.25.0 -n paypilot
kubectl set image deployment/paypilot-worker paypilot-worker=paypilot:v1.25.0 -n paypilot

# Monitor rolling replacement (guaranteed maxUnavailable: 0)
kubectl rollout status deployment/paypilot-api -n paypilot
```

---

## 6. Incident Recovery & Troubleshooting

### Scenario A: API Pods Returning 503 Service Unavailable
1. Check if the application is in graceful shutdown / draining state:
   ```bash
   kubectl logs -n paypilot deployment/paypilot-api --tail=50
   ```
2. Verify dependency health:
   ```bash
   kubectl exec -it -n paypilot statefulset/postgres -- pg_isready -U paypilot -d paypilot
   kubectl exec -it -n paypilot deployment/redis -- redis-cli ping
   ```

### Scenario B: Worker Node Crash & Stale Lease Recovery
1. Worker failure recovery is automatic:
   Expired leases (`> JOB_LEASE_TIMEOUT_SECONDS = 300s`) will be reclaimed by surviving workers.
2. Inspect worker logs for lease recovery events:
   ```bash
   kubectl logs -n paypilot deployment/paypilot-worker | grep "Recovered stale running job lease"
   ```

### Scenario C: Unsafe Database Connection Exhaustion
1. Calculate current cluster connection demand:
   ```python
   from backend.config import calculate_total_db_connections
   print(calculate_total_db_connections(api_replicas=4, worker_replicas=4))
   ```
2. Scale down replicas or increase PostgreSQL `max_connections` if approaching limit.
