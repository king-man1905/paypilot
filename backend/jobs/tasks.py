"""Executable Background Tasks for PayPilot Job Processing.

Implements asynchronous workflow execution routines for LangGraph diagnostics
and maintenance jobs.
"""

import logging
import time
from typing import Any, Dict, Optional

from backend.graph.run import run_pipeline
from backend.storage.migrator import seed_database_from_csv
from backend.utils.redaction import summarize_query_safely

logger = logging.getLogger("paypilot.jobs.tasks")


def run_async_analysis_task(query: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Executes the full PayPilot LangGraph multi-agent diagnostic analysis asynchronously."""
    start_time = time.perf_counter()
    cleaned_query = query.strip()
    safe_summary = summarize_query_safely(cleaned_query, max_chars=60)

    logger.info(f"[{request_id or 'job'}] Starting background analysis: '{safe_summary}'")

    # Run LangGraph pipeline
    result = run_pipeline(cleaned_query)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    # Format return payload
    intent = result.get("intent", "general_business_analysis")
    executed_agents = result.get("executed_agents", [])
    raw_actions = result.get("priority_actions", []) or result.get("recovery_actions", [])
    final_answer = result.get("final_answer", "") or ""
    estimated_recovery = result.get("estimated_recovery", {}) or {}

    logger.info(
        f"[{request_id or 'job'}] Background analysis finished in {duration_ms}ms | "
        f"Intent: '{intent}' | Agents: {executed_agents} | Actions: {len(raw_actions)}"
    )

    return {
        "query": cleaned_query,
        "intent": intent,
        "executed_agents": executed_agents,
        "priority_actions": raw_actions,
        "final_answer": final_answer,
        "estimated_recovery": estimated_recovery,
        "duration_ms": duration_ms,
    }


def run_database_migration_task(
    csv_path: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Executes deterministic database migration/seeding in the background."""
    logger.info("Executing background database migration task...")
    return seed_database_from_csv(csv_path=csv_path, overwrite=overwrite)
