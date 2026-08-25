"""Evidence Evaluator for PayPilot.

Deterministically verifies:
1. Presence of required evidence sections in pipeline state
2. Field-level validity and non-null status of diagnostic metrics
3. Aggregated evidence coverage percentage
"""

from typing import Any, Dict, List


def evaluate_evidence(
    dataset: List[Dict[str, Any]],
    pipeline_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluates evidence completeness and coverage across benchmark queries."""
    total_cases = len(dataset)
    if total_cases == 0:
        return {
            "evidence_coverage_pct": 0.0,
            "field_completeness_pct": 0.0,
            "case_results": [],
        }

    total_required_sections = 0
    total_present_sections = 0
    cases_passed = 0

    case_results = []

    for expected, actual in zip(dataset, pipeline_results):
        qid = expected["id"]
        req_evidence = expected.get("required_evidence", [])
        actual_evidence = actual.get("evidence", {}) or {}

        missing_sections = []
        present_sections = []

        for section in req_evidence:
            total_required_sections += 1
            if section in actual_evidence and actual_evidence[section]:
                total_present_sections += 1
                present_sections.append(section)
            else:
                missing_sections.append(section)

        # Field-level verification
        field_checks = {}
        if "payment" in req_evidence and "payment" in actual_evidence:
            p = actual_evidence["payment"]
            field_checks["payment_success_rate_present"] = "overall_success_rate_pct" in p
            field_checks["gross_failed_value_present"] = "gross_failed_value_inr" in p

        if "checkout" in req_evidence and "checkout" in actual_evidence:
            c = actual_evidence["checkout"]
            field_checks["mobile_conversion_present"] = "mobile_conversion_rate_pct" in c
            field_checks["desktop_conversion_present"] = "desktop_conversion_rate_pct" in c

        if "customer" in req_evidence and "customer" in actual_evidence:
            cu = actual_evidence["customer"]
            field_checks["refund_rate_present"] = "overall_refund_rate_pct" in cu

        if "revenue" in req_evidence and "revenue" in actual_evidence:
            r = actual_evidence["revenue"]
            field_checks["business_health_present"] = "business_health" in r

        case_passed = (len(missing_sections) == 0) and all(field_checks.values()) if field_checks else (len(missing_sections) == 0)
        if case_passed:
            cases_passed += 1

        case_results.append({
            "id": qid,
            "required_evidence": req_evidence,
            "present_evidence": present_sections,
            "missing_evidence": missing_sections,
            "field_checks": field_checks,
            "passed": case_passed,
        })

    coverage_pct = (
        round((total_present_sections / total_required_sections) * 100, 2)
        if total_required_sections > 0
        else 100.0
    )
    case_pass_rate = round((cases_passed / total_cases) * 100, 2) if total_cases > 0 else 0.0

    return {
        "evidence_coverage_pct": coverage_pct,
        "case_pass_rate_pct": case_pass_rate,
        "total_required_sections": total_required_sections,
        "total_present_sections": total_present_sections,
        "cases_passed": cases_passed,
        "total_cases": total_cases,
        "case_results": case_results,
    }
