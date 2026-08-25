# ==============================================================================
# PayPilot Production Dockerfile (Phase 22)
# Multi-stage slim image for production FastAPI & LangGraph multi-agent service
# Python 3.13 compatible, non-root user, container healthcheck configured.
# ==============================================================================

# Stage 1: Build & Dependencies
FROM python:3.13-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2: Final Production Runtime Image
FROM python:3.13-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    FASTAPI_HOST=0.0.0.0 \
    FASTAPI_PORT=8000 \
    PATH="/install/bin:${PATH}"

# Install minimal runtime healthcheck utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create dedicated non-root user and group (UID/GID 10001)
RUN groupadd -g 10001 paypilot && \
    useradd -u 10001 -g paypilot -d /app -s /sbin/nologin -c "PayPilot App User" paypilot

# Copy application source files and datasets
COPY --chown=paypilot:paypilot backend/ ./backend/
COPY --chown=paypilot:paypilot data/ ./data/
COPY --chown=paypilot:paypilot evaluation/ ./evaluation/

# Ensure directory permissions
RUN mkdir -p /app/data/backups /app/data/processed && \
    chown -R paypilot:paypilot /app

# Switch to non-root user
USER paypilot:paypilot

# Expose production ASGI port
EXPOSE 8000

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production ASGI command with graceful signal handling
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
