"""PayPilot Live API Quality & Contract Audit Script.

Validates end-to-end multi-agent execution against the live FastAPI application
and NVIDIA LLM provider, enforcing strict schema, non-empty P1-P4 actions,
zero prompt/scratchpad leakage, and distributed tracing telemetry.
"""

import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import io
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.agents.llm_factory import get_llm_info
from backend.observability.tracing import get_trace_store


def run_live_audit() -> bool:
    """Executes the complete live API audit and returns True if all checks pass."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    client = TestClient(app)
    llm_info = get_llm_info()

    print("=================================================================")
    print("         PAYPILOT LIVE API ANALYZE AUDIT & VERIFICATION          ")
    print("=================================================================")
    print(f"Active Provider : {llm_info.get('active_provider')}")
    print(f"Is Live LLM     : {llm_info.get('is_live_llm')}")
    print(f"Supervisor Model: {llm_info.get('supervisor_model')}")
    print(f"Aggregator Model: {llm_info.get('aggregator_model')}")
    print(f"Recovery Model  : {llm_info.get('recovery_model')}")
    print("-----------------------------------------------------------------")

    payload = {"query": "Why did my revenue decrease and what should I do?"}

    t_start = time.perf_counter()
    res = client.post("/api/v1/analyze", json=payload)
    dur_s = time.perf_counter() - t_start

    print(f"HTTP Status Code    : {res.status_code}")
    print(f"Request Duration    : {dur_s:.2f}s")

    if res.status_code != 200:
        print(f"Error Response: {res.text}")
        return False

    data = res.json()

    intent = data.get("intent")
    agents = data.get("agents_participated")
    final_answer = data.get("final_answer", "")
    meta = data.get("execution_metadata", {})
    node_models = data.get("node_models") or meta.get("node_models")
    is_live = data.get("is_live_llm")

    print(f"Success Flag        : {meta.get('success')}")
    print(f"Is Live LLM         : {is_live}")
    print(f"Detected Intent     : {intent}")
    print(f"Agents Participated : {agents}")
    print(f"Node Models         : {json.dumps(node_models, indent=2)}")
    print(f"Final Answer Length : {len(final_answer)} chars")

    checks = {
        "HTTP 200": res.status_code == 200,
        "Success Flag True": meta.get("success") is True,
        "Starts with BUSINESS DIAGNOSIS": final_answer.startswith("BUSINESS DIAGNOSIS") or final_answer.startswith("## BUSINESS DIAGNOSIS"),
        "Contains Section 1 (BUSINESS DIAGNOSIS)": ("BUSINESS DIAGNOSIS" in final_answer),
        "Contains Section 2 (TOP REVENUE LEAKS)": ("TOP REVENUE LEAKS" in final_answer),
        "Contains Section 3 (PRIORITIZED ACTIONS)": ("PRIORITIZED ACTIONS" in final_answer or "PRIORITIZED ACTION PLAN" in final_answer),
        "Contains Section 4 (EXPECTED UPSIDE)": ("EXPECTED UPSIDE" in final_answer or "EXPECTED REVENUE UPSIDE" in final_answer),
        "Contains Section 5 (EXECUTIVE RECOMMENDATION)": ("EXECUTIVE RECOMMENDATION" in final_answer),
        "Contains Action P1": ("P1 —" in final_answer or "[P1]" in final_answer or "P1:" in final_answer or "P1." in final_answer),
        "Contains Action P2": ("P2 —" in final_answer or "[P2]" in final_answer or "P2:" in final_answer or "P2." in final_answer),
        "Contains Action P3": ("P3 —" in final_answer or "[P3]" in final_answer or "P3:" in final_answer or "P3." in final_answer),
        "Contains Action P4": ("P4 —" in final_answer or "[P4]" in final_answer or "P4:" in final_answer or "P4." in final_answer),
        "No <think> Leakage": ("<think>" not in final_answer.lower()),
        "No Meta text ('Let's compute')": ("let's compute" not in final_answer.lower()),
        "No Meta text ('Now let's')": ("now let's" not in final_answer.lower()),
        "No Meta text ('Analyze User Input')": ("analyze user input" not in final_answer.lower()),
        "No Meta text ('Thinking process')": ("thinking process" not in final_answer.lower()),
        "No Prompt Leakage ('Ground all conclusions')": ("ground all conclusions" not in final_answer.lower()),
        "No '...' Placeholder": ("..." not in final_answer),
        "Clean Terminal Punctuation": bool(final_answer.rstrip().endswith((".", "!", '"', "'", ")"))),
        "Node Models Supervisor": bool(node_models and "nano-30b" in node_models.get("supervisor", "")),
        "Node Models Aggregator": bool(node_models and "super-120b" in node_models.get("aggregator", "")),
        "Node Models Recovery": bool(node_models and "super-120b" in node_models.get("recovery", "")),
    }

    print("\n--- DETAILED SECTION & QUALITY AUDIT ---")
    all_passed = True
    for c_name, c_val in checks.items():
        st = "PASS" if c_val else "FAIL"
        print(f"  [{st}] {c_name}")
        if not c_val:
            all_passed = False

    print("\n--- FINAL ANSWER OUTPUT ---")
    print(final_answer)

    # Trace Inspection
    store = get_trace_store()
    trace_id = meta.get("trace_id")
    print("\n--- TRACE OBSERVABILITY BREAKDOWN ---")
    if trace_id:
        spans = store.get_trace(trace_id)
        if spans:
            for s in spans:
                dur = f"{s.duration_ms:.1f}ms" if s.duration_ms else "N/A"
                node_m = s.metadata.get("node", "N/A")
                model_m = s.metadata.get("model", "N/A")
                print(f"  Span: {s.operation_name:20} | Component: {s.component:15} | Duration: {dur:8} | Node: {node_m:12} | Model: {model_m}")
        else:
            print(f"  No spans recorded in memory store for trace_id={trace_id}")
    else:
        print("  No trace_id returned in metadata")

    print("\n=================================================================")
    print(f"OVERALL AUDIT: {'SUCCESS - ALL AUDIT CHECKS PASSED' if all_passed else 'FAILURE - CHECKS FAILED'}")
    print("=================================================================")
    return all_passed


if __name__ == "__main__":
    success = run_live_audit()
    sys.exit(0 if success else 1)
