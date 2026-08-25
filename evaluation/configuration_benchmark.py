"""PayPilot Production Configuration & Secrets Management Benchmark (Phase 20).

Measures local micro-benchmarks for:
1. Environment loading and parsing latency
2. Strong typing & cross-backend validation latency
3. SecretProvider lookup latency
4. Redacted snapshot & diagnostics generation latency
"""

import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from backend.config import (
    CONFIG_SCHEMA_VERSION,
    EnvironmentSecretProvider,
    PayPilotSettings,
    get_config_diagnostics,
    get_secret_provider,
    safe_config_snapshot,
)


def benchmark_config_loading(iterations: int = 1000) -> Dict[str, float]:
    """Measures latency of loading settings from environment."""
    latencies: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        PayPilotSettings.load_from_environment()
        latencies.append((time.perf_counter() - t0) * 1000.0)

    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(latencies), 4),
        "median_ms": round(statistics.median(latencies), 4),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 4),
        "p99_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 4),
        "min_ms": round(min(latencies), 4),
        "max_ms": round(max(latencies), 4),
    }


def benchmark_validation(iterations: int = 1000) -> Dict[str, float]:
    """Measures latency of performing strong typing & cross-backend validation."""
    settings = PayPilotSettings.load_from_environment()
    latencies: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        settings.validate()
        latencies.append((time.perf_counter() - t0) * 1000.0)

    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(latencies), 4),
        "median_ms": round(statistics.median(latencies), 4),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 4),
        "p99_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 4),
        "min_ms": round(min(latencies), 4),
        "max_ms": round(max(latencies), 4),
    }


def benchmark_secret_lookup(iterations: int = 1000) -> Dict[str, float]:
    """Measures latency of SecretProvider secret resolution."""
    provider = get_secret_provider()
    latencies: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        provider.get_secret("NVIDIA_API_KEY")
        provider.get_secret("DATABASE_URL")
        provider.get_secret("PAYPILOT_ADMIN_KEY")
        latencies.append((time.perf_counter() - t0) * 1000.0)

    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(latencies), 4),
        "median_ms": round(statistics.median(latencies), 4),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 4),
        "p99_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 4),
        "min_ms": round(min(latencies), 4),
        "max_ms": round(max(latencies), 4),
    }


def benchmark_snapshot_generation(iterations: int = 1000) -> Dict[str, float]:
    """Measures latency of generating safe, sanitized configuration snapshots."""
    settings = PayPilotSettings.load_from_environment()
    latencies: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        safe_config_snapshot(settings)
        latencies.append((time.perf_counter() - t0) * 1000.0)

    return {
        "iterations": iterations,
        "mean_ms": round(statistics.mean(latencies), 4),
        "median_ms": round(statistics.median(latencies), 4),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 4),
        "p99_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 4),
        "min_ms": round(min(latencies), 4),
        "max_ms": round(max(latencies), 4),
    }


def run_configuration_benchmark() -> Dict[str, Any]:
    print("\n" + "=" * 90)
    print("         PAYPILOT CONFIGURATION & SECRETS BENCHMARK (PHASE 20)              ")
    print("          [LOCAL MICROBENCHMARK — NOT A CLOUD SLA GUARANTEE]                ")
    print("=" * 90)

    # 1. Warm-up
    print("\n[1/5] Warming up Python runtime and environment parsing...")
    PayPilotSettings.load_from_environment().validate()
    safe_config_snapshot()

    # 2. Config Loading Benchmark
    print("[2/5] Benchmarking Configuration Loading (1,000 iterations)...")
    load_stats = benchmark_config_loading(1000)
    print(f"      Mean: {load_stats['mean_ms']:.4f} ms | Median: {load_stats['median_ms']:.4f} ms | P95: {load_stats['p95_ms']:.4f} ms")

    # 3. Validation Benchmark
    print("[3/5] Benchmarking Strong Typing & Compatibility Validation (1,000 iterations)...")
    val_stats = benchmark_validation(1000)
    print(f"      Mean: {val_stats['mean_ms']:.4f} ms | Median: {val_stats['median_ms']:.4f} ms | P95: {val_stats['p95_ms']:.4f} ms")

    # 4. Secret Lookup Benchmark
    print("[4/5] Benchmarking SecretProvider Lookups (1,000 iterations)...")
    sec_stats = benchmark_secret_lookup(1000)
    print(f"      Mean: {sec_stats['mean_ms']:.4f} ms | Median: {sec_stats['median_ms']:.4f} ms | P95: {sec_stats['p95_ms']:.4f} ms")

    # 5. Snapshot Generation Benchmark
    print("[5/5] Benchmarking Sanitized Snapshot Generation (1,000 iterations)...")
    snap_stats = benchmark_snapshot_generation(1000)
    print(f"      Mean: {snap_stats['mean_ms']:.4f} ms | Median: {snap_stats['median_ms']:.4f} ms | P95: {snap_stats['p95_ms']:.4f} ms")

    report = {
        "benchmark": "configuration_and_secrets_management",
        "schema_version": CONFIG_SCHEMA_VERSION,
        "measurements": {
            "config_loading": load_stats,
            "config_validation": val_stats,
            "secret_lookup": sec_stats,
            "snapshot_generation": snap_stats,
        },
        "disclaimer": "Local microbenchmark measurements obtained in an offline test harness.",
    }

    report_path = ROOT_DIR / "evaluation" / "configuration_benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 90)
    print(f"Configuration benchmark report successfully saved to: {report_path}")
    print("=" * 90 + "\n")
    return report


if __name__ == "__main__":
    run_configuration_benchmark()
