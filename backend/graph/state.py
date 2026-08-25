"""State schema definition for PayPilot LangGraph workflow.

Defines the complete state structure passed across Supervisor, Specialist Agents,
Evidence Aggregator, and Revenue Recovery nodes.
"""

from typing import Any, Dict, List, Optional, TypedDict
from pydantic import BaseModel, Field


class SupervisorDecision(BaseModel):
    """Structured decision output from the Supervisor router."""
    intent: str = Field(
        description="Identified user intent (e.g. revenue, payment, checkout, customer, category, comparison, what_if, general_business_analysis)"
    )
    required_agents: List[str] = Field(
        description="List of specialist agent node names to invoke (payment_agent, checkout_agent, customer_agent, revenue_agent)"
    )
    reasoning: str = Field(
        description="Brief justification for selecting these agents"
    )


class RecoveryAction(BaseModel):
    """Schema for an individual prioritized recovery action."""
    rank: int = Field(description="Priority rank (1 being highest, P1)")
    action: str = Field(description="Action recommendation title")
    problem: str = Field(description="Root cause problem diagnosed")
    affected_area: str = Field(description="Functional area of impact")
    estimated_revenue_impact_inr: float = Field(description="Estimated recoverable revenue opportunity in INR")
    observed_loss_inr: float = Field(description="Observed gross transaction loss in INR")
    confidence: float = Field(description="Statistical confidence score between 0.0 and 1.0")
    effort: str = Field(description="Implementation effort: 'Low', 'Medium', or 'High'")
    urgency: str = Field(description="Operational urgency: 'High', 'Medium', or 'Low'")
    priority_score: float = Field(description="Deterministic composite score between 0.0 and 100.0")
    reasoning: str = Field(description="Analytical rationale")


class PayPilotState(TypedDict, total=False):
    """Complete LangGraph state passed between all nodes."""
    user_query: str
    intent: str
    required_agents: List[str]
    executed_agents: List[str]
    tool_results: Dict[str, Any]
    evidence: Dict[str, Any]
    analysis: Dict[str, Any]
    root_cause_analysis: Dict[str, Any]
    recommendations: List[Dict[str, Any]]
    recovery_actions: List[Dict[str, Any]]
    priority_actions: List[Dict[str, Any]]
    prioritized_actions: List[Dict[str, Any]]
    estimated_recovery: Dict[str, Any]
    executive_summary: Dict[str, Any]
    final_answer: Optional[str]
    errors: List[str]
