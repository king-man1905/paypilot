"""Unit tests for Phase 4 Revenue Recovery & Action Prioritization Agent."""

import pytest
from unittest.mock import MagicMock, patch

from backend.agents.recovery_agent import (
    calculate_priority_score,
    generate_candidate_recovery_actions,
    prioritize_actions,
    generate_deterministic_executive_report,
    recovery_agent_node,
)
from backend.agents.llm_factory import get_llm, get_llm_info
from backend.graph.state import PayPilotState
from backend.tools.analytics import (
    get_business_health_summary,
    get_payment_success_rate,
    get_revenue_lost_by_failure,
    get_what_if_success_rate,
    get_conversion_by_device,
    get_category_performance,
)


@pytest.fixture
def sample_evidence():
    """Provides a realistic sample evidence bundle matching Phase 3 output."""
    health = get_business_health_summary()
    loss = get_revenue_lost_by_failure()
    sim = get_what_if_success_rate(target_success_rate=3.0)
    devices = get_conversion_by_device()
    categories = get_category_performance()

    return {
        "payment": {
            "overall_success_rate_pct": 81.71,
            "overall_failure_rate_pct": 18.29,
            "gross_failed_value_inr": 12654909.17,
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
            "payment_methods": {
                # Keys mirror the real shape returned by analytics.get_revenue_by_payment_method()
                # (lost_failed_value, not failed_amount/lost_revenue) — see regression test below.
                "Netbanking": {"lost_failed_value": 2500000.0, "total_attempts": 3000},
                "UPI": {"lost_failed_value": 4000000.0, "total_attempts": 6000},
            },
            "top_overall_failure_reasons": [
                {"failure_reason": "BANK_SERVER_TIMEOUT", "count": 520, "lost_revenue_inr": 1850000.0},
                {"failure_reason": "USER_ABORTED", "count": 768, "lost_revenue_inr": 2952124.32},
            ],
            "top_upi_failure_reasons": [
                {"failure_reason": "BANK_SERVER_TIMEOUT", "count": 310, "lost_revenue_inr": 1100000.0},
            ],
        },
        "checkout": {
            "mobile_conversion_rate_pct": 80.78,
            "desktop_conversion_rate_pct": 85.11,
            "mobile_desktop_conversion_gap_pct": 4.33,
            "lowest_converting_device": {"device": "Mobile_Android", "conversion_rate_pct": 80.66},
            "device_performance": devices,
        },
        "customer": {
            "overall_refund_rate_pct": 8.24,
            "highest_refund_category": {
                "category": "Fashion",
                "refund_rate_pct": 17.99,
                "refunded_orders_count": 628,
                "refunded_amount_inr": 1648780.21,
            },
            "category_performance": categories,
        },
        "revenue": {
            "business_health": health,
            "revenue_loss_breakdown": loss,
            "what_if_simulation": sim,
        },
    }


def test_calculate_priority_score_bounds_and_weights():
    """Verify priority score calculations across extreme and typical values."""
    score_max = calculate_priority_score(
        estimated_impact=1000000.0,
        max_impact=1000000.0,
        confidence=1.0,
        urgency="High",
        effort="Low",
    )
    assert 99.0 <= score_max <= 100.0

    score_min = calculate_priority_score(
        estimated_impact=0.0,
        max_impact=1000000.0,
        confidence=0.0,
        urgency="Low",
        effort="High",
    )
    assert 0.0 <= score_min <= 20.0

    score_low_effort = calculate_priority_score(500000.0, 1000000.0, 0.8, "High", "Low")
    score_high_effort = calculate_priority_score(500000.0, 1000000.0, 0.8, "High", "High")
    assert score_low_effort > score_high_effort


def test_generate_candidate_recovery_actions(sample_evidence):
    """Verify deterministic rule generation produces expected recovery items."""
    actions = generate_candidate_recovery_actions(sample_evidence, {})
    assert len(actions) >= 4

    action_titles = [a["action"] for a in actions]
    assert any("UPI" in t or "Timeout" in t for t in action_titles)
    assert any("Netbanking" in t for t in action_titles)
    assert any("Mobile" in t for t in action_titles)
    assert any("Fashion" in t or "Sizing" in t for t in action_titles)

    for a in actions:
        assert a["estimated_revenue_impact_inr"] > 0
        assert a["observed_loss_inr"] >= a["estimated_revenue_impact_inr"]
        assert 0.0 < a["confidence"] <= 1.0
        assert a["effort"] in ["Low", "Medium", "High"]
        assert a["urgency"] in ["High", "Medium", "Low"]


def test_prioritize_actions_ranking(sample_evidence):
    """Verify that actions are deterministically ranked from highest to lowest score."""
    candidates = generate_candidate_recovery_actions(sample_evidence, {})
    ranked = prioritize_actions(candidates)

    assert len(ranked) == len(candidates)
    for i, item in enumerate(ranked):
        assert item["rank"] == i + 1
        if i > 0:
            assert ranked[i - 1]["priority_score"] >= item["priority_score"]


def test_empty_and_missing_evidence():
    """Verify graceful handling when evidence is empty or partially populated."""
    empty_actions = generate_candidate_recovery_actions({}, {})
    assert empty_actions == []
    empty_ranked = prioritize_actions([])
    assert empty_ranked == []

    partial_evidence = {
        "payment": {
            "overall_success_rate_pct": 80.0,
            "gross_failed_value_inr": 100000.0,
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 25.0},
            "payment_methods": {"Netbanking": {"lost_failed_value": 50000.0}},
            "top_overall_failure_reasons": [],
            "top_upi_failure_reasons": [],
        }
    }
    partial_actions = generate_candidate_recovery_actions(partial_evidence, {})
    assert len(partial_actions) >= 1
    assert "Netbanking" in partial_actions[0]["action"]


def test_deterministic_executive_report(sample_evidence):
    """Verify the structure and numbers of the deterministic executive report."""
    candidates = generate_candidate_recovery_actions(sample_evidence, {})
    ranked = prioritize_actions(candidates)
    report = generate_deterministic_executive_report(
        "Why did revenue drop?", sample_evidence, ranked
    )

    text = report["final_answer"]
    assert "BUSINESS DIAGNOSIS" in text
    assert "TOP REVENUE LEAKS" in text
    assert "PRIORITIZED ACTIONS" in text
    assert "EXPECTED UPSIDE" in text
    assert "EXECUTIVE RECOMMENDATION" in text
    assert "P1 —" in text
    assert "Estimated Recoverable Opportunity" in text


def test_executive_recommendation_references_actual_highest_priority_action():
    """Verify executive recommendation dynamically references the top ranked action without hardcoding."""
    mock_ranked_actions = [
        {
            "rank": 1,
            "action": "Custom Action Alpha - Reduce Gateway Latency",
            "problem": "Latency issues",
            "estimated_revenue_impact_inr": 750000.0,
            "observed_loss_inr": 1500000.0,
            "confidence": 0.95,
            "effort": "Low",
            "urgency": "High",
            "priority_score": 95.0,
            "reasoning": "Quick win",
        },
        {
            "rank": 2,
            "action": "Custom Action Beta - Fix iOS Checkout Drop-off",
            "problem": "iOS issues",
            "estimated_revenue_impact_inr": 450000.0,
            "observed_loss_inr": 900000.0,
            "confidence": 0.85,
            "effort": "Medium",
            "urgency": "Medium",
            "priority_score": 80.0,
            "reasoning": "Secondary fix",
        },
    ]

    report = generate_deterministic_executive_report("Test query", {}, mock_ranked_actions)
    text = report["final_answer"]

    # Must contain Custom Action Alpha as P1
    assert "Custom Action Alpha - Reduce Gateway Latency" in text
    assert "Execute P1 (Custom Action Alpha - Reduce Gateway Latency)" in text
    # Must contain Custom Action Beta as P2
    assert "Follow with P2 (Custom Action Beta - Fix iOS Checkout Drop-off)" in text
    # Must not contain old hardcoded text
    assert "Dynamic Gateway Routing & Timeout Handling" not in text


def test_nvidia_live_provider_metadata_when_configured(monkeypatch):
    """Verify NVIDIA live provider metadata when a valid NVIDIA_API_KEY is configured."""
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-live-mock-key-12345")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    info = get_llm_info()
    assert info["active_provider"] == "nvidia"
    assert info["active_model"] in ["nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3.5-lightning-30b-a3b", "meta/llama-3.3-70b-instruct"]
    assert info["is_live_llm"] is True
    assert info["is_llm_active"] is True
    assert info["nvidia_key_present"] is True


def test_deterministic_fallback_metadata_when_nvidia_unavailable(monkeypatch):
    """Verify deterministic fallback metadata when NVIDIA_API_KEY is empty."""
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    info = get_llm_info()
    assert info["active_provider"] == "deterministic_fallback"
    assert info["active_model"] == "none"
    assert info["is_live_llm"] is False
    assert info["is_llm_active"] is False
    assert info["nvidia_key_present"] is False


def test_recovery_agent_node_execution(sample_evidence):
    """Verify complete recovery agent node execution in LangGraph state."""
    state: PayPilotState = {
        "user_query": "Why did my revenue drop?",
        "intent": "revenue",
        "required_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "executed_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "tool_results": {},
        "evidence": sample_evidence,
        "analysis": {"key_facts": {}},
        "recommendations": [],
        "recovery_actions": [],
        "priority_actions": [],
        "final_answer": None,
        "errors": [],
    }

    out_state = recovery_agent_node(state)

    assert "recovery_agent" in out_state["executed_agents"]
    assert len(out_state["priority_actions"]) > 0
    assert len(out_state["recovery_actions"]) > 0
    assert out_state["estimated_recovery"]["total_estimated_recoverable_inr"] > 0
    assert out_state["final_answer"] is not None
    assert "BUSINESS DIAGNOSIS" in out_state["final_answer"] or "DIAGNOSIS" in out_state["final_answer"]


def test_recovery_agent_with_mocked_nvidia_llm(sample_evidence):
    """Verify that when NVIDIA LLM is available, it synthesizes the executive response."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    expected_report = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n1. Netbanking failure at 21.57%\n\n"
        "PRIORITIZED ACTIONS\n"
        "P1 — Streamline Mobile Checkout UX\n  • Estimated Recoverable Impact: INR 2,589,659.65\n"
        "P2 — Multi-Point Payment Reliability\n  • Estimated Recoverable Impact: INR 1,839,235.50\n"
        "P3 — Dynamic Gateway Routing\n  • Estimated Recoverable Impact: INR 1,241,965.81\n"
        "P4 — Return Controls for Fashion\n  • Estimated Recoverable Impact: INR 412,195.05\n\n"
        "EXPECTED UPSIDE\nEstimated Recoverable Opportunity: INR 3,488,251.64\nWhat-If +3.0% Success Uplift: +INR 1,839,235.50\n\n"
        "EXECUTIVE RECOMMENDATION\nExecute P1 as primary priority to recover revenue."
    )
    mock_response.content = expected_report
    mock_llm.invoke.return_value = mock_response

    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_llm):
        state: PayPilotState = {
            "user_query": "What should I prioritize?",
            "intent": "revenue",
            "required_agents": ["revenue_agent"],
            "executed_agents": ["revenue_agent"],
            "tool_results": {},
            "evidence": sample_evidence,
            "analysis": {},
            "recommendations": [],
            "final_answer": None,
            "errors": [],
        }

        out_state = recovery_agent_node(state)
        assert out_state["final_answer"] == expected_report
        assert len(out_state["priority_actions"]) >= 4


def test_clean_llm_synthesis_cases_a_through_l(sample_evidence):
    """Regression test cases A through L for executive synthesis sanitization and validation."""
    from backend.agents.recovery_agent import (
        _clean_llm_synthesis,
        recovery_agent_node,
        generate_deterministic_executive_report,
    )
    from backend.agents.aggregator import _clean_llm_synthesis as clean_agg, evidence_aggregator_node

    valid_report = (
        "BUSINESS DIAGNOSIS\n------------------\n"
        "Realized Revenue: INR 50,092,576.66\nOverall Payment Success Rate: 81.71%\nObserved Failed Volume: INR 12,654,909.17\n\n"
        "TOP REVENUE LEAKS\n"
        "1. Payment Method Friction: Netbanking at 21.57% failure rate.\n"
        "2. Primary Technical Drop-off: 'USER_ABORTED' loss.\n\n"
        "PRIORITIZED ACTIONS\n"
        "P1 — Streamline Mobile Checkout UX with 1-Click UPI Intent & Autofill\n"
        "  • Estimated Recoverable Impact: INR 2,589,659.65\n"
        "  • Observed Gross Loss: INR 10,358,638.58\n"
        "  • Confidence: 90%\n"
        "  • Effort / Urgency: Medium Effort | High Urgency (Priority Score: 92.5/100)\n"
        "  • Rationale: Mobile checkout friction depresses conversion.\n\n"
        "P2 — Execute Multi-Point Payment Reliability Program\n"
        "  • Estimated Recoverable Impact: INR 1,839,235.50\n"
        "  • Observed Gross Loss: INR 3,488,251.64\n"
        "  • Confidence: 92%\n"
        "  • Effort / Urgency: Medium Effort | High Urgency (Priority Score: 81.41/100)\n"
        "  • Rationale: Target +3% payment success uplift.\n\n"
        "P3 — Deploy Dynamic Gateway Routing & Intelligent Auto-Retry\n"
        "  • Estimated Recoverable Impact: INR 1,241,965.81\n"
        "  • Observed Gross Loss: INR 3,104,914.53\n"
        "  • Confidence: 95%\n"
        "  • Effort / Urgency: Low Effort | High Urgency (Priority Score: 77.93/100)\n"
        "  • Rationale: Instant fallback for timeouts.\n\n"
        "P4 — Implement Pre-Purchase Sizing Verification for Fashion\n"
        "  • Estimated Recoverable Impact: INR 412,195.05\n"
        "  • Observed Gross Loss: INR 1,648,780.20\n"
        "  • Confidence: 85%\n"
        "  • Effort / Urgency: Medium Effort | Medium Urgency (Priority Score: 65.2/100)\n"
        "  • Rationale: Size ambiguity reduction.\n\n"
        "EXPECTED UPSIDE\n"
        "Estimated Recoverable Opportunity : INR 3,488,251.64\n"
        "What-If +3.0% Success Uplift     : +INR 1,839,235.50\n\n"
        "EXECUTIVE RECOMMENDATION\n"
        "Execute P1 as the primary operational priority to recover INR 2,589,659.65. Follow with P2."
    )

    # Case A: Valid complete report -> accepted unchanged
    res_a = _clean_llm_synthesis(valid_report)
    assert res_a.startswith("BUSINESS DIAGNOSIS")
    assert "P1 —" in res_a and "P4 —" in res_a

    # Case B: <think>...</think> + valid report -> thinking removed
    think_with_report = "<think>Analyzing numbers and revenue impact...</think>\n\n" + valid_report
    res_b = _clean_llm_synthesis(think_with_report)
    assert res_b.startswith("BUSINESS DIAGNOSIS")
    assert "<think>" not in res_b

    # Case C: "BUSINESS DIAGNOSIS" appearing inside quoted prompt instructions -> MUST NOT match
    quoted_prompt_rule = (
        "Here's a thinking process:\n"
        "1.  **Analyze Request and Constraints:**\n"
        "    - Role: Chief Financial Intelligence Officer.\n"
        "    - Structure: BUSINESS DIAGNOSIS, TOP REVENUE LEAKS, PRIORITIZED ACTIONS, EXPECTED UPSIDE, EXECUTIVE RECOMMENDATION.\n"
        "    - Output ONLY the briefing.\n"
        "2.  **Map Data to Required Sections:**\n"
        "    - Realized Revenue is 50M."
    )
    assert _clean_llm_synthesis(quoted_prompt_rule) == ""

    # Case D: Thinking process containing prompt rules but no actual report -> MUST return ""
    leak_case_d = (
        "Thinking process:\n"
        "BUSINESS DIAGNOSIS, TOP REVENUE LEAKS, PRIORITIZED ACTIONS, EXPECTED UPSIDE, EXECUTIVE RECOMMENDATION.\n"
        "Use clear terminology: 'Estimated recoverable opportunity'.\n"
        "I think I should present: Total realized revenue: 50,092,576.66 INR."
    )
    assert _clean_llm_synthesis(leak_case_d) == ""

    # Case E: Incomplete report (missing sections) -> MUST return ""
    incomplete_report = "BUSINESS DIAGNOSIS\n------------------\nShort incomplete draft without structure."
    assert _clean_llm_synthesis(incomplete_report) == ""

    # Case F: P1-P3 but missing P4 -> MUST return ""
    missing_p4 = valid_report.replace(
        "P4 — Implement Pre-Purchase Sizing Verification for Fashion\n"
        "  • Estimated Recoverable Impact: INR 412,195.05\n"
        "  • Observed Gross Loss: INR 1,648,780.20\n"
        "  • Confidence: 85%\n"
        "  • Effort / Urgency: Medium Effort | Medium Urgency (Priority Score: 65.2/100)\n"
        "  • Rationale: Size ambiguity reduction.\n\n",
        ""
    )
    assert _clean_llm_synthesis(missing_p4) == ""

    # Case G: Valid report followed by "Let's compute..." -> MUST return ""
    followed_by_compute = valid_report + "\n\nLet's compute numbers with proper formatting."
    assert _clean_llm_synthesis(followed_by_compute) == ""

    # Case H: Valid report followed by calculations/meta text -> MUST return ""
    followed_by_meta = valid_report + "\n\nNow let's check all the revenue leaks:"
    assert _clean_llm_synthesis(followed_by_meta) == ""

    # Case I: Report ending with "..." -> MUST return ""
    ending_dots = valid_report + "..."
    assert _clean_llm_synthesis(ending_dots) == ""

    # Case J: Report ending mid-sentence -> MUST return ""
    mid_sentence = valid_report[:-1]  # remove terminal period
    assert _clean_llm_synthesis(mid_sentence) == ""

    # Case K: Deterministic fallback returns complete P1-P4 report
    mock_rec_llm = MagicMock()
    mock_rec_llm.invoke.return_value = MagicMock(content=followed_by_compute)
    with patch("backend.agents.recovery_agent.get_llm", return_value=mock_rec_llm):
        rec_state: PayPilotState = {
            "user_query": "Why did revenue drop?",
            "intent": "revenue",
            "required_agents": ["revenue_agent"],
            "executed_agents": ["revenue_agent"],
            "tool_results": {},
            "evidence": sample_evidence,
            "analysis": {},
            "recommendations": [],
            "final_answer": None,
            "errors": [],
        }
        out_rec = recovery_agent_node(rec_state)
        ans = out_rec["final_answer"]
        assert ans.startswith("BUSINESS DIAGNOSIS")
        assert "TOP REVENUE LEAKS" in ans
        assert "PRIORITIZED ACTIONS" in ans
        assert "EXPECTED UPSIDE" in ans
        assert "EXECUTIVE RECOMMENDATION" in ans
        assert "P1 —" in ans and "P2 —" in ans and "P3 —" in ans and "P4 —" in ans
        assert "Let's compute" not in ans

    # Case L: Financial terminology validation in deterministic report
    det_out = generate_deterministic_executive_report("Why revenue down?", sample_evidence, out_rec["priority_actions"])
    det_ans = det_out["final_answer"]
    assert "Estimated Recoverable Opportunity" in det_ans
    assert "What-If +3.0% Success Uplift" in det_ans
    assert "Realized Revenue" in det_ans


def test_worst_payment_method_action_uses_real_analytics_field_name():
    """Regression test: the worst-payment-method recovery rule must read the actual field
    name returned by analytics.get_revenue_by_payment_method() ('lost_failed_value'), not the
    stale 'failed_amount'/'lost_revenue' keys that never appear in real evidence. Netbanking is
    above the 15% failure-rate threshold and above zero lost value, so the rule must trigger.
    """
    evidence = {
        "payment": {
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
            "payment_methods": {
                "Netbanking": {
                    "total_attempts": 3237,
                    "successful_transactions": 2538,
                    "failed_transactions": 699,
                    "success_rate_pct": 78.43,
                    "realized_revenue": 9800000.0,
                    "lost_failed_value": 2500000.0,
                },
            },
            "top_overall_failure_reasons": [],
            "top_upi_failure_reasons": [],
        }
    }

    actions = generate_candidate_recovery_actions(evidence, {})
    netbanking_actions = [a for a in actions if "Netbanking" in a["action"]]

    assert len(netbanking_actions) == 1, (
        "Expected exactly one Netbanking-specific recovery action; got "
        f"{[a['action'] for a in actions]}"
    )
    action = netbanking_actions[0]
    assert action["observed_loss_inr"] == 2500000.0
    assert action["estimated_revenue_impact_inr"] == round(2500000.0 * 0.30, 2)


def test_worst_payment_method_action_absent_when_lost_value_missing():
    """Verifies rule 2 (worst-payment-method) specifically stays silent when the worst method
    has no recorded loss, rather than silently using a wrong/zero value from a bad key. Uses the
    rule's distinctive 'metrics.method' marker to isolate it from the unrelated <4-actions
    default-template backfill, which also happens to include a Netbanking-titled template.
    """
    evidence = {
        "payment": {
            "highest_failure_method": {"method": "Netbanking", "failure_rate_pct": 21.57},
            "payment_methods": {"Netbanking": {"total_attempts": 100}},  # no lost_failed_value
            "top_overall_failure_reasons": [],
            "top_upi_failure_reasons": [],
        }
    }
    actions = generate_candidate_recovery_actions(evidence, {})
    rule_2_actions = [
        a for a in actions
        if isinstance(a.get("metrics"), dict) and a["metrics"].get("method") == "Netbanking"
    ]
    assert rule_2_actions == []


def test_estimated_recovery_exposes_two_distinct_labeled_definitions(sample_evidence):
    """Verifies the API-facing estimated_recovery payload clearly distinguishes the two
    recoverable-opportunity concepts (sum of ranked actions vs. technical-loss estimate) and
    carries an explicit disclaimer that neither is actual/confirmed recovered revenue.
    """
    state: PayPilotState = {
        "user_query": "Why did my revenue drop?",
        "intent": "revenue",
        "required_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "executed_agents": ["revenue_agent", "payment_agent", "checkout_agent", "customer_agent"],
        "tool_results": {},
        "evidence": sample_evidence,
        "analysis": {"key_facts": {}},
        "recommendations": [],
        "recovery_actions": [],
        "priority_actions": [],
        "final_answer": None,
        "errors": [],
    }
    out_state = recovery_agent_node(state)
    recovery = out_state["estimated_recovery"]

    # Both distinct concepts must be present, separately labeled, and not silently collapsed.
    assert "estimated_recovery_from_prioritized_actions_inr" in recovery
    assert "identified_recoverable_opportunity_inr" in recovery
    assert recovery["estimated_recovery_from_prioritized_actions_inr"] == recovery["total_estimated_recoverable_inr"]

    # These are two genuinely different numbers by design for this fixture (sum-of-actions vs.
    # 70%-of-technical-loss) — assert they are not accidentally identical/collapsed into one value.
    assert recovery["identified_recoverable_opportunity_inr"] != recovery["estimated_recovery_from_prioritized_actions_inr"]

    # Must never claim these are actual/confirmed recovered money.
    assert "note" in recovery
    note_lower = recovery["note"].lower()
    assert "not confirmed" in note_lower or "not actual" in note_lower or "estimated" in note_lower
    assert "actual" in note_lower or "confirmed" in note_lower
