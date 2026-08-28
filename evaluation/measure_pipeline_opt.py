import json
import sys
import time
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.tools.analytics import load_transaction_data
from backend.agents.revenue_agent import revenue_agent_node
from backend.agents.payment_agent import payment_agent_node
from backend.agents.checkout_agent import checkout_agent_node
from backend.agents.customer_agent import customer_agent_node
from evaluation.mock_llm import patch_offline_evaluation_llm
from fastapi.testclient import TestClient
from backend.api.main import app

def profile():
    load_transaction_data()
    query = "Why did my revenue decrease and where is my biggest revenue leakage?"

    # Measure specialist agents
    timings = {"revenue_agent": [], "payment_agent": [], "checkout_agent": [], "customer_agent": []}
    for _ in range(20):
        state = {"user_query": query, "intent": "revenue", "required_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"], "errors": []}
        t0 = time.perf_counter()
        state = revenue_agent_node(state)
        timings["revenue_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = payment_agent_node(state)
        timings["payment_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = checkout_agent_node(state)
        timings["checkout_agent"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        state = customer_agent_node(state)
        timings["customer_agent"].append((time.perf_counter() - t0) * 1000.0)

    summary = {k: round(sum(v)/len(v), 2) for k, v in timings.items()}
    total_specialists = round(sum(summary.values()), 2)

    # Measure E2E API
    api_timings = []
    with patch_offline_evaluation_llm():
        client = TestClient(app)
        client.post("/api/v1/analyze", json={"query": query}) # warmup
        for _ in range(20):
            t0 = time.perf_counter()
            res = client.post("/api/v1/analyze", json={"query": query})
            dur = (time.perf_counter() - t0) * 1000.0
            assert res.status_code == 200
            api_timings.append(dur)

    e2e_mean = round(sum(api_timings)/len(api_timings), 2)
    e2e_p95 = round(sorted(api_timings)[int(len(api_timings) * 0.95)], 2)
    e2e_min = round(min(api_timings), 2)
    e2e_max = round(max(api_timings), 2)

    print("=== OPTIMIZATION PROFILE RESULTS ===")
    print("Specialist Node Latencies:")
    for k, v in summary.items():
        print(f"  • {k}: {v} ms")
    print(f"  -> Total Specialist Stage: {total_specialists} ms (Baseline: 292.69 ms)")
    print(f"\nE2E /api/v1/analyze Latency (20 runs):")
    print(f"  • Mean : {e2e_mean} ms")
    print(f"  • P95  : {e2e_p95} ms")
    print(f"  • Min  : {e2e_min} ms")
    print(f"  • Max  : {e2e_max} ms")

if __name__ == "__main__":
    profile()
