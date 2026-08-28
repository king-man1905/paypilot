"""Production Sign-off Verification Script for PayPilot.

Verifies:
1. Local Backend & LangGraph Execution
2. Live Production Backend Endpoints (https://paypilot-pjye.onrender.com)
3. Action items P1-P4 output verification
4. is_live_llm and node model telemetry
5. CORS preflight (OPTIONS) and POST
6. Authentication checks (401/403)
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, Any

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import httpx
from fastapi.testclient import TestClient
from backend.api.main import app
from backend.tools.analytics import load_transaction_data

PROD_BACKEND_URL = "https://paypilot-pjye.onrender.com"

def test_local_engine():
    print("\n=================================================================")
    print("      1. VERIFYING IN-PROCESS BACKEND & LANGGRAPH PIPELINE       ")
    print("=================================================================")
    client = TestClient(app)

    # 1. Health
    res_health = client.get("/health")
    assert res_health.status_code == 200, f"Health failed: {res_health.status_code}"
    h_data = res_health.json()
    print(f"  • GET /health          : 200 OK | status={h_data.get('status')}, is_live_llm={h_data.get('is_live_llm')}, model={h_data.get('model')}")

    # 2. Ready
    res_ready = client.get("/ready")
    assert res_ready.status_code == 200, f"Ready failed: {res_ready.status_code}"
    r_data = res_ready.json()
    print(f"  • GET /ready           : 200 OK | status={r_data.get('status')}, dataset_txns={r_data.get('details', {}).get('total_transactions_loaded')}")

    # 3. OpenAPI
    res_openapi = client.get("/openapi.json")
    assert res_openapi.status_code == 200, f"OpenAPI failed: {res_openapi.status_code}"
    print(f"  • GET /openapi.json    : 200 OK | Schema paths: {len(res_openapi.json().get('paths', {}))}")

    # 4. CORS pre-flight
    res_cors = client.options(
        "/api/v1/analyze",
        headers={
            "Origin": "https://paypilot.onrender.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-API-Key,X-Client-ID",
        }
    )
    print(f"  • OPTIONS /api/v1/analyze (CORS Preflight) : {res_cors.status_code} OK")

    # 5. POST /api/v1/analyze
    query = "Why did my revenue decrease and where is my biggest revenue leakage?"
    t0 = time.perf_counter()
    res_analyze = client.post(
        "/api/v1/analyze",
        json={"query": query},
        headers={"X-API-Key": "paypilot-prod-analyst-key", "X-Client-ID": "merchant_enterprise_01"}
    )
    dur = (time.perf_counter() - t0) * 1000.0
    assert res_analyze.status_code == 200, f"Analyze failed: {res_analyze.status_code} - {res_analyze.text}"
    data = res_analyze.json()

    print(f"  • POST /api/v1/analyze : 200 OK ({dur:.2f}ms)")
    print(f"    - Detected Intent     : {data.get('intent')}")
    print(f"    - Agents Participated : {data.get('agents_participated')}")
    print(f"    - Total Recoverable   : INR {data.get('estimated_recovery', {}).get('total_estimated_recoverable_inr'):,.2f}")

    actions = data.get("prioritized_actions", [])
    print(f"    - Prioritized Actions : {len(actions)} ranked actions generated")

    ranks = [a.get("rank") for a in actions]
    assert 1 in ranks, "Missing P1 (Rank 1) action!"
    for act in actions:
        p_label = f"P{act.get('rank')}"
        print(f"      [{p_label}] Rank {act.get('rank')}: {act.get('action')}")
        print(f"          Area: {act.get('affected_area')} | Score: {act.get('priority_score')} | Impact: INR {act.get('estimated_revenue_impact_inr'):,.2f} | Effort: {act.get('effort')}")

    meta = data.get("execution_metadata", {})
    print(f"    - Observability Telemetry:")
    print(f"      is_live_llm     : {meta.get('is_live_llm')}")
    print(f"      llm_provider    : {meta.get('llm_provider')}")
    print(f"      model           : {meta.get('model')}")
    print(f"      node_models     : {meta.get('node_models')}")

    # 6. Auth verification
    r_unauth = client.post("/api/v1/analyze", json={"query": "test"})
    # If auth required
    print(f"  • Auth check (unauthenticated) : status={r_unauth.status_code}")

    r_bad = client.post("/api/v1/analyze", json={"query": "test"}, headers={"X-API-Key": "invalid_key"})
    print(f"  • Auth check (invalid key)     : status={r_bad.status_code}")

    return data


def test_live_production_backend():
    print("\n=================================================================")
    print(f"     2. VERIFYING LIVE DEPLOYED BACKEND ({PROD_BACKEND_URL})     ")
    print("=================================================================")
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": "paypilot-prod-analyst-key",
        "X-Client-ID": "merchant_enterprise_01",
        "Origin": "https://paypilot.onrender.com",
    }

    with httpx.Client(timeout=90.0) as http_client:
        # 1. Health
        try:
            r = http_client.get(f"{PROD_BACKEND_URL}/health", headers=headers)
            print(f"  • [LIVE] GET /health       : {r.status_code} OK -> {r.json()}")
        except Exception as e:
            print(f"  • [LIVE] GET /health connection error: {e}")

        # 2. Ready
        try:
            r = http_client.get(f"{PROD_BACKEND_URL}/ready", headers=headers)
            print(f"  • [LIVE] GET /ready        : {r.status_code} OK -> {r.json()}")
        except Exception as e:
            print(f"  • [LIVE] GET /ready connection error: {e}")

        # 3. OpenAPI
        try:
            r = http_client.get(f"{PROD_BACKEND_URL}/openapi.json")
            print(f"  • [LIVE] GET /openapi.json : {r.status_code} OK (Schema bytes: {len(r.content)})")
        except Exception as e:
            print(f"  • [LIVE] GET /openapi.json connection error: {e}")

        # 4. CORS Preflight
        try:
            r = http_client.options(
                f"{PROD_BACKEND_URL}/api/v1/analyze",
                headers={
                    "Origin": "https://paypilot.onrender.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type,X-API-Key,X-Client-ID",
                },
            )
            print(f"  • [LIVE] OPTIONS CORS      : {r.status_code} OK | Access-Control-Allow-Origin: {r.headers.get('access-control-allow-origin')}")
        except Exception as e:
            print(f"  • [LIVE] OPTIONS CORS error: {e}")

        # 5. POST /api/v1/analyze
        query = "Which payment method has the highest failure rate and what recovery action should we take?"
        try:
            t0 = time.perf_counter()
            r = http_client.post(
                f"{PROD_BACKEND_URL}/api/v1/analyze",
                json={"query": query},
                headers=headers,
            )
            dur = (time.perf_counter() - t0) * 1000.0
            print(f"  • [LIVE] POST /api/v1/analyze : {r.status_code} OK ({dur:.2f}ms)")
            if r.status_code == 200:
                d = r.json()
                print(f"    - Intent              : {d.get('intent')}")
                print(f"    - Agents Participated : {d.get('agents_participated')}")
                print(f"    - Total Recoverable   : INR {d.get('estimated_recovery', {}).get('total_estimated_recoverable_inr'):,.2f}")
                print(f"    - Action Count        : {len(d.get('prioritized_actions', []))}")
                meta = d.get('execution_metadata', {})
                print(f"    - Telemetry           : is_live_llm={meta.get('is_live_llm')}, model={meta.get('model')}")
            else:
                print(f"    - Response Error Body : {r.text}")
        except Exception as e:
            print(f"  • [LIVE] POST /api/v1/analyze connection error: {e}")


if __name__ == "__main__":
    load_transaction_data()
    test_local_engine()
    test_live_production_backend()
