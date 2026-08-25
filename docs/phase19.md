# Phase 19 — Distributed Tracing & SLO Engineering Specification

---

## 1. Overview & Architecture Goals

PayPilot Phase 19 establishes a production-grade **Distributed Tracing & Service Level Objective (SLO) Engineering** layer.

### Primary Question
> *"When a merchant submits an inquiry or async job, how do we trace its end-to-end execution across HTTP, Supervisor, Specialist Agents, LLM Inference, and Recovery nodes without performance degradation, secret leakage, or unconstrained memory growth?"*

---

## 2. Distributed Tracing Architecture

```
HTTP Ingress (FastAPI)
 └── [Span: http.request] (trace_id=tr_xxx, span_id=sp_root)
      └── [Span: agent.supervisor] (parent_span_id=sp_root)
           ├── [Span: agent.revenue] (parent_span_id=sp_sup)
           ├── [Span: agent.payment] (parent_span_id=sp_sup)
           ├── [Span: agent.checkout] (parent_span_id=sp_sup)
           ├── [Span: agent.customer] (parent_span_id=sp_sup)
           ├── [Span: agent.aggregator] (parent_span_id=sp_sup)
           │    └── [Span: llm.generate] (parent_span_id=sp_agg, provider=nvidia)
           └── [Span: agent.recovery] (parent_span_id=sp_sup)
                └── [Span: llm.generate] (parent_span_id=sp_rec, provider=nvidia)
```

### Key Components

1. **`TraceContext` Dataclass**:
   - `trace_id`: Correlated tracking identifier across all hops.
   - `request_id`: Preserves `X-Request-ID` correlation.
   - `span_id`: Unique identifier for the individual execution unit.
   - `parent_span_id`: Pointer to the parent span.
2. **Context Variable Propagation (`contextvars.ContextVar`)**:
   - Guarantees async-safe and thread-safe context propagation across coroutines and threads.
3. **`SpanRecord` Dataclass**:
   - Captures `operation_name`, `component`, `start_time`, `end_time`, `duration_ms`, `status` (`OK`, `ERROR`), `error_category`, `error_message`, and sanitized `metadata`.
4. **`trace_span` Context Manager & Decorator**:
   - Automatically computes duration, catches exceptions, sets `status="ERROR"`, sanitizes error messages, and commits spans to the trace store on exit. Guarantees **no orphaned spans**.

---

## 3. Background Job Correlation & Multi-Worker Tracing

1. **Submission**:
   - `POST /api/v1/jobs` captures the incoming request's `trace_id` and attaches it to `JobRecord.trace_id`.
2. **Worker Execution**:
   - Worker thread initializes `TraceContext(trace_id=job.trace_id, request_id=job.request_id)`.
   - Spans executed inside the job worker (e.g. `job.execute`, `agent.supervisor`, `agent.payment`, `llm.generate`) are automatically linked as child spans to that trace ID.

---

## 4. Trace Storage & Retention Lifecycle

- **`BaseTraceStore`**: Abstract storage contract.
- **`InMemoryTraceStore`**:
  - Thread-safe ring buffer protected by `threading.Lock`.
  - Configurable retention bounds:
    - `TRACE_MAX_EVENTS=5000`: Maximum total span events retained.
    - `TRACE_MAX_TRACES=1000`: Maximum unique trace trees retained.
  - Automatic FIFO eviction ensures memory usage remains strictly bounded.

---

## 5. Security & Sensitive Credential Non-Exposure

In strict adherence to PayPilot security policies:
1. **Never Captured**: Raw prompts, bearer tokens, passwords, database URLs with passwords, and `NVIDIA_API_KEY` are never written to span records or metadata.
2. **Automatic Redaction**: All metadata fields and exception messages are processed through `redact_sensitive_text()`.
3. **Admin-Only API**:
   - `GET /admin/traces/{trace_id}` is strictly restricted to principals with `role=admin` via `require_admin`.
   - Non-admin analysts attempting access receive `403 Forbidden`.

---

## 6. Service Level Objectives (SLO) Specification

> [!IMPORTANT]
> **Proposed Production Targets vs Local Offline Results**:
> The following targets represent proposed operational thresholds for production deployment. Local measurements reflect offline test harness executions.

| SLO Metric Name | Proposed Production Target | Locally Measured Value | Local Status |
| :--- | :--- | :--- | :--- |
| **Analyze P50 Latency** | $< 500\text{ ms}$ | **$121.30\text{ ms}$** | **PASS** |
| **Analyze P95 Latency** | $< 1500\text{ ms}$ | **$245.46\text{ ms}$** | **PASS** |
| **Analyze P99 Latency** | $< 2500\text{ ms}$ | **$317.80\text{ ms}$** | **PASS** |
| **API Error Rate** | $< 1.0\%$ | **$0.0\%$** | **PASS** |
| **Job Completion Rate** | $\ge 99.0\%$ | **$100.0\%$** | **PASS** |
| **LLM Fallback Rate** | $\le 5.0\%$ (Production Target) | $33.33\%$ (Offline Mock) | **EXPECTED** (Offline) |

---

## 7. Deterministic SLO Breach Detection & Alert Cooldown

1. **Deterministic Evaluation**:
   - When an operational metric breaches its threshold (e.g. `P95 > 1500 ms` or `Error Rate > 1.0%`), `evaluate_slo_breaches()` creates an `SLOBreachEvent`.
2. **Alert Deduplication Cooldown**:
   - `SLOCooldownManager` enforces `SLO_ALERT_COOLDOWN_SECONDS=60.0`s cooldown per SLO.
   - Prevents duplicate breach alert flooding during sustained load or transient latency spikes.

---

## 8. Hardened Tracing Overhead Benchmark Results

```
===============================================================================================
         PAYPILOT DISTRIBUTED TRACING OVERHEAD BENCHMARK (PHASE 19 FINAL HARDENING)
           [STATISTICALLY RIGOROUS REPEATED & ALTERNATING RUNS — 5 ROUNDS]
===============================================================================================
Sequential Workload (50 requests per mode):
  - Tracing OFF : Mean:  93.99 ms | Median:  55.09 ms | P95: 206.78 ms | Throughput: 10.64 req/s
  - Tracing ON  : Mean:  96.98 ms | Median:  62.30 ms | P95: 201.86 ms | Throughput: 10.31 req/s
  - Overhead    : Mean:  +2.99 ms (+3.18%) | Median:  +7.21 ms (+13.09%) | P95:  -4.92 ms (-2.38%)

Concurrent Workload (125 requests per mode across 5 worker threads):
  - Tracing OFF : Mean: 606.28 ms | Median: 431.05 ms | P95: 1290.52 ms | Throughput:  7.89 req/s
  - Tracing ON  : Mean: 636.74 ms | Median: 431.38 ms | P95: 1382.05 ms | Throughput:  7.46 req/s
  - Overhead    : Mean: +30.46 ms (+5.02%) | Median:  +0.33 ms (+0.08%) | P95: +91.53 ms (+7.09%)
===============================================================================================
```

### Statistical Analysis & Methodology
1. **Alternating Order**: Executed across 5 interleaved rounds (OFF→ON and ON→OFF) after warmup to eliminate thermal, memory, and ordering bias.
2. **Formula Applied**: $\text{Overhead \%} = \frac{\text{ON} - \text{OFF}}{\text{OFF}} \times 100$.
3. **Sequential Overhead**: Tracing introduces $+2.99\text{ ms}$ ($+3.18\%$) mean overhead from context propagation and span recording. Variations within $\pm 2-3\%$ at P95 reflect local measurement jitter.
4. **Concurrent Overhead**: Under 5-worker thread concurrency, synchronized in-memory ring-buffer insertion adds $+30.46\text{ ms}$ ($+5.02\%$) mean overhead. Optimized with $O(1)$ `OrderedDict` evictions.

---

## 9. Production Deployment Considerations

1. **OpenTelemetry Collector Export**:
   - For enterprise clusters, a custom `OTLPTraceStore` can be plugged into `set_trace_store()` to export spans to Jaeger, Tempo, or Google Cloud Trace without changing agent or API code.
2. **Prometheus / Grafana Alerting**:
   - The `/admin/slo` endpoint provides JSON-formatted breach states suitable for custom alerting bridges or automated incident management webhooks.
