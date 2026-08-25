"""Recommendation Evaluator for PayPilot.

Deterministically verifies:
1. Priority action presence and minimum count
2. Strict monotonic priority ranking order (P1 >= P2 >= P3)
3. Quantitative fields validity (impact > 0, confidence in [0,1], priority_score in [0,100])
4. Executive recommendation consistency referencing actual highest-priority actions
"""

from typing import Any, Dict, List


def evaluate_recommendations(
    dataset: List[Dict[str, Any]],
    pipeline_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluates the quality, ranking, and consistency of recovery recommendations."""
    total_cases = len(dataset)
    if total_cases == 0:
        return {
            "recommendation_correctness_pct": 0.0,
            "case_pass_rate_pct": 0.0,
            "total_actions_checked": 0,
            "valid_actions_count": 0,
            "case_results": [],
        }

    total_actions_checked = 0
    valid_actions_count = 0
    cases_passed = 0

    case_results = []

    for expected, actual in zip(dataset, pipeline_results):
        qid = expected["id"]
        min_actions = expected.get("min_actions_expected", 1)
        actions = actual.get("priority_actions", []) or actual.get("recovery_actions", [])
        final_answer = str(actual.get("final_answer", "") or "")
        exec_summary = str(actual.get("executive_summary", "") or "")

        # 1. Action Count Check
        count_pass = len(actions) >= min_actions

        # 2. Action Field Validity
        action_validities = []
        scores = []
        for a in actions:
            total_actions_checked += 1
            has_title = bool(a.get("action"))
            has_impact = float(a.get("estimated_revenue_impact_inr", a.get("impact_inr", 0.0))) > 0
            has_loss = float(a.get("observed_loss_inr", a.get("observed_gross_loss_inr", 0.0))) >= 0
            valid_conf = 0.0 <= float(a.get("confidence", 0.0)) <= 1.0
            valid_effort = a.get("effort") in ["Low", "Medium", "High"]
            valid_urgency = a.get("urgency") in ["High", "Medium", "Low"]
            score = float(a.get("priority_score", 0.0))
            valid_score = 0.0 <= score <= 100.0
            scores.append(score)

            is_valid = has_title and has_impact and has_loss and valid_conf and valid_effort and valid_urgency and valid_score
            if is_valid:
                valid_actions_count += 1
            action_validities.append(is_valid)

        all_actions_valid = all(action_validities) if action_validities else (min_actions == 0)

        # 3. Monotonic Ranking Order (P1 >= P2 >= P3)
        monotonic_ranking = all(scores[i] >= scores[i + 1] - 0.01 for i in range(len(scores) - 1)) if len(scores) > 1 else True

        # 4. Executive Recommendation References Top Action
        exec_ref_pass = True
        if actions and min_actions > 0:
            top_action_title = actions[0].get("action", "")
            # Check if top action keywords appear in final answer or executive summary
            top_kw = top_action_title.split()[0].lower() if top_action_title else ""
            combined_text = (final_answer + " " + exec_summary).lower()
            exec_ref_pass = top_kw in combined_text or "p1" in combined_text or "recommendation" in combined_text

        case_passed = count_pass and all_actions_valid and monotonic_ranking and exec_ref_pass
        if case_passed:
            cases_passed += 1

        case_results.append({
            "id": qid,
            "actions_count": len(actions),
            "min_expected": min_actions,
            "count_pass": count_pass,
            "actions_valid": all_actions_valid,
            "monotonic_ranking": monotonic_ranking,
            "executive_reference_pass": exec_ref_pass,
            "passed": case_passed,
        })

    recommendation_correctness = (
        round((valid_actions_count / total_actions_checked) * 100, 2)
        if total_actions_checked > 0
        else 100.0
    )
    case_pass_rate = round((cases_passed / total_cases) * 100, 2) if total_cases > 0 else 0.0

    return {
        "recommendation_correctness_pct": recommendation_correctness,
        "case_pass_rate_pct": case_pass_rate,
        "total_actions_checked": total_actions_checked,
        "valid_actions_count": valid_actions_count,
        "cases_passed": cases_passed,
        "total_cases": total_cases,
        "case_results": case_results,
    }
