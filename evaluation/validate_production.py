"""PayPilot Automated Production Validation & CI Verification Engine (Phase 22).

[LOCAL PRODUCTION READINESS VALIDATION / SIMULATION]

Orchestrates automated end-to-end regression, security checks, benchmark suites,
container static validation, health/readiness probe lifecycle, and graceful shutdown.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List

# Ensure repository root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paypilot.validation.production")

import tempfile

REPORT_PATH = Path(__file__).resolve().parent / "production_validation_report.json"


def run_step(name: str, cmd: List[str], cwd: Path = ROOT_DIR) -> Dict[str, Any]:
    """Executes a validation command step and captures execution metrics."""
    logger.info(f"Running step: {name} ({' '.join(cmd)})...")
    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stdout_f, \
             tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stderr_f:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=stdout_f,
                stderr=stderr_f,
                timeout=300,
            )
            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            stdout_f.seek(0)
            stderr_f.seek(0)
            out = stdout_f.read()
            err = stderr_f.read()
            passed = proc.returncode == 0
            return {
                "step": name,
                "command": " ".join(cmd),
                "status": "PASSED" if passed else "FAILED",
                "return_code": proc.returncode,
                "duration_ms": duration_ms,
                "stdout_tail": out[-300:] if out else "",
                "stderr_tail": err[-300:] if err else "",
            }
    except Exception as e:
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "step": name,
            "command": " ".join(cmd),
            "status": "FAILED",
            "return_code": -1,
            "duration_ms": duration_ms,
            "error": str(e),
        }


def validate_docker_configuration() -> Dict[str, Any]:
    """Statically validates Dockerfile and docker-compose.yml configurations."""
    t0 = time.perf_counter()
    issues = []

    # 1. Check Dockerfile
    dockerfile_path = ROOT_DIR / "Dockerfile"
    if not dockerfile_path.exists():
        issues.append("Dockerfile not found")
    else:
        content = dockerfile_path.read_text(encoding="utf-8")
        if "USER paypilot" not in content and "USER paypilot:paypilot" not in content:
            issues.append("Dockerfile does not switch to non-root user 'paypilot'")
        if "EXPOSE 8000" not in content:
            issues.append("Dockerfile missing explicit EXPOSE 8000")
        if "HEALTHCHECK" not in content:
            issues.append("Dockerfile missing HEALTHCHECK instruction")

    # 2. Check docker-compose.yml
    compose_path = ROOT_DIR / "docker-compose.yml"
    if not compose_path.exists():
        issues.append("docker-compose.yml not found")
    else:
        compose_content = compose_path.read_text(encoding="utf-8")
        if "postgres:" not in compose_content:
            issues.append("docker-compose.yml missing postgres service")
        if "redis:" not in compose_content:
            issues.append("docker-compose.yml missing redis service")
        if "paypilot-api:" not in compose_content:
            issues.append("docker-compose.yml missing paypilot-api service")
        if "healthcheck:" not in compose_content:
            issues.append("docker-compose.yml missing healthcheck definitions")

    # 3. Check .dockerignore
    ignore_path = ROOT_DIR / ".dockerignore"
    if not ignore_path.exists():
        issues.append(".dockerignore not found")
    else:
        ignore_content = ignore_path.read_text(encoding="utf-8")
        if ".env" not in ignore_content:
            issues.append(".dockerignore does not exclude .env")

    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    passed = len(issues) == 0

    return {
        "step": "Docker & Compose Static Configuration Validation",
        "command": "internal_static_check",
        "status": "PASSED" if passed else "FAILED",
        "return_code": 0 if passed else 1,
        "duration_ms": duration_ms,
        "issues": issues,
    }


def run_pytest_step() -> Dict[str, Any]:
    """Runs pytest regression suite in-process for speed and deterministic execution."""
    logger.info("Running step: Pytest Regression Test Suite...")
    import pytest
    t0 = time.perf_counter()
    code = pytest.main(["-q"])
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    return {
        "step": "Pytest Regression Test Suite",
        "command": "pytest.main(['-q'])",
        "status": "PASSED" if code == 0 else "FAILED",
        "return_code": int(code),
        "duration_ms": duration_ms,
    }


def run_production_validation() -> Dict[str, Any]:
    """Runs all 12 production readiness validation steps."""
    logger.info("==========================================================================")
    logger.info("            PAYPILOT PRODUCTION VALIDATION & CI VERIFICATION")
    logger.info("==========================================================================")

    steps = []

    # Step 1: Docker static validation
    steps.append(validate_docker_configuration())

    # Step 2: Full Pytest Test Suite
    steps.append(run_pytest_step())

    # Step 3: 32-case Offline Multi-Agent Evaluation
    steps.append(run_step("32-case Offline Multi-Agent Evaluation", [sys.executable, "evaluation/run_evaluation.py"]))

    # Step 4: Configuration Validation & Security
    steps.append(run_step("Configuration & Secret Boundary Benchmark", [sys.executable, "evaluation/configuration_benchmark.py"]))

    # Step 5: Traffic Control & Idempotency Benchmark
    steps.append(run_step("Traffic Control & Concurrency Idempotency Benchmark", [sys.executable, "evaluation/traffic_benchmark.py"]))

    # Step 6: Distributed Job & Lease Recovery Benchmark
    steps.append(run_step("Distributed Job & Multi-Worker Benchmark", [sys.executable, "evaluation/distributed_benchmark.py"]))

    # Step 7: Disaster Recovery & Parity Verification
    steps.append(run_step("Disaster Recovery & Financial Parity Benchmark", [sys.executable, "evaluation/disaster_recovery_benchmark.py"]))

    # Step 8: Distributed Tracing Benchmark
    steps.append(run_step("Distributed Tracing & Overhead Benchmark", [sys.executable, "evaluation/tracing_benchmark.py"]))

    # Step 9: SLO Evaluation Benchmark
    steps.append(run_step("SLO Evaluation Benchmark", [sys.executable, "evaluation/slo_benchmark.py"]))

    # Step 10: Performance Benchmark
    steps.append(run_step("Async Performance & Latency Benchmark", [sys.executable, "evaluation/performance_benchmark.py"]))

    # Step 11: Job Runner Throughput Benchmark
    steps.append(run_step("Background Job Throughput Benchmark", [sys.executable, "evaluation/job_benchmark.py"]))

    # Step 12: Graceful Shutdown & Drain Benchmark
    steps.append(run_step("Graceful Shutdown & Drain Microbenchmark", [sys.executable, "evaluation/shutdown_benchmark.py"]))

    # Step 13: Release Engineering, Versioned Migrations & Rollback Benchmark
    steps.append(run_step("Release Engineering & Safe Rollback Benchmark", [sys.executable, "evaluation/release_benchmark.py"]))

    # Step 14: Kubernetes Deployment & Manifest Validation Benchmark
    steps.append(run_step("Kubernetes Deployment & Manifest Benchmark", [sys.executable, "evaluation/kubernetes_deployment_benchmark.py"]))

    # Step 15: Kubernetes Failure & HA Recovery Benchmark
    steps.append(run_step("Kubernetes Failure & HA Recovery Benchmark", [sys.executable, "evaluation/kubernetes_failure_benchmark.py"]))

    all_passed = all(s["status"] == "PASSED" for s in steps)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "validation_environment": "LOCAL PRODUCTION READINESS VALIDATION / SIMULATION",
        "overall_status": "PASSED" if all_passed else "FAILED",
        "total_steps": len(steps),
        "passed_steps": sum(1 for s in steps if s["status"] == "PASSED"),
        "failed_steps": sum(1 for s in steps if s["status"] == "FAILED"),
        "steps": steps,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info(f"Production validation report written to {REPORT_PATH}")
    return report


if __name__ == "__main__":
    rep = run_production_validation()
    print("\n" + "=" * 90)
    print("                     PAYPILOT PRODUCTION READINESS VERIFICATION SUMMARY")
    print("=" * 90)
    print(f"Overall Status   : {rep['overall_status']}")
    print(f"Steps Passed     : {rep['passed_steps']} / {rep['total_steps']}")
    print("=" * 90)
    for s in rep["steps"]:
        status_sym = "[OK]" if s["status"] == "PASSED" else "[FAILED]"
        print(f" {status_sym:<8} | {s['step']:<52} | {s['duration_ms']:>8.1f} ms")
    print("=" * 90)
    if rep["overall_status"] != "PASSED":
        sys.exit(1)
    sys.exit(0)
