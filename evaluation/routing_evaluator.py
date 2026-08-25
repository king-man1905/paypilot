"""Routing Evaluator for PayPilot.

Evaluates:
1. Intent Classification Accuracy
2. Agent Recall (TP / (TP + FN))
3. Agent Precision (TP / (TP + FP))
4. Unnecessary Agent Rate (FP / Total Executed)
5. Holistic Routing Correctness
"""

from typing import Any, Dict, List


def evaluate_routing(
    dataset: List[Dict[str, Any]],
    pipeline_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluates routing and agent selection accuracy across benchmark queries."""
    total_cases = len(dataset)
    if total_cases == 0:
        return {
            "routing_accuracy_pct": 0.0,
            "agent_recall_pct": 0.0,
            "agent_precision_pct": 0.0,
            "unnecessary_agent_rate_pct": 0.0,
            "holistic_routing_correctness_pct": 0.0,
            "case_results": [],
        }

    correct_intents = 0
    total_tp = 0
    total_fp = 0
    total_fn = 0
    holistic_correct = 0
    holistic_total = 0

    case_results = []

    for expected, actual in zip(dataset, pipeline_results):
        qid = expected["id"]
        exp_intent = expected["expected_intent"]
        act_intent = actual.get("intent", "")

        exp_agents = set(expected.get("expected_agents", []))
        # Exclude recovery_agent from routing calculation if only specialists are expected
        act_agents = set(a for a in actual.get("executed_agents", []) if a != "recovery_agent")

        intent_match = (exp_intent == act_intent)
        if intent_match:
            correct_intents += 1

        tp = len(exp_agents.intersection(act_agents))
        fp = len(act_agents - exp_agents)
        fn = len(exp_agents - act_agents)

        total_tp += tp
        total_fp += fp
        total_fn += fn

        case_recall = round((tp / len(exp_agents)) * 100, 2) if exp_agents else 100.0
        case_precision = round((tp / len(act_agents)) * 100, 2) if act_agents else 100.0

        is_holistic = len(exp_agents) >= 4
        if is_holistic:
            holistic_total += 1
            if exp_agents.issubset(act_agents):
                holistic_correct += 1

        passed = intent_match and (fn == 0)

        case_results.append({
            "id": qid,
            "query": expected["query"],
            "category": expected.get("category", ""),
            "expected_intent": exp_intent,
            "actual_intent": act_intent,
            "intent_match": intent_match,
            "expected_agents": list(exp_agents),
            "actual_agents": list(act_agents),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "case_recall_pct": case_recall,
            "case_precision_pct": case_precision,
            "passed": passed,
        })

    routing_accuracy = round((correct_intents / total_cases) * 100, 2)
    overall_recall = round((total_tp / (total_tp + total_fn)) * 100, 2) if (total_tp + total_fn) > 0 else 0.0
    overall_precision = round((total_tp / (total_tp + total_fp)) * 100, 2) if (total_tp + total_fp) > 0 else 0.0
    unnecessary_rate = round((total_fp / (total_tp + total_fp)) * 100, 2) if (total_tp + total_fp) > 0 else 0.0
    holistic_accuracy = round((holistic_correct / holistic_total) * 100, 2) if holistic_total > 0 else 100.0

    return {
        "routing_accuracy_pct": routing_accuracy,
        "agent_recall_pct": overall_recall,
        "agent_precision_pct": overall_precision,
        "unnecessary_agent_rate_pct": unnecessary_rate,
        "holistic_routing_correctness_pct": holistic_accuracy,
        "total_cases": total_cases,
        "correct_intents": correct_intents,
        "case_results": case_results,
    }
