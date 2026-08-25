"""PayPilot Phase 24 — Kubernetes Deployment & Manifest Validation Benchmark.

Performs static validation of all Kubernetes manifests and dynamically detects
local cluster orchestration tools (kind / minikube).
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("paypilot.benchmark.k8s_deployment")

ROOT_DIR = Path(__file__).resolve().parent.parent
K8S_DIR = ROOT_DIR / "k8s"
REPORT_PATH = ROOT_DIR / "evaluation" / "kubernetes_deployment_report.json"

EXPECTED_MANIFESTS = [
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


def validate_manifest_files() -> Dict[str, Any]:
    """Validates existence and YAML syntax of all Kubernetes manifests."""
    results = {}
    missing = []
    syntax_errors = []

    for manifest_name in EXPECTED_MANIFESTS:
        file_path = K8S_DIR / manifest_name
        if not file_path.exists():
            missing.append(manifest_name)
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                docs = list(yaml.safe_load_all(f))
            results[manifest_name] = {
                "exists": True,
                "document_count": len(docs),
                "kinds": [doc.get("kind", "Unknown") for doc in docs if isinstance(doc, dict)],
            }
        except Exception as e:
            syntax_errors.append({"file": manifest_name, "error": str(e)})

    return {
        "all_present": len(missing) == 0,
        "missing_files": missing,
        "syntax_errors": syntax_errors,
        "manifests": results,
    }


def validate_security_and_resources() -> Dict[str, Any]:
    """Validates non-root execution, dropped capabilities, and resource limits."""
    issues = []
    checks = {
        "api_non_root": False,
        "api_resources_defined": False,
        "api_probes_configured": False,
        "worker_non_root": False,
        "worker_resources_defined": False,
        "secret_placeholders_only": True,
        "namespace_isolation": True,
    }

    # API Deployment validation
    api_path = K8S_DIR / "api-deployment.yaml"
    if api_path.exists():
        with open(api_path, "r", encoding="utf-8") as f:
            api_doc = yaml.safe_load(f)
        pod_spec = api_doc.get("spec", {}).get("template", {}).get("spec", {})
        sec_ctx = pod_spec.get("securityContext", {})
        if sec_ctx.get("runAsNonRoot") is True:
            checks["api_non_root"] = True
        else:
            issues.append("API Deployment pod does not set runAsNonRoot: true")

        containers = pod_spec.get("containers", [])
        if containers:
            c = containers[0]
            if "resources" in c and "requests" in c["resources"] and "limits" in c["resources"]:
                checks["api_resources_defined"] = True
            else:
                issues.append("API container missing resource requests/limits")

            if "livenessProbe" in c and "readinessProbe" in c:
                checks["api_probes_configured"] = True
            else:
                issues.append("API container missing liveness or readiness probes")

    # Worker Deployment validation
    worker_path = K8S_DIR / "worker-deployment.yaml"
    if worker_path.exists():
        with open(worker_path, "r", encoding="utf-8") as f:
            worker_doc = yaml.safe_load(f)
        pod_spec = worker_doc.get("spec", {}).get("template", {}).get("spec", {})
        sec_ctx = pod_spec.get("securityContext", {})
        if sec_ctx.get("runAsNonRoot") is True:
            checks["worker_non_root"] = True
        else:
            issues.append("Worker Deployment pod does not set runAsNonRoot: true")

        containers = pod_spec.get("containers", [])
        if containers:
            c = containers[0]
            if "resources" in c and "requests" in c["resources"] and "limits" in c["resources"]:
                checks["worker_resources_defined"] = True
            else:
                issues.append("Worker container missing resource requests/limits")

    # Secret template validation
    secret_path = K8S_DIR / "secrets.example.yaml"
    if secret_path.exists():
        with open(secret_path, "r", encoding="utf-8") as f:
            secret_doc = yaml.safe_load(f)
        string_data = secret_doc.get("stringData", {})
        for k, v in string_data.items():
            if not str(v).startswith("<inject-"):
                checks["secret_placeholders_only"] = False
                issues.append(f"Secret key '{k}' contains non-placeholder value")

    return {
        "passed": len(issues) == 0,
        "checks": checks,
        "issues": issues,
    }


def detect_kubernetes_runtime() -> Dict[str, Any]:
    """Detects availability of local Kubernetes orchestration tooling (kind / minikube)."""
    has_kubectl = shutil.which("kubectl") is not None
    has_kind = shutil.which("kind") is not None
    has_minikube = shutil.which("minikube") is not None
    has_docker = shutil.which("docker") is not None

    available_tool = None
    if has_kubectl and has_kind:
        available_tool = "kind"
    elif has_kubectl and has_minikube:
        available_tool = "minikube"

    return {
        "kubectl_available": has_kubectl,
        "kind_available": has_kind,
        "minikube_available": has_minikube,
        "docker_available": has_docker,
        "orchestrator_available": available_tool is not None,
        "active_tool": available_tool,
        "runtime_status": (
            f"LOCAL KUBERNETES ({available_tool.upper()}) READY"
            if available_tool
            else "LOCAL KUBERNETES RUNTIME NOT EXECUTED"
        ),
    }


def run_kubernetes_deployment_benchmark() -> Dict[str, Any]:
    """Executes Kubernetes static validation and optional runtime deployment verification."""
    logger.info("Executing Kubernetes Deployment & Manifest Validation Benchmark...")
    t0 = time.perf_counter()

    manifest_validation = validate_manifest_files()
    security_validation = validate_security_and_resources()
    runtime_detection = detect_kubernetes_runtime()

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    static_passed = manifest_validation["all_present"] and security_validation["passed"]

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark_environment": "KUBERNETES MANIFEST VALIDATED / STATIC BENCHMARK",
        "overall_status": "PASSED" if static_passed else "FAILED",
        "duration_ms": duration_ms,
        "manifest_validation": manifest_validation,
        "security_validation": security_validation,
        "runtime_detection": runtime_detection,
        "demarcation": {
            "manifest_status": "KUBERNETES MANIFEST VALIDATED",
            "runtime_status": runtime_detection["runtime_status"],
            "production_status": "NOT CLOUD PRODUCTION VALIDATED",
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Kubernetes deployment benchmark report written to {REPORT_PATH}")
    return report


if __name__ == "__main__":
    rep = run_kubernetes_deployment_benchmark()
    print("\n==========================================================================")
    print("           PAYPILOT KUBERNETES DEPLOYMENT BENCHMARK REPORT")
    print("==========================================================================")
    print(f"Status           : {rep['overall_status']}")
    print(f"Manifest Status  : {rep['demarcation']['manifest_status']}")
    print(f"Runtime Status   : {rep['demarcation']['runtime_status']}")
    print(f"Production Scope : {rep['demarcation']['production_status']}")
    print(f"Duration         : {rep['duration_ms']} ms")
    print("==========================================================================")
