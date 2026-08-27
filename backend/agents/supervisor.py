"""Supervisor / Router Agent for PayPilot.

Determines merchant intent and selects the optimal specialist agents required to
investigate the query using deterministic analytics tools.
Uses NVIDIA LLMs for intelligent routing with a three-stage reliability architecture:
  Stage 1: NVIDIA structured output
  Stage 2: NVIDIA JSON prompt parsing fallback
  Stage 3: Deterministic heuristic routing fallback (when NVIDIA is unavailable)
"""

import json
import logging
import re
from typing import List, Optional
from langchain_core.messages import SystemMessage, HumanMessage

from backend.graph.state import PayPilotState, SupervisorDecision
from backend.agents.llm_factory import get_llm
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


SUPERVISOR_SYSTEM_PROMPT = """You are the Lead Orchestrator for PayPilot, an Agentic Revenue Recovery & Growth System.
Your job is to analyze the merchant's query and decide which specialist diagnostic agents must be dispatched.

Available Specialist Agents:
- "revenue_agent": Investigates overarching revenue trends, macro leakages, high-level business health KPIs, period deltas, and what-if simulation impact.
- "payment_agent": Investigates payment methods (UPI, Cards, Netbanking), failure rates, error reasons (BANK_SERVER_TIMEOUT, UPI_APP_NOT_RESPONDING), and lost payment amounts.
- "checkout_agent": Investigates device conversions (Mobile Android, iOS, Desktop), checkout funnel drop-offs, and device-specific latency friction.
- "customer_agent": Investigates customer cohort behavior (NEW, RETURNING, VIP), repeat purchase friction, and category-level refund spikes (e.g. Fashion).

Routing Guidelines:
1. Holistic/Root-Cause inquiries (e.g. "Why did my revenue decrease?", "Where is my biggest revenue leakage?", "What should I prioritize?") require multi-agent investigation: ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"].
2. Targeted payment inquiries (e.g. "Which payment method is failing?", "Why is UPI failing?") require: ["payment_agent"].
3. Device/Funnel inquiries (e.g. "Why are mobile users converting less?", "How is Desktop vs Android performing?") require: ["checkout_agent"].
4. Customer/Cohort/Refund inquiries (e.g. "Why are refunds high?", "How do returning customers behave?") require: ["customer_agent"].
5. What-If/Simulation inquiries (e.g. "What if success rate improves by 3%?", "How much revenue can we recover?") require: ["revenue_agent", "payment_agent"].

Respond ONLY in valid JSON format matching this schema:
{
  "intent": "<revenue|payment|checkout|customer|category|comparison|what_if|general_business_analysis>",
  "required_agents": ["<agent_name>", ...],
  "reasoning": "<brief explanation>"
}
"""


def _rule_based_routing(query: str) -> SupervisorDecision:
    """Stage 3: Deterministic heuristic routing fallback when NVIDIA LLM is unavailable."""
    q = query.lower()

    # What-if scenario queries
    if any(k in q for k in ["what if", "what-if", "improve by", "uplift", "increase by", "simulate", "if we improve", "if payment success"]):
        return SupervisorDecision(
            intent="what_if",
            required_agents=["revenue_agent", "payment_agent"],
            reasoning="Deterministic rule: Query requests a scenario simulation or revenue recovery projection.",
        )

    # Holistic revenue & recovery action prioritization inquiries
    if any(k in q for k in [
        "why did", "revenue decrease", "revenue drop", "revenue fall", "biggest leakage",
        "what is happening", "what should i do", "what should the merchant", "audit", "prioritize",
        "health", "recoverable opportunity", "total realized", "recovery priorities", "top 3 revenue",
        "revenue recovery", "where am i losing",
    ]):
        return SupervisorDecision(
            intent="revenue",
            required_agents=["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
            reasoning="Deterministic rule: Holistic diagnosis requires cross-functional evidence from all specialist agents.",
        )

    # Customer cohorts, product categories, and refund queries
    if any(k in q for k in ["customer", "cohort", "vip", "returning", "refund", "fashion", "category", "electronics"]):
        return SupervisorDecision(
            intent="customer",
            required_agents=["customer_agent"],
            reasoning="Deterministic rule: Query targets customer cohorts, product categories, or refund patterns.",
        )

    # Specific payment methods and failure inquiries
    if any(k in q for k in [
        "payment method", "upi", "credit card", "card failure", "failure reason",
        "failed transaction", "payment success", "gateway downtime", "failed payment",
        "transaction value", "lost due to failed",
    ]):
        return SupervisorDecision(
            intent="payment",
            required_agents=["payment_agent"],
            reasoning="Deterministic rule: Query specifically targets payment method and failure reason metrics.",
        )

    # Device & checkout funnel conversion queries
    if any(k in q for k in ["mobile", "android", "ios", "desktop", "device", "conversion", "funnel", "drop-off", "drop off", "checkout"]):
        return SupervisorDecision(
            intent="checkout",
            required_agents=["checkout_agent"],
            reasoning="Deterministic rule: Query targets device-level conversion and checkout funnel performance.",
        )

    # Default fallback
    return SupervisorDecision(
        intent="revenue",
        required_agents=["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        reasoning="Deterministic rule: General business health analysis requires complete diagnostic sweep.",
    )


def _parse_llm_json_response(raw_text: str) -> Optional[SupervisorDecision]:
    """Stage 2: Extracts and parses JSON from raw NVIDIA LLM text if structured output fails."""
    try:
        clean_text = raw_text.strip()
        if "```json" in clean_text:
            clean_text = re.sub(r"```json\s*", "", clean_text)
            clean_text = re.sub(r"```\s*$", "", clean_text).strip()
        elif "```" in clean_text:
            clean_text = re.sub(r"```\s*", "", clean_text).strip()

        try:
            data = json.loads(clean_text)
            if "intent" in data and "required_agents" in data:
                return SupervisorDecision(
                    intent=str(data["intent"]),
                    required_agents=list(data["required_agents"]),
                    reasoning=str(data.get("reasoning", "NVIDIA LLM JSON parsed routing")),
                )
        except Exception:
            pass

        for pattern in [r"\{[^{}]*\}", r"\{.*\}"]:
            json_match = re.search(pattern, clean_text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    if "intent" in data and "required_agents" in data:
                        return SupervisorDecision(
                            intent=str(data["intent"]),
                            required_agents=list(data["required_agents"]),
                            reasoning=str(data.get("reasoning", "NVIDIA LLM JSON parsed routing")),
                        )
                except Exception:
                    continue
    except Exception:
        pass
    return None


@trace_span("agent.supervisor", component="supervisor")
def supervisor_node(state: PayPilotState) -> PayPilotState:
    """Supervisor node in the LangGraph workflow.

    Classifies user intent and routes execution to specialist agents using NVIDIA LLM.
    """
    import time
    from backend.observability.metrics import record_llm_call, record_error

    query = state.get("user_query", "").strip()
    if not query:
        state["errors"] = state.get("errors", []) + ["Empty user query received by supervisor."]
        state["intent"] = "unknown"
        state["required_agents"] = []
        return state

    llm = get_llm(node_type="supervisor", temperature=0.0, max_tokens=256)
    decision: Optional[SupervisorDecision] = None



    if llm is not None:
        from backend.utils.resilience import execute_with_retry, nvidia_circuit_breaker
        from backend.observability.metrics import record_retry

        if not nvidia_circuit_breaker.can_execute():
            logger.warning("NVIDIA circuit breaker is OPEN; bypassing LLM routing directly to deterministic heuristic.")
        else:
            t_llm = time.perf_counter()
            try:
                prompt_content = (
                    f"{SUPERVISOR_SYSTEM_PROMPT}\n\n"
                    f"Merchant Query: {query}\n\n"
                    f'Respond ONLY with valid JSON in this exact structure: {{"intent": "<category>", "required_agents": ["<agent_name>", ...]}}'
                )

                def _call_llm():
                    return llm.invoke([
                        SystemMessage(content="You are PayPilot's Supervisor Agent. Return only valid raw JSON without markdown wrapping."),
                        HumanMessage(content=prompt_content),
                    ])

                raw_res = execute_with_retry(_call_llm, max_retries=0, on_retry=lambda att, exc, d: record_retry())
                lat_ms = round((time.perf_counter() - t_llm) * 1000, 2)
                content = getattr(raw_res, "content", str(raw_res))
                decision = _parse_llm_json_response(content)
                if decision:
                    nvidia_circuit_breaker.record_success()
                    record_llm_call(duration_ms=lat_ms, success=True, is_timeout=False, is_fallback=False)
                    logger.info(f"Supervisor NVIDIA LLM routing succeeded: intent='{decision.intent}', agents={decision.required_agents}")
                else:
                    record_llm_call(duration_ms=lat_ms, success=False, is_timeout=False, is_fallback=True)
                    record_error("routing_error")
            except Exception as e:
                lat_ms = round((time.perf_counter() - t_llm) * 1000, 2)
                nvidia_circuit_breaker.record_failure()
                is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
                record_llm_call(duration_ms=lat_ms, success=False, is_timeout=is_timeout, is_fallback=True)
                record_error("timeout" if is_timeout else "provider_error")
                logger.warning(f"NVIDIA LLM routing notice ({e}), falling back to deterministic heuristic.")

    # Stage 3: Deterministic Heuristic Fallback
    if decision is None:
        decision = _rule_based_routing(query)
        logger.info(f"Supervisor Stage 3 (Deterministic Heuristic) executed: {decision.intent}")


    # Update state
    state["intent"] = decision.intent
    state["required_agents"] = decision.required_agents
    state["executed_agents"] = []
    
    if "tool_results" not in state or state["tool_results"] is None:
        state["tool_results"] = {}
    if "evidence" not in state or state["evidence"] is None:
        state["evidence"] = {}
    if "analysis" not in state or state["analysis"] is None:
        state["analysis"] = {}
    if "recommendations" not in state or state["recommendations"] is None:
        state["recommendations"] = []
    if "errors" not in state or state["errors"] is None:
        state["errors"] = []

    logger.info(f"Supervisor routed query '{query}' to intent='{decision.intent}', agents={decision.required_agents}")
    return state

