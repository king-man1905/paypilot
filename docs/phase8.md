# PayPilot Phase 8: Deployment & CI/CD Guide

---

## 1. Overview

Phase 8 provides production deployment artifacts and an automated CI/CD pipeline for the PayPilot Multi-Agent Engine. It establishes reproducible containerization, non-root security standards, automated healthchecks, and offline continuous integration guarantees.

```mermaid
graph LR
    Dev[Developer Commit] --> Git[Git Push]
    Git --> GHA[GitHub Actions CI/CD]
    
    subgraph CI Pipeline
        GHA --> Dep[Install Python 3.13 Deps]
        Dep --> Test[pytest -v<br>Unit & Integration Tests]
        Test --> Eval[run_evaluation.py<br>Offline Benchmark (32 Cases)]
        Eval --> Bench[benchmark.py<br>Latency & Perf Stats]
        Bench --> DBuild[Docker Image Build Validation]
    end
    
    DBuild --> Prod[Production Server / Docker Compose]
```

---

## 2. Local Production Server Run

To run the PayPilot API in production mode without `--reload`:

```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
```

### Production Runtime Options
- `--host 0.0.0.0`: Binds to all network interfaces for container and reverse proxy compatibility.
- `--port 8000`: Default PayPilot API listening port.
- `--workers 4`: (Optional) Spawns multiple worker processes for high-concurrency deployments.

---

## 3. Containerization with Docker

### A. Build the Production Image
```bash
docker build -t paypilot:latest .
```

### B. Run the Container
```bash
docker run -d \
  --name paypilot-api \
  -p 8000:8000 \
  --env-file .env \
  paypilot:latest
```

### Key Dockerfile Features
- **Base Image**: `python:3.13-slim` for minimal surface area and fast startup.
- **Security**: Runs under an unprivileged `paypilot` non-root system user.
- **Exclusion**: `.dockerignore` prevents `.env`, test caches, and git metadata from entering the image.
- **Liveness Healthcheck**: Periodic probe hitting `http://localhost:8000/health`.

---

## 4. Docker Compose Deployment

The provided `docker-compose.yml` orchestrates the PayPilot application service:

```yaml
version: '3.8'

services:
  paypilot-api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: paypilot-api
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - LLM_PROVIDER=${LLM_PROVIDER:-nvidia}
      - NVIDIA_API_KEY=${NVIDIA_API_KEY:-}
      - NVIDIA_MODEL=${NVIDIA_MODEL:-meta/llama-3.3-70b-instruct}
      - NVIDIA_BASE_URL=${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}
      - FASTAPI_HOST=0.0.0.0
      - FASTAPI_PORT=8000
      - DATA_SEED=${DATA_SEED:-42}
      - DATA_PATH=${DATA_PATH:-data/processed/merchant_transactions.csv}
      - LLM_REQUEST_TIMEOUT=${LLM_REQUEST_TIMEOUT:-25.0}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

### Launching with Docker Compose
```bash
# Build and start in background
docker compose up --build -d

# View real-time logs
docker compose logs -f

# Check container health status
docker compose ps

# Tear down service
docker compose down
```

---

## 5. Environment Variables Reference

| Variable | Default | Required | Description |
| :--- | :--- | :---: | :--- |
| `LLM_PROVIDER` | `nvidia` | Yes | Active LLM backend (`nvidia` or `deterministic_fallback`). |
| `NVIDIA_API_KEY` | *(empty)* | Optional* | API key for NVIDIA Llama 3.3 70B (*deterministic fallback used if unset). |
| `NVIDIA_MODEL` | `meta/llama-3.3-70b-instruct` | No | Target NVIDIA model identifier. |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` | No | NVIDIA OpenAI-compatible API base URL. |
| `FASTAPI_HOST` | `0.0.0.0` | No | Bind host IP address. |
| `FASTAPI_PORT` | `8000` | No | Listening TCP port. |
| `DATA_PATH` | `data/processed/merchant_transactions.csv` | No | Path to merchant transaction dataset. |
| `DATA_SEED` | `42` | No | Random seed for deterministic data generation. |
| `LLM_REQUEST_TIMEOUT` | `25.0` | No | Timeout in seconds for LLM invocations. |

---

## 6. Container Health & Readiness Probes

### Liveness Probe (`GET /health`)
- **Endpoint**: `http://localhost:8000/health`
- **Purpose**: Fast HTTP 200 liveness check for container engines and load balancers.
- **Dependency**: Zero external dependencies (responds in < 5ms).

### Readiness Probe (`GET /ready`)
- **Endpoint**: `http://localhost:8000/ready`
- **Purpose**: Confirms data store, analytics engine, and LLM provider initialization before routing merchant traffic.

---

## 7. CI/CD Pipeline (`.github/workflows/ci.yml`)

The GitHub Actions workflow automates quality enforcement across every commit:

1. **Environment Setup**: Provisions Python 3.13 runner.
2. **Dependency Resolution**: Installs production (`requirements.txt`) and dev/test (`requirements-dev.txt`) packages.
3. **Syntax Integrity**: Compiles all project modules (`python -m compileall backend evaluation tests`).
4. **Automated Test Suite**: Executes `pytest -v` (75 tests across all 8 phases).
5. **Offline Evaluation Guarantee**: Executes `python evaluation/run_evaluation.py` with an empty `NVIDIA_API_KEY` to prove 100% offline determinism and zero secret leakage.
6. **Latency Benchmark**: Runs `python evaluation/benchmark.py`.
7. **Artifact Archive**: Uploads `evaluation_report.json` as a build artifact.
8. **Docker Build Validation**: Validates Dockerfile compilation and build caching via `docker/build-push-action`.

---

## 8. Security Considerations

- **Secrets Isolation**: Real API keys are never written to `Dockerfile`, `docker-compose.yml`, or `.github/workflows/ci.yml`.
- **Non-Root Execution**: Container processes run under UID `paypilot` rather than `root`.
- **Dockerignore Rules**: Explicitly excludes `.env*`, `.git/`, test caches, and documentation from build context.
- **Safe Fallback**: If `NVIDIA_API_KEY` is omitted or unavailable, the system safely operates in deterministic mode without crashing or exposing stack traces.
