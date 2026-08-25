"""PayPilot CLI Entry Point.

Allows testing and demonstrating the complete LangGraph agentic recovery workflow directly from the terminal.
"""

import argparse
import sys
from backend.graph.workflow import paypilot_graph
from backend.agents.llm_factory import get_llm_info

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


def run_pipeline(user_query: str) -> dict:
    """Executes the PayPilot LangGraph workflow on a user query."""
    initial_state = {
        "user_query": user_query,
        "intent": "",
        "required_agents": [],
        "executed_agents": [],
        "tool_results": {},
        "evidence": {},
        "analysis": {},
        "root_cause_analysis": {},
        "recommendations": [],
        "recovery_actions": [],
        "priority_actions": [],
        "prioritized_actions": [],
        "estimated_recovery": {},
        "executive_summary": {},
        "final_answer": None,
        "errors": [],
    }

    result = paypilot_graph.invoke(initial_state)
    return result


def main():
    parser = argparse.ArgumentParser(description="Run PayPilot LangGraph Multi-Agent Revenue Recovery Workflow.")
    parser.add_argument(
        "--query",
        type=str,
        default="Why did my revenue decrease and what should I do?",
        help="Merchant question to analyze",
    )
    args = parser.parse_args()

    llm_info = get_llm_info()

    print("\n=======================================================")
    print("       PAYPILOT — REVENUE RECOVERY & ACTION RUNNER    ")
    print("=======================================================")
    print(f"Merchant Query   : '{args.query}'")
    print(f"LLM Provider     : {llm_info['active_provider'].upper()} (Configured: {llm_info['configured_provider']})")
    print(f"Active Model     : {llm_info['active_model']}")
    print(f"Is Live LLM Call : {llm_info['is_live_llm']}")
    if not llm_info['is_live_llm'] and llm_info.get('status_reason'):
        print(f"Provider Status  : {llm_info['status_reason']}")
    print("-------------------------------------------------------\n")

    result = run_pipeline(args.query)

    print("--- 1. SUPERVISOR ROUTING DECISION ---")
    print(f"Detected Intent  : {result.get('intent')}")
    print(f"Required Agents  : {result.get('required_agents')}")
    print(f"Executed Agents  : {result.get('executed_agents')}\n")

    print("--- 2. AGGREGATED NUMERICAL EVIDENCE ---")
    evidence = result.get("evidence", {})
    for section_name, section_data in evidence.items():
        print(f"\n[Specialist: {section_name.upper()} AGENT]")
        if section_name == "payment":
            print(f"  • Overall Success Rate : {section_data.get('overall_success_rate_pct')}%")
            print(f"  • Gross Failed Value   : INR {section_data.get('gross_failed_value_inr'):,.2f}")
            print(f"  • Highest Failure Mtd  : {section_data.get('highest_failure_method')}")
            reasons = section_data.get('top_overall_failure_reasons', [])
            if reasons:
                cnt = reasons[0].get('count', reasons[0].get('failure_count', 0))
                loss = reasons[0].get('lost_revenue_inr', reasons[0].get('lost_amount_inr', 0))
                print(f"  • Top Failure Reason   : {reasons[0].get('failure_reason')} ({cnt} txns, INR {loss:,.2f})")
        elif section_name == "checkout":
            print(f"  • Mobile Conversion    : {section_data.get('mobile_conversion_rate_pct')}%")
            print(f"  • Desktop Conversion   : {section_data.get('desktop_conversion_rate_pct')}%")
            print(f"  • Conversion Gap       : {section_data.get('mobile_desktop_conversion_gap_pct')}%")
            print(f"  • Lowest Conv. Device  : {section_data.get('lowest_converting_device')}")
        elif section_name == "customer":
            print(f"  • Overall Refund Rate  : {section_data.get('overall_refund_rate_pct')}%")
            print(f"  • High Refund Category : {section_data.get('highest_refund_category')}")
        elif section_name == "revenue":
            health = section_data.get("business_health", {})
            sim = section_data.get("what_if_simulation", {})
            print(f"  • Total Revenue        : INR {health.get('total_realized_revenue_inr', 0):,.2f}")
            print(f"  • Recoverable Oppty    : INR {health.get('recoverable_opportunity_inr', 0):,.2f}")
            print(f"  • What-If Simulation  : +INR {sim.get('estimated_additional_revenue_inr', 0):,.2f} (+{sim.get('additional_successful_transactions', 0)} txns)")

    print("\n--- 3. PHASE 4: EXECUTIVE REVENUE RECOVERY REPORT ---")
    if result.get("final_answer"):
        print(result.get("final_answer"))
    else:
        print("No final synthesis generated.")

    if result.get("errors"):
        print(f"\n⚠️ Errors encountered: {result.get('errors')}")

    print("\n=======================================================")
    print("                  EXECUTION COMPLETE                  ")
    print("=======================================================")


if __name__ == "__main__":
    main()
