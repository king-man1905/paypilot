"""Evidence Aggregator & Synthesis Node.

Consolidates deterministic numerical evidence from specialist agents and
generates executive synthesis and actionable recommendations using NVIDIA LLM
(or robust deterministic synthesis when operating in fallback mode).
"""

import json
import logging
from typing import Dict, List, Any
from langchain_core.messages import SystemMessage, HumanMessage

from backend.graph.state import PayPilotState
from backend.agents.llm_factory import get_llm
from backend.observability.tracing import trace_span

logger = logging.getLogger(__name__)


SYNTHESIS_SYSTEM_PROMPT = """You are the Lead Financial & Payment Intelligence Analyst for PayPilot.
Your role is to synthesize deterministic numerical evidence gathered by specialist diagnostic agents into a clear, executive-ready diagnosis and prioritized recommendations for the merchant.

Rules:
1. Ground all conclusions STRICTLY in the provided numerical evidence. Never invent, hallucinate, or contradict the numbers.
2. Structure your response with exact section headers:
   - Executive Summary: Direct answer to the merchant query with key metrics (Total Realized Revenue, Overall Success Rate, Loss Amounts).
   - Root-Cause Breakdown: What is causing the issue (payment methods, error codes, device conversion gaps, or category refunds).
   - Prioritized Action Plan: 2-4 concrete, actionable steps the merchant can take immediately to recover revenue.
3. Keep the tone professional, authoritative, and solutions-oriented.
4. Output ONLY the executive synthesis. Do NOT output any thinking process, reasoning steps, calculations, preamble, conversational filler, or meta-commentary. Begin your response immediately with "Executive Summary:" and terminate immediately after the Prioritized Action Plan.
"""

FORBIDDEN_META_PHRASES_AGG = [
    "let's compute",
    "let's calculate",
    "now let's",
    "analyze user input",
    "analyze request",
    "map data",
    "i think",
    "we need to",
    "the user wants",
    "prompt",
    "system prompt",
    "thinking process",
    "internal reasoning",
    "<think>",
    "</think>",
    "here's how",
]


def _clean_llm_synthesis(text: str) -> str:
    """Strips chain-of-thought, thinking processes, and meta-commentary from LLM aggregator output.

    Validates complete report structure and terminates cleanly without meta-text.
    """
    if not text or not isinstance(text, str):
        return ""
    import re

    # 1. Strip XML think tags (including unclosed)
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()

    # 2. Reject placeholder text or ellipsis
    if "..." in cleaned:
        return ""

    # 3. Match standalone report header line (never match inside bullet lists or inline mentions)
    header_pattern = re.compile(
        r"(?m)^(?:#{1,4}\s*)?(?:EXECUTIVE SUMMARY|BUSINESS DIAGNOSIS|EXECUTIVE BRIEFING|EXECUTIVE DIAGNOSIS)(?:\s*:|\s*[-=]{2,}|\s*$)",
        re.IGNORECASE,
    )
    match = header_pattern.search(cleaned)
    if not match:
        return ""

    cleaned = cleaned[match.start():].strip()

    # 4. Meta-commentary guardrails across the extracted text
    lowered = cleaned.lower()
    for phrase in FORBIDDEN_META_PHRASES_AGG:
        if phrase in lowered:
            return ""

    # 5. Structural validation: must contain key sections
    has_exec = bool(re.search(r"(?m)^(?:#{1,4}\s*)?(?:Executive Summary|Business Diagnosis|Executive Briefing)", cleaned, re.IGNORECASE))
    has_breakdown = bool(re.search(r"(?m)^(?:#{1,4}\s*)?(?:Root-Cause Breakdown|Top Revenue Leaks|Revenue Leaks|Findings)", cleaned, re.IGNORECASE))
    has_actions = bool(re.search(r"(?m)^(?:#{1,4}\s*)?(?:Prioritized Action|Recommendations|Action Plan)", cleaned, re.IGNORECASE))

    if not (has_exec and has_breakdown and has_actions) or len(cleaned) < 150:
        return ""

    # 6. Truncation detection: reject mid-sentence or mid-block cutoffs
    if re.search(r"[,:;•\-\(\[\{]\s*$", cleaned):
        return ""

    # Must end cleanly with terminal punctuation
    if not re.search(r'[\.\!\"\'\)]\s*$', cleaned):
        return ""

    return cleaned


def _generate_deterministic_synthesis(query: str, evidence: Dict[str, Any], executed: List[str]) -> Dict[str, Any]:
    """Generates structured deterministic synthesis and recommendations when LLM is unavailable."""
    summary_points = []
    recs = []

    if "payment" in evidence and isinstance(evidence["payment"], dict):
        pay = evidence["payment"]
        sr = pay.get("overall_success_rate_pct", 0)
        fr = pay.get("overall_failure_rate_pct", 0)
        failed_val = pay.get("gross_failed_value_inr", 0)
        worst_m = pay.get("highest_failure_method")
        top_reasons = pay.get("top_overall_failure_reasons", [])

        summary_points.append(
            f"Payment Success Rate is {sr}% with {fr}% failure rate, resulting in INR {failed_val:,.2f} in failed attempts."
        )
        if worst_m:
            if isinstance(worst_m, dict):
                m_name = worst_m.get("method", "Unknown")
                m_rate = worst_m.get("failure_rate_pct", 0)
                summary_points.append(f"Highest failure method is {m_name} at {m_rate}% failure rate.")
            else:
                m_name = str(worst_m)
                summary_points.append(f"Highest failure method is {m_name}.")
            recs.append(f"Optimize {m_name} routing and implement automated retry logic for transient timeouts.")

        if top_reasons and isinstance(top_reasons, list) and isinstance(top_reasons[0], dict):
            primary_reason = top_reasons[0].get("failure_reason", "Technical Error")
            reason_count = top_reasons[0].get("count", top_reasons[0].get("failure_count", 0))
            reason_loss = top_reasons[0].get("lost_revenue_inr", top_reasons[0].get("lost_amount_inr", 0))
            summary_points.append(
                f"Primary failure reason: '{primary_reason}' ({reason_count} instances, INR {reason_loss:,.2f} lost)."
            )

    if "checkout" in evidence and isinstance(evidence["checkout"], dict):
        chk = evidence["checkout"]
        m_conv = chk.get("mobile_conversion_rate_pct", 0)
        d_conv = chk.get("desktop_conversion_rate_pct", 0)
        gap = chk.get("mobile_desktop_conversion_gap_pct", 0)

        summary_points.append(
            f"Checkout conversion: Mobile is {m_conv}% vs Desktop at {d_conv}% (Mobile-Desktop gap: {gap}%)."
        )
        if gap > 5.0:
            recs.append("Streamline Mobile checkout UX (enable 1-click UPI intent & autofill) to close the device conversion gap.")

    if "customer" in evidence and isinstance(evidence["customer"], dict):
        cust = evidence["customer"]
        ref_rate = cust.get("overall_refund_rate_pct", 0)
        high_ref = cust.get("highest_refund_category")
        summary_points.append(f"Overall refund rate is {ref_rate}%.")
        if high_ref:
            if isinstance(high_ref, dict):
                cat = high_ref.get("category", "Unknown")
                cat_rate = high_ref.get("refund_rate_pct", 0)
                summary_points.append(f"Category with highest refunds: {cat} ({cat_rate}% refund rate).")
            else:
                cat = str(high_ref)
                summary_points.append(f"Category with highest refunds: {cat}.")
            recs.append(f"Audit {cat} product descriptions and return policies to mitigate high return volumes.")

    if "revenue" in evidence and isinstance(evidence["revenue"], dict):
        rev = evidence["revenue"]
        health = rev.get("business_health", {}) if isinstance(rev.get("business_health"), dict) else {}
        sim = rev.get("what_if_simulation", {}) if isinstance(rev.get("what_if_simulation"), dict) else {}
        tot_rev = health.get("total_realized_revenue_inr", 0)
        rec_opp = health.get("recoverable_opportunity_inr", 0)

        summary_points.insert(
            0,
            f"Total realized revenue is INR {tot_rev:,.2f} with an estimated recoverable opportunity of INR {rec_opp:,.2f}."
        )
        if sim:
            uplift_amt = sim.get("estimated_additional_revenue_inr", 0)
            uplift_pct = sim.get("target_success_rate_uplift_pct", 3.0)
            recs.append(
                f"A +{uplift_pct}% success rate improvement is projected to unlock +INR {uplift_amt:,.2f} in net new revenue."
            )


    final_text = (
        f"### PayPilot Diagnostic Report\n\n"
        f"**Query**: {query}\n\n"
        f"**Key Findings**:\n"
        + "\n".join(f"- {p}" for p in summary_points)
        + "\n\n**Actionable Recommendations**:\n"
        + "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))
    )

    return {
        "final_answer": final_text,
        "recommendations": recs,
    }


def _compact_evidence_for_prompt(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Prunes and compacts deep/lengthy time-series in evidence to avoid oversized LLM prompts."""
    if not isinstance(evidence, dict):
        return {}

    compacted: Dict[str, Any] = {}
    for section_key, section_val in evidence.items():
        if not isinstance(section_val, dict):
            compacted[section_key] = section_val
            continue

        sec_copy = dict(section_val)
        if "weekly_trend" in sec_copy and isinstance(sec_copy["weekly_trend"], list):
            sec_copy["weekly_trend"] = sec_copy["weekly_trend"][-4:]  # Last 4 weeks
        if "monthly_trend" in sec_copy and isinstance(sec_copy["monthly_trend"], list):
            sec_copy["monthly_trend"] = sec_copy["monthly_trend"][-6:]  # Last 6 months
        if "top_overall_failure_reasons" in sec_copy and isinstance(sec_copy["top_overall_failure_reasons"], list):
            sec_copy["top_overall_failure_reasons"] = sec_copy["top_overall_failure_reasons"][:5]
        if "top_multidimensional_leaks" in sec_copy and isinstance(sec_copy["top_multidimensional_leaks"], list):
            sec_copy["top_multidimensional_leaks"] = sec_copy["top_multidimensional_leaks"][:5]

        compacted[section_key] = sec_copy

    return compacted


@trace_span("agent.aggregator", component="aggregator")
def evidence_aggregator_node(state: PayPilotState) -> PayPilotState:
    """Consolidates evidence and produces executive synthesis via NVIDIA LLM (or deterministic fallback)."""
    evidence = state.get("evidence", {}) or {}
    executed = state.get("executed_agents", []) or []
    query = state.get("user_query", "").strip()

    # 1. Compile structured high-level evidence summary
    aggregated_summary = {
        "intent": state.get("intent", "general_business_analysis"),
        "agents_participated": executed,
        "evidence_sections": list(evidence.keys()),
        "key_facts": {},
    }

    if "revenue" in evidence and isinstance(evidence["revenue"], dict):
        health = evidence["revenue"].get("business_health", {})
        if isinstance(health, dict):
            aggregated_summary["key_facts"]["total_revenue_inr"] = health.get("total_realized_revenue_inr")
            aggregated_summary["key_facts"]["recoverable_opportunity_inr"] = health.get("recoverable_opportunity_inr")

    if "payment" in evidence and isinstance(evidence["payment"], dict):
        pay = evidence["payment"]
        aggregated_summary["key_facts"]["payment_success_rate_pct"] = pay.get("overall_success_rate_pct")
        aggregated_summary["key_facts"]["highest_failure_method"] = pay.get("highest_failure_method")

    if "checkout" in evidence and isinstance(evidence["checkout"], dict):
        chk = evidence["checkout"]
        aggregated_summary["key_facts"]["mobile_conversion_rate_pct"] = chk.get("mobile_conversion_rate_pct")
        aggregated_summary["key_facts"]["desktop_conversion_rate_pct"] = chk.get("desktop_conversion_rate_pct")

    if "customer" in evidence and isinstance(evidence["customer"], dict):
        cust = evidence["customer"]
        aggregated_summary["key_facts"]["highest_refund_category"] = cust.get("highest_refund_category")

    state["analysis"] = aggregated_summary

    # 2. Generate LLM synthesis if available (with deterministic fallback)
    import time
    from backend.observability.metrics import record_llm_call, record_error, record_retry
    from backend.utils.resilience import execute_with_retry, nvidia_circuit_breaker

    llm = get_llm(node_type="aggregator", temperature=0.2, max_tokens=2048)
    synthesis_done = False

    if llm is not None and evidence:
        if not nvidia_circuit_breaker.can_execute():
            logger.warning("NVIDIA circuit breaker is OPEN; bypassing LLM synthesis directly to deterministic fallback.")
        else:
            t_llm = time.perf_counter()
            try:
                compact_evidence = _compact_evidence_for_prompt(evidence)
                prompt_content = (
                    f"Merchant Query: {query}\n\n"
                    f"Factual Numerical Evidence Gathered:\n{json.dumps(compact_evidence, indent=2, default=str)}\n\n"
                    f"Synthesize this evidence into an Executive Diagnosis and Action Plan for the merchant."
                )

                def _call_synthesis():
                    return llm.invoke([
                        SystemMessage(content=SYNTHESIS_SYSTEM_PROMPT),
                        HumanMessage(content=prompt_content),
                    ])

                res = execute_with_retry(_call_synthesis, max_retries=0, on_retry=lambda att, exc, d: record_retry())
                lat_ms = round((time.perf_counter() - t_llm) * 1000, 2)
                raw_text = getattr(res, "content", str(res)).strip()
                text_answer = _clean_llm_synthesis(raw_text)
                if text_answer:
                    nvidia_circuit_breaker.record_success()
                    state["final_answer"] = text_answer
                    synthesis_done = True
                    record_llm_call(duration_ms=lat_ms, success=True, is_timeout=False, is_fallback=False)
                    logger.info("Generated executive synthesis using NVIDIA LLM.")
                else:
                    record_llm_call(duration_ms=lat_ms, success=False, is_timeout=False, is_fallback=True)
            except Exception as e:
                lat_ms = round((time.perf_counter() - t_llm) * 1000, 2)
                nvidia_circuit_breaker.record_failure()
                is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
                record_llm_call(duration_ms=lat_ms, success=False, is_timeout=is_timeout, is_fallback=True)
                record_error("timeout" if is_timeout else "provider_error")
                logger.warning(f"NVIDIA synthesis notice ({e}), falling back to deterministic synthesis.")

    if not synthesis_done:
        det = _generate_deterministic_synthesis(query, evidence, executed)
        state["final_answer"] = det["final_answer"]
        state["recommendations"] = det["recommendations"]

    logger.info(f"Evidence aggregated successfully from agents: {executed}")
    return state


