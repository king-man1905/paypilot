# PayPilot — Autonomous Enterprise Revenue Recovery Engine

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent_Workflow-blue?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![NVIDIA AI](https://img.shields.io/badge/NVIDIA_Nemotron-3_Super_120B_%26_Nano_30B-76B900?style=flat-square&logo=nvidia&logoColor=white)](https://build.nvidia.com)
[![React 18](https://img.shields.io/badge/React-18.3-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Tailwind CSS](https://img.shields.io/badge/TailwindCSS-3.4-38B2AC?style=flat-square&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![Pytest](https://img.shields.io/badge/Pytest-349_Passed-brightgreen?style=flat-square&logo=pytest&logoColor=white)](https://pytest.org)
[![Render Live](https://img.shields.io/badge/Render-Live_Production-46E3B7?style=flat-square&logo=render&logoColor=white)](https://paypilot-frontend-cptp.onrender.com)

**PayPilot** is a production-grade, multi-agent financial intelligence and revenue recovery system. It diagnoses e-commerce and fintech revenue leakages across payment gateways, checkout funnels, customer cohorts, and product returns, then synthesizes mathematically grounded, prioritized recovery playbooks with deterministic financial attribution.

---

## Live Production Deployments

| Component | Platform | URL | Status |
| :--- | :--- | :--- | :--- |
| **Frontend Application** | Render Static Site | [https://paypilot-frontend-cptp.onrender.com](https://paypilot-frontend-cptp.onrender.com) | `Live` |
| **Backend API Service** | Render Web Service | [https://paypilot-pjye.onrender.com](https://paypilot-pjye.onrender.com) | `Live` |
| **Health Liveness Probe** | Render Web Service | [https://paypilot-pjye.onrender.com/health](https://paypilot-pjye.onrender.com/health) | `200 OK` |
| **System Readiness Probe**| Render Web Service | [https://paypilot-pjye.onrender.com/ready](https://paypilot-pjye.onrender.com/ready) | `200 OK` |

---

## Problem Statement

Modern e-commerce and fintech merchants lose between **3% to 8% of gross revenue** due to silent, fragmented operational leakages:

1. **Payment Gateway Latency & Timeouts**: Issuer bank server timeouts and UPI application latency result in aborted transactions without immediate smart-routing retry.
2. **Device-Specific Checkout Friction**: Mobile checkout conversion frequently lags desktop by 4–8% due to manual data entry, slow payment sheet rendering, and keyboard context switching.
3. **High-Refund Category Anomalies**: High return and refund rates in specific categories (such as Fashion) erode realized margins.
4. **Lack of Actionable Attribution**: Merchants are overwhelmed by raw charts without clear root-cause diagnosis, expected ROI, or prioritized action ranking.

PayPilot solves this by combining **deterministic analytical tools** with an **orchestrated multi-agent LangGraph pipeline** powered by **NVIDIA Nemotron LLMs**.

> **What this is and isn't:** PayPilot detects revenue-at-risk, computes deterministic financial
> attribution, and generates prioritized, INR-quantified recovery *recommendations*. It does
> **not** modify live payment gateway or checkout infrastructure, and "deploying" a
> recommendation enqueues a tracked follow-up task — it is not an automated payment-side
> intervention. All recoverable/recovery figures throughout this system (and this document) are
> **model-estimated projections based on historical transaction patterns, not confirmed or
> actual recovered revenue.** The dataset is synthetic (see Environment Variables below).

---

## Key Capabilities

- **Deterministic Specialist Diagnostics**: Four dedicated analytical nodes compute exact revenue metrics, payment failure rates, device conversion gaps, and refund statistics from transactional data.
- **Strict Separation of Math and Language**: All calculations and rankings are computed deterministically in Python. LLMs synthesize and format natural language reports without hallucinating numbers.
- **Multi-Factor Action Prioritization**: Ranks actionable recommendations into P1–P4 tiers using an objective mathematical scoring formula factoring in Recoverable Impact, Statistical Confidence, Operational Urgency, and Implementation Effort.
- **Shared-Tier Model Routing**: Supervisor, Evidence Aggregator, and Recovery Engine all currently
  route to `nvidia/nemotron-3-super-120b-a12b`. The Supervisor previously used a smaller/faster
  `nemotron-3-nano-30b-a3b` model, which NVIDIA retired (EOL 2026-09-01); it was consolidated
  onto the same already-verified model tier rather than an unverified replacement. Each node still
  routes independently via `SUPERVISOR_MODEL` / `AGGREGATOR_MODEL` / `RECOVERY_MODEL`, so a
  smaller model can be reinstated for the Supervisor at any time via configuration alone.
- **Robust Multi-Layer Guardrails**: Comprehensive sanitization filters out `<think>` tags, system prompt leakage, placeholder text, and truncated reports with automated fallback to deterministic synthesis.
- **Enterprise-Grade Observability**: Distributed tracing (`trace_span`), node-level model telemetry (`node_models`), SLO tracking (P95 latency, error budgets), and audit logging.
- **Traffic Control & Security**: API key authentication, sliding-window rate limiting, tenant daily quotas, and idempotency key locking (`Idempotency-Key`).
- **Interactive Executive Dashboard**: Modern React 18 UI featuring multi-currency conversion (INR, USD, GBP, EUR, JPY), real-time reasoning visualization, background job processing, and one-click recommendation deployment.

---

## System Architecture

```
                                  +-----------------------+
                                  |   Merchant / User     |
                                  |     (Web UI / API)    |
                                  +-----------+-----------+
                                              |
                                              | POST /api/v1/analyze
                                              v
                                  +-----------------------+
                                  |    FastAPI Backend    |
                                  |  Security & Rate Limiter|
                                  +-----------+-----------+
                                              |
                                              v
+----------------------------------------------------------------------------------------------------+
|                                     LangGraph StateGraph Workflow                                  |
|                                                                                                    |
|                                        [ START ]                                                   |
|                                            |                                                       |
|                                            v                                                       |
|                         +-------------------------------------+                                    |
|                         |          Supervisor Node            |                                    |
|                         | (nvidia/nemotron-3-super-120b-a12b)    |                                    |
|                         +------------------+------------------+                                    |
|                                            |                                                       |
|                   +------------------------+------------------------+                              |
|                   |                        |                        |                              |
|                   v                        v                        v                              |
|         +-------------------+    +-------------------+    +-------------------+                    |
|         |   Revenue Agent   |    |   Payment Agent   |    |  Checkout Agent   |                    |
|         |  (Deterministic)  |    |  (Deterministic)  |    |  (Deterministic)  |                    |
|         +---------+---------+    +---------+---------+    +---------+---------+                    |
|                   |                        |                        |                              |
|                   +------------------------+------------------------+                              |
|                                            |                                                       |
|                                            v                                                       |
|                                 +---------------------+                                            |
|                                 |   Customer Agent    |                                            |
|                                 |   (Deterministic)   |                                            |
|                                 +----------+----------+                                            |
|                                            |                                                       |
|                                            v                                                       |
|                         +-------------------------------------+                                    |
|                         |      Evidence Aggregator Node       |                                    |
|                         | (nvidia/nemotron-3-super-120b-a12b) |                                    |
|                         +------------------+------------------+                                    |
|                                            |                                                       |
|                                            v                                                       |
|                         +-------------------------------------+                                    |
|                         |        Recovery Agent Node          |                                    |
|                         | (nvidia/nemotron-3-super-120b-a12b) |                                    |
|                         +------------------+------------------+                                    |
|                                            |                                                       |
|                                            v                                                       |
|                                         [ END ]                                                    |
+----------------------------------------------------------------------------------------------------+
                                              |
                                              v
                                  +-----------------------+
                                  |  AnalyzeResponse JSON |
                                  |  Executive Brief &    |
                                  |  Ranked Actions P1-P4 |
                                  +-----------------------+
```

---

## LangGraph State & Workflow Engine

PayPilot structures its multi-agent orchestration via **LangGraph (`StateGraph`)**.

### State Schema (`PayPilotState`)

The state dictionary is passed across all nodes in the graph:

```python
class PayPilotState(TypedDict, total=False):
    user_query: str                          # Original merchant inquiry
    intent: str                              # Classified intent (e.g. revenue, payment, what_if)
    required_agents: List[str]               # Specialist agent nodes selected by supervisor
    executed_agents: List[str]               # Nodes that completed execution
    tool_results: Dict[str, Any]             # Raw tool execution data
    evidence: Dict[str, Any]                 # Consolidated domain metrics (revenue, payment, etc.)
    analysis: Dict[str, Any]                 # High-level analytical findings
    root_cause_analysis: Dict[str, Any]      # Root-cause breakdown
    recommendations: List[Dict[str, Any]]    # Candidate action items
    prioritized_actions: List[Dict[str, Any]]# Ranked P1–P4 actions with composite scores
    estimated_recovery: Dict[str, Any]       # Total recoverable opportunity summary
    executive_summary: Dict[str, Any]        # Factual summary payload
    final_answer: Optional[str]              # Clean executive briefing
    errors: List[str]                        # Non-fatal error logs for resilient degradation
```

### Graph Nodes and Edges

The workflow is constructed in `backend/graph/workflow.py`:

```python
builder = StateGraph(PayPilotState)

# Register Nodes
builder.add_node("supervisor", supervisor_node)
builder.add_node("revenue_agent", revenue_agent_node)
builder.add_node("payment_agent", payment_agent_node)
builder.add_node("checkout_agent", checkout_agent_node)
builder.add_node("customer_agent", customer_agent_node)
builder.add_node("evidence_aggregator", evidence_aggregator_node)
builder.add_node("recovery_agent", recovery_agent_node)

# Connect Sequential Pipeline Edges
builder.add_edge(START, "supervisor")
builder.add_edge("supervisor", "revenue_agent")
builder.add_edge("revenue_agent", "payment_agent")
builder.add_edge("payment_agent", "checkout_agent")
builder.add_edge("checkout_agent", "customer_agent")
builder.add_edge("customer_agent", "evidence_aggregator")
builder.add_edge("evidence_aggregator", "recovery_agent")
builder.add_edge("recovery_agent", END)
```

Each specialist agent checks `state["required_agents"]`. If the supervisor did not dispatch that specialist, the node acts as a no-op pass-through, minimizing unnecessary computation.

---

## Detailed Agent Breakdown

### 1. Supervisor Agent (`backend/agents/supervisor.py`)
- **Role**: Dispatches specialist agents and classifies merchant intent.
- **Model**: `nvidia/nemotron-3-super-120b-a12b`
- **Reliability Architecture**:
  - *Stage 1*: NVIDIA Structured Output (`SupervisorDecision` Pydantic schema).
  - *Stage 2*: NVIDIA JSON text-prompt extraction fallback.
  - *Stage 3*: Deterministic heuristic router fallback when external API is unreachable.

### 2. Revenue Agent (`backend/agents/revenue_agent.py`)
- **Role**: Evaluates overarching financial health, period-over-period degradation deltas, and what-if simulation scenarios.
- **Methodology**: Deterministic Python analytics (`backend/tools/analytics.py`).
- **Key Metrics**: Realized revenue, baseline success rate, period degradation trends, and projected revenue recovery for a given uplift (e.g. +3.0%).

### 3. Payment Agent (`backend/agents/payment_agent.py`)
- **Role**: Deep-dives into payment method performance, failure taxonomy, and gateway errors.
- **Methodology**: Deterministic Python analytics.
- **Key Metrics**: Payment method failure rates (UPI, Credit/Debit Cards, Netbanking), top error codes (`BANK_SERVER_TIMEOUT`, `UPI_APP_NOT_RESPONDING`), and total lost transaction value.

### 4. Checkout Agent (`backend/agents/checkout_agent.py`)
- **Role**: Analyzes conversion funnels and device drop-offs.
- **Methodology**: Deterministic Python analytics.
- **Key Metrics**: Mobile (Android/iOS) vs. Desktop conversion rates, checkout drop-off gap, and multi-dimensional error clusters.

### 5. Customer Agent (`backend/agents/customer_agent.py`)
- **Role**: Investigates customer cohorts and product category anomalies.
- **Methodology**: Deterministic Python analytics.
- **Key Metrics**: Cohort conversion (New vs. Returning vs. VIP), category gross revenue, category refund rates, and refund financial impact.

### 6. Evidence Aggregator (`backend/agents/aggregator.py`)
- **Role**: Consolidates numerical evidence from all executed specialist nodes.
- **Model**: `nvidia/nemotron-3-super-120b-a12b`
- **Output**: Generates a factual executive summary and root-cause breakdown strictly constrained by specialist metrics.

### 7. Recovery Agent (`backend/agents/recovery_agent.py`)
- **Role**: Generates candidate recovery items, computes deterministic multi-factor priority scores (P1–P4), and formats the final executive briefing.
- **Model**: `nvidia/nemotron-3-super-120b-a12b`
- **Formula**:
  $$\text{Priority Score} = \left(\frac{\text{Estimated Impact}}{\text{Max Impact}} \times 40\right) + (\text{Confidence} \times 25) + (\text{Urgency Weight} \times 20) + (\text{Effort Weight} \times 15)$$

| Component | Maximum Points | Description |
| :--- | :--- | :--- |
| **Normalized Impact** | `40.0 pts` | Relative recoverable revenue opportunity in INR. |
| **Statistical Confidence** | `25.0 pts` | Data reliability score ($0.0 \le C \le 1.0$). |
| **Operational Urgency** | `20.0 pts` | High (`1.0` $\rightarrow$ 20 pts), Medium (`0.6` $\rightarrow$ 12 pts), Low (`0.3` $\rightarrow$ 6 pts). |
| **Implementation Effort** | `15.0 pts` | Low / Quick-Win (`1.0` $\rightarrow$ 15 pts), Medium (`0.667` $\rightarrow$ 10 pts), High (`0.333` $\rightarrow$ 5 pts). |

---

## Deterministic Analytics vs. LLM Responsibilities

| Responsibility | Handled By | Mechanism | Guarantee |
| :--- | :--- | :--- | :--- |
| **Financial Calculations** | Python Analytics Engine | `backend/tools/analytics.py` | 100% deterministic; zero math errors |
| **Metric Aggregations** | Specialist Agent Nodes | Pandas & Polars-optimized vector operations | No hallucinated financial numbers |
| **Action Prioritization** | Recovery Engine | Deterministic scoring formula (0–100) | Fully reproducible ranking (P1–P4) |
| **Intent Classification** | Supervisor Node | `nvidia/nemotron-3-super-120b-a12b` + Rule Fallback | Fast routing with graceful degradation |
| **Executive Synthesis** | Aggregator & Recovery | `nvidia/nemotron-3-super-120b-a12b` | Natural language business framing |

---

## Output Validation & Anti-Hallucination Guardrails

To ensure production-safe outputs suitable for enterprise finance teams, PayPilot implements strict output sanitizers (`_clean_llm_synthesis` in `aggregator.py` and `recovery_agent.py`):

1. **Anti-`<think>` Leakage**: Strips all reasoning tokens, chain-of-thought blocks, and `<think>...</think>` XML tags (including incomplete or unclosed opening tags).
2. **Prompt & Meta-Commentary Shield**: Detects and rejects internal prompt restatements and forbidden meta-phrases (e.g. `"let's compute"`, `"analyze user input"`, `"the user wants"`, `"system prompt"`).
3. **Report Structural Validation**: Enforces the presence of all five mandatory sections:
   - `BUSINESS DIAGNOSIS`
   - `TOP REVENUE LEAKS`
   - `PRIORITIZED ACTIONS` (must contain all 4 ranks: P1, P2, P3, P4)
   - `EXPECTED UPSIDE`
   - `EXECUTIVE RECOMMENDATION`
4. **Truncation & Dangling Punctuation Detection**: Rejects responses that terminate mid-sentence, end with hanging punctuation (`[,:;•\-\(\[\{]`), or lack terminal sentence punctuation (`.`, `!`, `"`).
5. **Deterministic Synthesis Fallback**: If LLM output fails any validation check, the system immediately substitutes high-fidelity deterministic template synthesis without throwing HTTP 500 errors.

---

## Observability, Tracing & Telemetry

PayPilot includes built-in observability modules:

- **Distributed Tracing (`backend/observability/tracing.py`)**: Instruments all node executions and LLM invocations using `@trace_span`. Provides parent-child trace hierarchy and timing metadata.
- **Node-Level Model Telemetry (`node_models`)**: Every API response reports the exact LLM utilized on each node:
  ```json
  "node_models": {
    "supervisor": "nvidia/nemotron-3-super-120b-a12b",
    "aggregator": "nvidia/nemotron-3-super-120b-a12b",
    "recovery": "nvidia/nemotron-3-super-120b-a12b"
  }
  ```
- **SLO Engine (`backend/observability/slo.py`)**: Tracks performance against operational SLOs (P95 analyze latency $\le$ 1500ms in fallback mode, error rate $\le$ 1.0%, async job success rate $\ge$ 99.0%).
- **Immutable Audit Logging (`backend/observability/audit.py`)**: Records security, analysis, and action deployment events with client IP, timestamp, and payload hash.

---

## API Reference & Schemas

### 1. `GET /health`
Liveness check verifying provider readiness and live LLM connectivity.

**Response (HTTP 200)**:
```json
{
  "status": "healthy",
  "service": "paypilot",
  "llm_provider": "nvidia",
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "is_live_llm": true,
  "timestamp": "2026-08-29T15:41:37.198632+00:00"
}
```

### 2. `GET /ready`
Readiness probe verifying subsystem availability.

**Response (HTTP 200)**:
```json
{
  "status": "ready",
  "service": "paypilot",
  "checks": {
    "dataset_accessible": true,
    "analytics_engine_ready": true,
    "llm_provider_initialized": true,
    "job_runner_ready": true,
    "accepting_traffic": true
  },
  "details": {
    "total_transactions_loaded": 15000,
    "active_llm_provider": "nvidia",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "is_live_llm": true,
    "runner_state": "RUNNING"
  },
  "timestamp": "2026-08-29T15:41:37.500000+00:00"
}
```

### 3. `POST /api/v1/analyze`
Executes synchronous multi-agent revenue diagnostic and recovery prioritization.

> The `estimated_recovery` object below carries two distinct, separately-labeled figures —
> `estimated_recovery_from_prioritized_actions_inr` (sum of the P1–P4 ranked actions) and
> `identified_recoverable_opportunity_inr` (a conservative technical-loss-only estimate) — plus a
> `note` field stating both are projections, not actual recovered revenue. The example response
> below predates this change and shows the legacy field names for illustration.

**Request**:
```json
{
  "query": "Why did my revenue decrease and what should I do?"
}
```

**Response (HTTP 200)**:
```json
{
  "query": "Why did my revenue decrease and what should I do?",
  "intent": "revenue",
  "agents_participated": [
    "revenue_agent",
    "payment_agent",
    "checkout_agent",
    "customer_agent",
    "recovery_agent"
  ],
  "key_facts": {
    "total_revenue_inr": 50092576.66,
    "recoverable_opportunity_inr": 3488251.64,
    "payment_success_rate_pct": 81.71,
    "highest_failure_method": {
      "method": "Netbanking",
      "failure_rate_pct": 21.57
    },
    "mobile_conversion_rate_pct": 80.78,
    "desktop_conversion_rate_pct": 85.11,
    "highest_refund_category": {
      "category": "Fashion",
      "refund_rate_pct": 17.99,
      "refunded_orders_count": 628,
      "refunded_amount_inr": 1648780.21
    }
  },
  "revenue_leaks": [
    "Payment Method Friction: Netbanking failure rate is 21.57%.",
    "Technical Drop-off: 'USER_ABORTED' (768 txns, INR 2,952,124.32 lost).",
    "Device Conversion Gap: Mobile checkout conversion lags Desktop by 4.33%.",
    "Refund Anomaly: Fashion product category shows an elevated refund rate of 17.99%."
  ],
  "prioritized_actions": [
    {
      "rank": 1,
      "action": "Streamline Mobile Checkout UX with 1-Click UPI Intent & Autofill",
      "problem": "Mobile checkout conversion (80.78%) lags Desktop (85.11%) by a 4.33% gap (INR 10,358,638.58 mobile loss).",
      "affected_area": "Checkout Frontend / Mobile Web & App UX",
      "estimated_revenue_impact_inr": 2589659.65,
      "observed_loss_inr": 10358638.58,
      "confidence": 0.9,
      "effort": "Medium",
      "urgency": "High",
      "priority_score": 92.5,
      "reasoning": "Mobile friction depresses conversion. 1-click UPI intent and browser autofill recover high-intent shoppers."
    },
    {
      "rank": 2,
      "action": "Execute Multi-Point Payment Reliability Program to Achieve +3.0% Success Uplift",
      "problem": "Overall payment success rate leaves uncaptured transactions capable of generating +INR 1,839,235.50 in net incremental revenue.",
      "affected_area": "End-to-End Payment Infrastructure",
      "estimated_revenue_impact_inr": 1839235.5,
      "observed_loss_inr": 11219660.3,
      "confidence": 0.92,
      "effort": "Medium",
      "urgency": "High",
      "priority_score": 81.41,
      "reasoning": "A 3% absolute success uplift from 81.71% to 84.71% yields +INR 1,839,235.50 in recovered revenue."
    },
    {
      "rank": 3,
      "action": "Deploy Dynamic Gateway Routing & Intelligent Auto-Retry for UPI / Bank Timeouts",
      "problem": "Technical timeouts and gateway drop-offs caused 687 failed attempts (INR 3,104,914.53 observed loss).",
      "affected_area": "Payment Gateway & UPI Routing Stack",
      "estimated_revenue_impact_inr": 1241965.81,
      "observed_loss_inr": 3104914.53,
      "confidence": 0.95,
      "effort": "Low",
      "urgency": "High",
      "priority_score": 77.93,
      "reasoning": "Dynamic routing with instant fallback to secondary gateways recaptures immediate intent."
    },
    {
      "rank": 4,
      "action": "Implement Pre-Purchase Sizing Verification & Return Controls for Fashion",
      "problem": "Fashion product category shows an elevated refund rate of 17.99% (628 refunded orders, INR 1,648,780.21 refunded).",
      "affected_area": "Catalog Management & Return Operations",
      "estimated_revenue_impact_inr": 412195.05,
      "observed_loss_inr": 1648780.21,
      "confidence": 0.85,
      "effort": "Medium",
      "urgency": "Medium",
      "priority_score": 49.62,
      "reasoning": "Pre-purchase fit verification reduces avoidable sizing returns."
    }
  ],
  "executive_recommendation": "Execute Action P1 (Mobile Checkout UX) and P2 (Payment Reliability Program) immediately to recover up to INR 4.42M in annualized revenue.",
  "final_answer": "BUSINESS DIAGNOSIS\n...",
  "estimated_recovery": {
    "total_recoverable_opportunity_inr": 3488251.64,
    "what_if_3pct_uplift_inr": 1839235.5
  },
  "llm_provider": "nvidia",
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "node_models": {
    "supervisor": "nvidia/nemotron-3-super-120b-a12b",
    "aggregator": "nvidia/nemotron-3-super-120b-a12b",
    "recovery": "nvidia/nemotron-3-super-120b-a12b"
  },
  "is_live_llm": true,
  "execution_metadata": {
    "request_id": "9078ce44-bdfd-4a04-94a3-fbaaa2c88d22",
    "trace_id": "tr_7f3eafab8c404c2b",
    "query": "Why did my revenue decrease and what should I do?",
    "detected_intent": "revenue",
    "executed_agents": [
      "revenue_agent",
      "payment_agent",
      "checkout_agent",
      "customer_agent",
      "recovery_agent"
    ],
    "execution_duration_ms": 35860.91,
    "llm_provider": "nvidia",
    "model": "nvidia/nemotron-3-super-120b-a12b",
    "node_models": {
      "supervisor": "nvidia/nemotron-3-super-120b-a12b",
      "aggregator": "nvidia/nemotron-3-super-120b-a12b",
      "recovery": "nvidia/nemotron-3-super-120b-a12b"
    },
    "is_live_llm": true,
    "success": true,
    "timestamp": "2026-08-29T15:45:00.850168+00:00"
  }
}
```

---

## Environment Variables Configuration

Copy `.env.example` to `.env` in the project root:

```ini
# ============================================================================
# LLM Provider Configuration (NVIDIA API)
# ============================================================================
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=your_nvidia_api_key_here
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=nvidia/nemotron-3-super-120b-a12b
SUPERVISOR_MODEL=nvidia/nemotron-3-super-120b-a12b
AGGREGATOR_MODEL=nvidia/nemotron-3-super-120b-a12b
RECOVERY_MODEL=nvidia/nemotron-3-super-120b-a12b
LLM_REQUEST_TIMEOUT=60.0
LLM_MAX_RETRIES=1

# ============================================================================
# Server & Synthetic Data Configuration
# ============================================================================
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000
DATA_PATH=data/processed/merchant_transactions.csv
DATA_SEED=42

# ============================================================================
# Security, Authentication & Rate Limiting
# ============================================================================
REQUIRE_AUTH=false
PAYPILOT_API_KEY=your_paypilot_analyst_api_key_here
PAYPILOT_ADMIN_KEY=your_paypilot_admin_api_key_here
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=60
RATE_LIMIT_WINDOW_SECONDS=60

# ============================================================================
# Background Jobs & Async Processing
# ============================================================================
JOB_MAX_WORKERS=3
JOB_MAX_QUEUE_SIZE=50
JOB_MAX_RETAINED_JOBS=200

# ============================================================================
# Observability & Distributed Tracing
# ============================================================================
TRACING_ENABLED=true
TRACE_MAX_EVENTS=5000
SLO_ANALYZE_P95_MS=1500.0
SLO_ERROR_RATE_PERCENT=1.0
SLO_JOB_SUCCESS_PERCENT=99.0
```

---

## Local Development Setup

### Prerequisites
- Python 3.11+ (Python 3.13 supported)
- Node.js 18+ and npm

### 1. Backend Setup

```bash
# Clone repository
git clone https://github.com/king-man1905/paypilot.git
cd paypilot

# Create and activate Python virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1

# Install backend dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Start FastAPI backend server
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
```
Backend API will be accessible at: `http://localhost:8000` (Swagger UI: `http://localhost:8000/docs`).

### 2. Frontend Setup

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start Vite development server
npm run dev
```
Frontend UI will be accessible at: `http://localhost:5173`.

---

## Testing & Quality Assurance

### Backend Pytest Suite
PayPilot maintains a comprehensive test suite of **349 automated tests** covering security, LangGraph nodes, deterministic tools, reliability, rate limiters, idempotency, SLOs, and graceful shutdown:

```bash
# Run complete pytest test suite
pytest
```
**Test Result**: `349 passed in 57.71s` across 33 test modules.

### Frontend Vitest Suite & Production Build

```bash
cd frontend

# Run frontend unit & component tests
npm test

# Run TypeScript check and production bundle build
npm run build
```
**Test & Build Result**:
- Vitest: 3 test files passed (17 unit & component tests passed, 8 live contract tests skipped in offline mode).
- Vite build: Completed cleanly in ~7.8s producing minified production assets.

---

## Repository Directory Structure

```text
paypilot/
├── backend/
│   ├── agents/
│   │   ├── aggregator.py          # Evidence aggregator & executive synthesis node
│   │   ├── checkout_agent.py      # Checkout & device conversion specialist node
│   │   ├── customer_agent.py      # Customer cohort & refund specialist node
│   │   ├── llm_factory.py         # Unified NVIDIA LLM factory with tracing wrapper
│   │   ├── payment_agent.py       # Payment method & gateway specialist node
│   │   ├── recovery_agent.py      # Multi-factor action prioritization (P1-P4) node
│   │   ├── revenue_agent.py       # Macro revenue & what-if simulation specialist node
│   │   └── supervisor.py          # Intent classification & agent router node
│   ├── api/
│   │   ├── main.py                # FastAPI application entry point & lifecycle hooks
│   │   ├── routes.py              # API routes (/analyze, /jobs, /deploy, /traces)
│   │   └── schemas.py             # Pydantic request/response data contracts
│   ├── graph/
│   │   ├── run.py                 # Pipeline execution runner
│   │   ├── state.py               # PayPilotState TypedDict and Pydantic models
│   │   └── workflow.py            # LangGraph StateGraph pipeline definition
│   ├── jobs/                      # Background asynchronous job processor
│   ├── observability/             # Distributed tracing, SLOs, metrics & audit trail
│   ├── security/                  # RBAC auth, rate limiters, quotas & idempotency
│   ├── storage/                   # Data backend loaders and database connectors
│   ├── tools/
│   │   ├── analytics.py           # Deterministic financial & conversion analytics (incl. what-if simulation)
│   │   └── tool_registry.py       # Agent tool registry and capability declarations
│   └── config.py                  # Centralized configuration with runtime validation
├── data/
│   └── processed/
│       └── merchant_transactions.csv # Benchmark transaction dataset
├── docs/                          # Architecture & design documentation
├── evaluation/                    # Benchmarks, evaluators & performance audit reports
├── frontend/
│   ├── src/
│   │   ├── api/                   # Type-safe API client & mock fallback data
│   │   ├── components/            # UI components (charts, cards, intelligence layout)
│   │   ├── context/               # Multi-currency, auth, and SLO React contexts
│   │   ├── pages/                 # Overview, Analytics, Intelligence, Jobs, Settings
│   │   ├── tests/                 # Vitest flow & API client test suites
│   │   └── types/                 # TypeScript API contracts aligned with backend
│   ├── package.json
│   └── vite.config.ts
├── render.yaml                    # Infrastructure as Code blueprint for Render Cloud
├── requirements.txt               # Production Python dependencies
├── requirements-dev.txt           # Development & testing dependencies
├── pyproject.toml                 # Pytest configuration
└── tests/                         # 33 comprehensive test suites (349 tests)
```

---

## Verified Performance Characteristics

Deterministic tool and agent node latencies are from **local profiling benchmarks** (`evaluation/production_perf_audit_results.json`). LLM latencies are measured against the **live NVIDIA cloud API**.

| Operation | Component | Execution Latency | Measurement Source |
| :--- | :--- | :--- | :--- |
| **Deterministic Specialist Pipeline** | Revenue, Payment, Checkout, Customer nodes | **~292 ms** total wall-clock time | Local benchmark |
| **Single Analytical Query** | `get_payment_success_rate` / `get_refund_rate` | **0.7 ms – 5.0 ms** | Local benchmark |
| **Complex Funnel Breakdown** | `get_conversion_by_device` / `get_business_health` | **15 ms – 32 ms** | Local benchmark |
| **Supervisor Intent Classification** | `nvidia/nemotron-3-super-120b-a12b` | **~4.6 s** | Live NVIDIA API |
| **Aggregator Evidence Synthesis** | `nvidia/nemotron-3-super-120b-a12b` | **~28.0 s – 32.0 s** | Live NVIDIA API |
| **Full Live E2E Pipeline** | Complete end-to-end (all agents + LLM synthesis) | **~35.9 s** | Live production (`POST /api/v1/analyze`) |
| **Cold Deterministic Fallback Run** | Complete end-to-end pipeline (no LLM) | **< 300 ms** (P95) | Local benchmark |

---

## Security Architecture

- **Role-Based Authentication**: Strict API key authentication supporting analyst keys (`X-API-Key`) and administrative keys (`X-Admin-Key`).
- **Sliding-Window Rate Limiting**: In-memory and Redis-ready sliding-window rate limiters preventing API abuse.
- **Tenant Quota Enforcement**: Daily quota guards preventing denial-of-wallet spikes.
- **Idempotency Protection**: Deterministic key reservation (`Idempotency-Key` header) preventing duplicate background executions.
- **Strict Input Sanitization**: Maximum 1000-character query limit with SQL/Script injection prevention.
- **Zero-Secret Exposure**: Strict isolation of environment variables with no raw secrets logged or exposed via diagnostics endpoints.

---

## Known Limitations & Roadmap

- **LLM Inference Latency**: Large parameter models (`nemotron-3-super-120b-a12b`) require ~25–35 seconds per complete reasoning synthesis over cloud endpoints.
- **Serverless Cold Starts**: On free-tier cloud instances, initial cold-start spin-up may take ~45–60 seconds after periods of inactivity.
- **Planned Enhancements**:
  - Server-Sent Events (SSE) streaming for real-time progressive thought streaming in the UI.
  - Redis Cluster distributed persistence backend for multi-region scale.
  - Automated outbound webhook dispatch upon one-click recommendation deployment.

---

## License

This project is licensed under the MIT License.
