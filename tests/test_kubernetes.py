"""PayPilot Phase 24 — Kubernetes, Cloud-Native Orchestration & HA Test Suite.

Validates:
1. Manifest existence, syntax, and schema completeness in k8s/
2. Namespace isolation, ConfigMaps, and Secret placeholder hygiene
3. Security contexts, non-root UID 10001, and capability dropping
4. Liveness, readiness, and startup probe configurations
5. Rolling update strategies and PodDisruptionBudgets
6. StatefulSet PVC definitions and Service ClusterIP configurations
7. Cluster database connection pool math and capacity safety checks
8. Lease recovery and graceful shutdown probe transitions
9. HorizontalPodAutoscaler specifications and immutable image references
10. Redis transient fallback and zero secret leakage across manifests
"""

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml
from starlette import status
from starlette.testclient import TestClient

from backend.api.main import app, set_shutting_down
from backend.config import calculate_total_db_connections, validate_cluster_db_capacity
from backend.jobs.models import JobRecord, JobStatus
from backend.jobs.store import InMemoryJobStore
from backend.security.rate_limiter import InMemoryRateLimiter, RedisRateLimiter

ROOT_DIR = Path(__file__).resolve().parent.parent
K8S_DIR = ROOT_DIR / "k8s"


@pytest.fixture(autouse=True)
def reset_lifecycle_state():
    """Ensures clean application lifecycle state before and after each test."""
    set_shutting_down(False)
    yield
    set_shutting_down(False)


class TestKubernetesManifestStructure:
    """Validates presence and YAML parsing of all required Kubernetes manifests."""

    EXPECTED_FILES = [
        "namespace.yaml",
        "configmap.yaml",
        "secrets.example.yaml",
        "api-deployment.yaml",
        "api-service.yaml",
        "worker-deployment.yaml",
        "postgres-statefulset.yaml",
        "postgres-service.yaml",
        "redis-deployment.yaml",
        "redis-service.yaml",
        "api-pdb.yaml",
        "worker-pdb.yaml",
        "api-hpa.yaml",
        "migration-job.yaml",
        "kustomization.yaml",
    ]

    def test_all_manifests_exist(self):
        for fname in self.EXPECTED_FILES:
            path = K8S_DIR / fname
            assert path.exists(), f"Missing required Kubernetes manifest: {fname}"

    def test_all_manifests_valid_yaml(self):
        for fname in self.EXPECTED_FILES:
            path = K8S_DIR / fname
            with open(path, "r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
                assert len(docs) > 0, f"Manifest {fname} produced empty document list"
                for doc in docs:
                    if isinstance(doc, dict) and "kind" in doc:
                        if doc.get("kind") != "Kustomization":
                            assert doc.get("metadata", {}).get("name") is not None

    def test_kustomization_references_all_manifests(self):
        with open(K8S_DIR / "kustomization.yaml", "r", encoding="utf-8") as f:
            kust = yaml.safe_load(f)
        assert kust["kind"] == "Kustomization"
        assert kust["namespace"] == "paypilot"
        resources = kust.get("resources", [])
        for fname in self.EXPECTED_FILES:
            if fname not in ["secrets.example.yaml", "kustomization.yaml"]:
                assert fname in resources, f"Kustomization missing resource: {fname}"


class TestKubernetesWorkloadConfigurations:
    """Validates deployment specifications, security contexts, probes, and resources."""

    def test_namespace_isolation(self):
        with open(K8S_DIR / "namespace.yaml", "r", encoding="utf-8") as f:
            ns_doc = yaml.safe_load(f)
        assert ns_doc["kind"] == "Namespace"
        assert ns_doc["metadata"]["name"] == "paypilot"

    def test_configmap_data_integrity(self):
        with open(K8S_DIR / "configmap.yaml", "r", encoding="utf-8") as f:
            cm_doc = yaml.safe_load(f)
        assert cm_doc["kind"] == "ConfigMap"
        assert cm_doc["metadata"]["namespace"] == "paypilot"
        data = cm_doc["data"]
        assert data["PAYPILOT_ENV"] == "production"
        assert data["JOB_STORE_BACKEND"] == "sql"
        assert data["DATA_BACKEND"] == "postgres"
        assert data["RATE_LIMIT_BACKEND"] == "redis"

    def test_secrets_placeholder_hygiene(self):
        with open(K8S_DIR / "secrets.example.yaml", "r", encoding="utf-8") as f:
            secret_doc = yaml.safe_load(f)
        assert secret_doc["kind"] == "Secret"
        assert secret_doc["metadata"]["namespace"] == "paypilot"
        string_data = secret_doc["stringData"]
        for k, v in string_data.items():
            assert str(v).startswith("<inject-"), f"Secret {k} contains non-placeholder value: {v}"

    def test_api_deployment_specifications(self):
        with open(K8S_DIR / "api-deployment.yaml", "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        assert doc["kind"] == "Deployment"
        assert doc["spec"]["replicas"] == 2

        # Rolling update strategy
        strat = doc["spec"]["strategy"]["rollingUpdate"]
        assert strat["maxUnavailable"] == 0
        assert strat["maxSurge"] == 1

        pod_spec = doc["spec"]["template"]["spec"]
        assert pod_spec["terminationGracePeriodSeconds"] == 30

        # Security context
        sec = pod_spec["securityContext"]
        assert sec["runAsNonRoot"] is True
        assert sec["runAsUser"] == 10001

        container = pod_spec["containers"][0]
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert "ALL" in container["securityContext"]["capabilities"]["drop"]

        # Probes
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
        assert container["startupProbe"]["httpGet"]["path"] == "/health"

        # Resources
        res = container["resources"]
        assert res["requests"]["cpu"] == "250m"
        assert res["limits"]["memory"] == "1Gi"

    def test_worker_deployment_specifications(self):
        with open(K8S_DIR / "worker-deployment.yaml", "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        assert doc["kind"] == "Deployment"
        assert doc["spec"]["replicas"] == 2

        pod_spec = doc["spec"]["template"]["spec"]
        assert pod_spec["securityContext"]["runAsNonRoot"] is True
        assert pod_spec["terminationGracePeriodSeconds"] == 30

        container = pod_spec["containers"][0]
        assert container["command"] == ["python", "-m", "backend.jobs.runner"]
        assert container["resources"]["requests"]["cpu"] == "250m"

    def test_statefulset_and_services(self):
        with open(K8S_DIR / "postgres-statefulset.yaml", "r", encoding="utf-8") as f:
            pg_doc = yaml.safe_load(f)
        assert pg_doc["kind"] == "StatefulSet"
        assert len(pg_doc["spec"]["volumeClaimTemplates"]) > 0

        with open(K8S_DIR / "api-service.yaml", "r", encoding="utf-8") as f:
            svc_doc = yaml.safe_load(f)
        assert svc_doc["kind"] == "Service"
        assert svc_doc["spec"]["type"] == "ClusterIP"
        assert svc_doc["spec"]["ports"][0]["port"] == 8000

    def test_pod_disruption_budgets(self):
        for pdb_file in ["api-pdb.yaml", "worker-pdb.yaml"]:
            with open(K8S_DIR / pdb_file, "r", encoding="utf-8") as f:
                pdb = yaml.safe_load(f)
            assert pdb["kind"] == "PodDisruptionBudget"
            assert pdb["spec"]["minAvailable"] == 1

    def test_migration_job_specifications(self):
        with open(K8S_DIR / "migration-job.yaml", "r", encoding="utf-8") as f:
            job_doc = yaml.safe_load(f)
        assert job_doc["kind"] == "Job"
        assert job_doc["spec"]["backoffLimit"] == 3
        assert job_doc["spec"]["template"]["spec"]["restartPolicy"] == "OnFailure"

    def test_horizontal_pod_autoscaler(self):
        with open(K8S_DIR / "api-hpa.yaml", "r", encoding="utf-8") as f:
            hpa_doc = yaml.safe_load(f)
        assert hpa_doc["kind"] == "HorizontalPodAutoscaler"
        assert hpa_doc["spec"]["scaleTargetRef"]["name"] == "paypilot-api"
        assert hpa_doc["spec"]["minReplicas"] == 2
        assert hpa_doc["spec"]["maxReplicas"] == 10

    def test_immutable_image_tags(self):
        for fname in ["api-deployment.yaml", "worker-deployment.yaml", "migration-job.yaml"]:
            with open(K8S_DIR / fname, "r", encoding="utf-8") as f:
                doc = yaml.safe_load(f)
            container = doc["spec"]["template"]["spec"]["containers"][0]
            image = container["image"]
            assert ":" in image, f"Image {image} does not have a tag"
            assert not image.endswith(":latest"), f"Image {image} must use an immutable version tag, not :latest"

    def test_no_secret_leakage_in_manifests(self):
        forbidden_patterns = ["nvapi-", "sk-", "password123", "postgres_prod_secret", "redis_prod_pass"]
        for fname in TestKubernetesManifestStructure.EXPECTED_FILES:
            content = (K8S_DIR / fname).read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                assert pattern not in content, f"Possible secret leakage found in {fname}: {pattern}"


class TestDatabaseConnectionPoolSafety:
    """Validates cluster-wide connection calculations and safety boundaries."""

    def test_calculate_total_db_connections(self):
        # 2 API replicas * (5 pool + 10 overflow = 15) = 30
        # 2 Worker replicas * 3 workers = 6
        # Total = 36
        total = calculate_total_db_connections(
            api_replicas=2,
            worker_replicas=2,
            db_pool_size=5,
            db_max_overflow=10,
            worker_pool_size=3,
        )
        assert total == 36

    def test_validate_cluster_db_capacity_safe(self):
        check = validate_cluster_db_capacity(
            max_db_server_connections=100,
            api_replicas=2,
            worker_replicas=2,
        )
        assert check["is_safe"] is True
        assert check["total_required_connections"] == 36
        assert check["utilization_percent"] == 36.0

    def test_validate_cluster_db_capacity_unsafe(self):
        # 10 API replicas (150) + 10 Worker replicas (30) = 180 > 100 max
        check = validate_cluster_db_capacity(
            max_db_server_connections=100,
            api_replicas=10,
            worker_replicas=10,
        )
        assert check["is_safe"] is False
        assert check["total_required_connections"] == 180

    def test_cluster_db_capacity_scaling_boundaries(self):
        # 4 API replicas (60) + 4 Worker replicas (12) = 72 <= 80 safe limit of 100
        check_4x4 = validate_cluster_db_capacity(
            max_db_server_connections=100,
            api_replicas=4,
            worker_replicas=4,
        )
        assert check_4x4["is_safe"] is True
        assert check_4x4["total_required_connections"] == 72


class TestKubernetesRuntimeSimulations:
    """Validates worker lease recovery and probe transitions during shutdown."""

    def test_worker_crash_and_lease_recovery_simulation(self):
        import uuid
        store = InMemoryJobStore()
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = JobRecord(
            job_id=job_id,
            task_type="async_analysis",
            client_id="tenant_k8s_test",
            status=JobStatus.QUEUED.value,
            parameters={"query": "Simulate multi-worker lease recovery on node failure"},
        )
        store.save_job(job)

        # Worker A claims with 1-second lease
        worker_a = "worker_node_a"
        worker_b = "worker_node_b"
        claimed_a = store.claim_job(job_id=job_id, worker_id=worker_a, lease_timeout_seconds=1)
        assert claimed_a is True

        # Worker B cannot claim while lease is active (at-most-one active claim)
        claimed_early = store.claim_job(job_id=job_id, worker_id=worker_b, lease_timeout_seconds=1)
        assert claimed_early is False

        # Simulate Worker A crash (lease expires)
        import time
        time.sleep(1.05)

        # Worker B recovers job (at-least-once recovery)
        claimed_b = store.claim_job(job_id=job_id, worker_id=worker_b, lease_timeout_seconds=1)
        assert claimed_b is True

    def test_api_probe_transitions_during_shutdown(self):
        client = TestClient(app)

        # Normal running state
        set_shutting_down(False)
        r_health = client.get("/health")
        r_ready = client.get("/ready")
        assert r_health.status_code == status.HTTP_200_OK
        assert r_ready.status_code == status.HTTP_200_OK

        # During shutdown: readiness becomes 503, liveness stays 200
        set_shutting_down(True)
        r_health_sd = client.get("/health")
        r_ready_sd = client.get("/ready")
        assert r_health_sd.status_code == status.HTTP_200_OK
        assert r_ready_sd.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_redis_outage_fallback_simulation(self):
        # When Redis is unavailable or unconfigured, RedisRateLimiter gracefully falls back to in-memory sliding window
        limiter = RedisRateLimiter(redis_url="redis://invalid-host-for-testing:6379/0", default_limit=10, default_window=60)
        assert limiter._client is None
        allowed, retry_after = limiter.is_allowed("tenant_k8s_test")
        assert allowed is True
        assert retry_after == 0

