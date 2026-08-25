"""Numerical Consistency Evaluator for PayPilot.

Compares numerical metrics in pipeline state/evidence/actions against
deterministic analytics engine ground truth within documented tolerance.
"""

import re
from typing import Any, Dict, List, Optional, Tuple


def _is_within_tolerance(
    actual: float,
    expected: float,
    rel_tol: float = 0.01,
    abs_tol: float = 0.5,
) -> bool:
    """Checks if actual value is within relative or absolute tolerance of expected."""
    if actual == expected:
        return True
    abs_diff = abs(actual - expected)
    if abs_diff <= abs_tol:
        return True
    if expected != 0 and (abs_diff / abs(expected)) <= rel_tol:
        return True
    return False


def _extract_metric_from_state(
    metric_name: str,
    state: Dict[str, Any],
) -> Optional[float]:
    """Recursively locates or derives a metric value from PayPilot state dict."""
    evidence = state.get("evidence", {}) or {}
    analysis = state.get("analysis", {}) or {}
    key_facts = analysis.get("key_facts", {}) or {}
    estimated_rec = state.get("estimated_recovery", {}) or {}

    # 1. Revenue Metrics
    if metric_name == "total_realized_revenue_inr":
        rev = evidence.get("revenue", {}).get("business_health", {})
        return rev.get("total_realized_revenue_inr", key_facts.get("total_revenue_inr"))

    if metric_name == "recoverable_opportunity_inr":
        rev = evidence.get("revenue", {}).get("business_health", {})
        return rev.get("recoverable_opportunity_inr", estimated_rec.get("total_recoverable_opportunity_inr"))

    # 2. Payment Metrics
    if metric_name == "payment_success_rate_pct":
        pay = evidence.get("payment", {})
        return pay.get("overall_success_rate_pct", key_facts.get("payment_success_rate_pct"))

    if metric_name == "payment_failure_rate_pct":
        pay = evidence.get("payment", {})
        return pay.get("overall_failure_rate_pct")

    if metric_name == "gross_failed_value_inr":
        pay = evidence.get("payment", {})
        return pay.get("gross_failed_value_inr")

    if metric_name == "netbanking_failure_rate_pct":
        pay = evidence.get("payment", {})
        return pay.get("failure_rate_by_method", {}).get("Netbanking")

    if metric_name == "upi_failure_rate_pct":
        pay = evidence.get("payment", {})
        return pay.get("failure_rate_by_method", {}).get("UPI")

    if metric_name == "credit_card_failure_rate_pct":
        pay = evidence.get("payment", {})
        return pay.get("failure_rate_by_method", {}).get("Credit_Card")

    if metric_name == "bank_server_timeout_count":
        pay = evidence.get("payment", {})
        for r in pay.get("top_overall_failure_reasons", []):
            if r.get("failure_reason") == "BANK_SERVER_TIMEOUT":
                return float(r.get("count", 0))

    if metric_name == "bank_server_timeout_loss_inr":
        pay = evidence.get("payment", {})
        for r in pay.get("top_overall_failure_reasons", []):
            if r.get("failure_reason") == "BANK_SERVER_TIMEOUT":
                return float(r.get("lost_revenue_inr", 0.0))

    # 3. Checkout Metrics
    if metric_name == "mobile_android_conversion_pct":
        chk = evidence.get("checkout", {})
        devs = chk.get("device_performance", {})
        return devs.get("Mobile_Android", {}).get("conversion_rate_pct")

    if metric_name == "mobile_ios_conversion_pct":
        chk = evidence.get("checkout", {})
        devs = chk.get("device_performance", {})
        return devs.get("Mobile_iOS", {}).get("conversion_rate_pct")

    if metric_name == "tablet_conversion_pct":
        chk = evidence.get("checkout", {})
        devs = chk.get("device_performance", {})
        return devs.get("Tablet", {}).get("conversion_rate_pct")

    if metric_name == "desktop_conversion_pct":
        chk = evidence.get("checkout", {})
        devs = chk.get("device_performance", {})
        return devs.get("Desktop", {}).get("conversion_rate_pct", chk.get("desktop_conversion_rate_pct"))

    if metric_name == "mobile_desktop_conversion_gap_pct":
        chk = evidence.get("checkout", {})
        return chk.get("mobile_desktop_conversion_gap_pct")

    if metric_name == "mobile_android_lost_value_inr":
        chk = evidence.get("checkout", {})
        devs = chk.get("device_performance", {})
        return devs.get("Mobile_Android", {}).get("lost_failed_value")

    # 4. Customer & Category Metrics
    if metric_name == "overall_refund_rate_pct":
        cust = evidence.get("customer", {})
        return cust.get("overall_refund_rate_pct")

    if metric_name == "fashion_refund_rate_pct":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        if "Fashion" in cat_perf:
            return cat_perf["Fashion"].get("refund_rate_pct")
        return cust.get("highest_refund_category", {}).get("refund_rate_pct")

    if metric_name == "fashion_refunded_amount_inr":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        if "Fashion" in cat_perf:
            return cat_perf["Fashion"].get("refunded_amount")
        return cust.get("highest_refund_category", {}).get("refunded_amount_inr")

    if metric_name == "fashion_refunded_orders_count":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        if "Fashion" in cat_perf:
            return float(cat_perf["Fashion"].get("refunded_orders_count", 0))
        return float(cust.get("highest_refund_category", {}).get("refunded_orders_count", 0))

    if metric_name == "beauty_refund_rate_pct":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        return cat_perf.get("Beauty_Personal_Care", {}).get("refund_rate_pct")

    if metric_name == "electronics_refund_rate_pct":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        return cat_perf.get("Electronics", {}).get("refund_rate_pct")

    if metric_name == "grocery_refund_rate_pct":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        return cat_perf.get("Grocery", {}).get("refund_rate_pct")

    if metric_name == "electronics_net_revenue_inr":
        cust = evidence.get("customer", {})
        cat_perf = cust.get("category_performance", {})
        return cat_perf.get("Electronics", {}).get("net_revenue")

    # 5. What-If Simulation Metrics
    if metric_name == "target_uplift_pct":
        sim = evidence.get("revenue", {}).get("what_if_simulation", {})
        return sim.get("rate_uplift_pct")

    if metric_name == "estimated_additional_revenue_inr":
        sim = evidence.get("revenue", {}).get("what_if_simulation", {})
        return sim.get("estimated_additional_revenue_inr", estimated_rec.get("what_if_additional_revenue_inr"))

    if metric_name == "additional_successful_transactions":
        sim = evidence.get("revenue", {}).get("what_if_simulation", {})
        return float(sim.get("additional_successful_transactions", estimated_rec.get("additional_successful_transactions", 0)))

    return None


def evaluate_numerical_consistency(
    dataset: List[Dict[str, Any]],
    pipeline_results: List[Dict[str, Any]],
    rel_tol: float = 0.01,
    abs_tol: float = 0.5,
) -> Dict[str, Any]:
    """Evaluates whether numbers produced match deterministic analytics ground truth."""
    total_metrics_evaluated = 0
    consistent_metrics = 0
    cases_passed = 0

    case_results = []

    for expected, actual in zip(dataset, pipeline_results):
        qid = expected["id"]
        exp_metrics = expected.get("expected_metrics", {})

        metric_checks = {}
        case_all_matched = True

        for m_name, m_expected in exp_metrics.items():
            if isinstance(m_expected, (int, float)):
                total_metrics_evaluated += 1
                actual_val = _extract_metric_from_state(m_name, actual)
                if actual_val is not None and _is_within_tolerance(actual_val, float(m_expected), rel_tol, abs_tol):
                    consistent_metrics += 1
                    metric_checks[m_name] = {
                        "status": "PASS",
                        "expected": m_expected,
                        "actual": actual_val,
                    }
                else:
                    case_all_matched = False
                    metric_checks[m_name] = {
                        "status": "FAIL",
                        "expected": m_expected,
                        "actual": actual_val,
                    }
            elif isinstance(m_expected, str):
                # String comparison (e.g. device name, category name, failure reason)
                total_metrics_evaluated += 1
                final_ans = str(actual.get("final_answer", "")) + str(actual.get("evidence", ""))
                matched = m_expected.lower() in final_ans.lower()
                if matched:
                    consistent_metrics += 1
                    metric_checks[m_name] = {"status": "PASS", "expected": m_expected, "found_in_text": True}
                else:
                    case_all_matched = False
                    metric_checks[m_name] = {"status": "FAIL", "expected": m_expected, "found_in_text": False}

        if case_all_matched and exp_metrics:
            cases_passed += 1
        elif not exp_metrics:
            cases_passed += 1

        case_results.append({
            "id": qid,
            "metric_checks": metric_checks,
            "passed": case_all_matched,
        })

    numerical_consistency = (
        round((consistent_metrics / total_metrics_evaluated) * 100, 2)
        if total_metrics_evaluated > 0
        else 100.0
    )
    case_pass_rate = (
        round((cases_passed / len(dataset)) * 100, 2)
        if dataset
        else 100.0
    )

    return {
        "numerical_consistency_pct": numerical_consistency,
        "case_pass_rate_pct": case_pass_rate,
        "total_metrics_checked": total_metrics_evaluated,
        "consistent_metrics": consistent_metrics,
        "cases_passed": cases_passed,
        "total_cases": len(dataset),
        "case_results": case_results,
    }
