# Phase 5 — Production API, Evaluation & Observability

PayPilot Phase 5 transforms the verified Phase 1–4 multi-agent revenue recovery engine into a production-grade REST API powered by FastAPI, featuring structured observability and a deterministic evaluation benchmark suite.

---

## 1. System Architecture

```text
Merchant / Client Request
          ↓
  FastAPI Application
          ↓
  Request Validation & Request-ID Middleware
          ↓
  LangGraph Multi-Agent Pipeline
    ├── Supervisor Router Node
    ├── Revenue Specialist Agent Node
    ├── Payment Specialist Agent Node
    ├── Checkout Specialist Agent Node
    └── Customer Specialist Agent Node
          ↓
  Evidence Aggregator Node
          ↓
  Revenue Recovery & Action Prioritization Node (Multi-factor Ranking)
          ↓
  NVIDIA LLM Executive Synthesis (with Deterministic Fallback)
          ↓
  Structured API Response & Observability Headers
```

---

## 2. API Endpoints

### 2.1 Health Check: `GET /health`

Returns service health and LLM provider readiness without leaking secrets.

#### Response Example (`200 OK`)
```json
{
  "status": "healthy",
  "service": "paypilot",
  "llm_provider": "nvidia",
  "model": "meta/llama-3.3-70b-instruct",
  "is_live_llm": true,
  "timestamp": "2026-08-22T00:40:00Z"
}
```

---

### 2.2 Revenue Recovery Analysis: `POST /api/v1/analyze`

Executes full diagnostic and revenue recovery action prioritization for a merchant query.

#### Request Body
```json
{
  "query": "Why did my revenue decrease and what should I do?"
}
```

#### Response Example (`200 OK`)
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
    "payment_success_rate_pct": 81.71,
    "gross_failed_volume_inr": 12654909.17
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
      "problem": "Mobile checkout conversion rate lags Desktop by 4.33%",
      "affected_area": "Mobile Checkout UX & Gateway Routing",
      "estimated_revenue_impact_inr": 2589659.65,
      "observed_loss_inr": 10358638.58,
      "confidence": 0.90,
      "effort": "Medium",
      "urgency": "High",
      "priority_score": 92.5,
      "reasoning": "Mobile friction depresses conversion. 1-click UPI intent and browser autofill recover high-intent shoppers."
    },
    {
      "rank": 2,
      "action": "Execute Multi-Point Payment Reliability Program to Achieve +3.0% Success Uplift",
      "problem": "Sub-optimal aggregate payment success rate (81.71%)",
      "affected_area": "End-to-End Payment Infrastructure",
      "estimated_revenue_impact_inr": 1839235.50,
      "observed_loss_inr": 3488251.64,
      "confidence": 0.92,
      "effort": "Medium",
      "urgency": "High",
      "priority_score": 81.41,
      "reasoning": "A targeted +3.0% payment success uplift across all methods deterministically recovers ~450 transactions."
    },
    {
      "rank": 3,
      "action": "Deploy Dynamic Gateway Routing & Intelligent Auto-Retry for UPI / Bank Timeouts",
      "problem": "Transient technical failures in UPI and netbanking gateways",
      "affected_area": "UPI & Netbanking Gateway Routing",
      "estimated_revenue_impact_inr": 1241965.81,
      "observed_loss_inr": 3104914.53,
      "confidence": 0.95,
      "effort": "Low",
      "urgency": "High",
      "priority_score": 77.93,
      "reasoning": "Issuer bank latency and UPI timeouts are transient. Dynamic routing recaptures immediate intent."
    }
  ],
  "executive_recommendation": "Execute P1 (Streamline Mobile Checkout UX with 1-Click UPI Intent & Autofill) as primary priority to recover an estimated INR 2,589,659.65 (Medium Effort, High Urgency). Follow with P2 (Execute Multi-Point Payment Reliability Program to Achieve +3.0% Success Uplift) to unlock an additional estimated INR 1,839,235.50.",
  "final_answer": "BUSINESS DIAGNOSIS\n------------------\nRealized Revenue: INR 50,092,576.66\n...",
  "estimated_recovery": {
    "total_estimated_recoverable_inr": 6251640.96,
    "total_actions_identified": 5,
    "simulated_uplift_inr": 1839235.50
  },
  "llm_provider": "nvidia",
  "model": "meta/llama-3.3-70b-instruct",
  "is_live_llm": true,
  "execution_metadata": {
    "request_id": "8f3b2311-6671-4770-bc2a-89a1c863f912",
    "query": "Why did my revenue decrease and what should I do?",
    "detected_intent": "revenue",
    "executed_agents": [
      "revenue_agent",
      "payment_agent",
      "checkout_agent",
      "customer_agent",
      "recovery_agent"
    ],
    "execution_duration_ms": 321.4,
    "llm_provider": "nvidia",
    "model": "meta/llama-3.3-70b-instruct",
    "is_live_llm": true,
    "success": true,
    "timestamp": "2026-08-22T00:40:02Z"
  }
}
```

---

## 3. Error Handling & Security

| HTTP Status Code | Scenario | Response Model |
| :--- | :--- | :--- |
| `400 Bad Request` | Empty query, whitespace, or invalid parameters | `ErrorResponse` |
| `422 Unprocessable` | Malformed JSON or schema validation failure | `ErrorResponse` |
| `500 Internal Error`| Unhandled workflow exception (sanitized, no secrets leaked) | `ErrorResponse` |

### Security Guarantees
- **No Secret Exposure**: API keys (`NVIDIA_API_KEY`) and internal credentials are never reflected in response bodies, headers, logs, or error responses.
- **Sanitized Errors**: Internal stack traces are suppressed in production mode; client receives structured `ErrorResponse`.

---

## 4. Observability & Tracing

- **Request ID Tracking**: Every request receives a unique UUID passed via request state and response header `X-Request-ID`.
- **Response Timing**: Total end-to-end execution time in milliseconds is tracked and returned in response header `X-Response-Time-Ms` and `execution_metadata.execution_duration_ms`.
- **Structured Logging**: Request start, routing decision, agent participation, duration, and status are logged with `[request_id]` prefixes.

---

## 5. Evaluation Benchmark Suite

- **Benchmark Dataset**: [`evaluation/queries.json`](file:///e:/paypilot/evaluation/queries.json) contains 13 business questions spanning `revenue`, `payment`, `checkout`, `customer`, `what_if`, and `holistic` audits.
- **Benchmark Runner**: [`evaluation/run_evaluation.py`](file:///e:/paypilot/evaluation/run_evaluation.py) executes queries and verifies:
  1. Routing accuracy (Supervisor intent classification)
  2. Specialist agent coverage
  3. Evidence presence and completeness
  4. Action prioritization scoring and generation
  5. Executive briefing generation and non-emptiness

### Running Evaluation
```bash
python evaluation/run_evaluation.py
```

---

## 6. How to Run the Application & Tests

### Start the FastAPI Production Server
```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
```
Interactive Swagger Documentation: `http://localhost:8000/docs`

### Run All Unit and Integration Tests
```bash
pytest -v
```
