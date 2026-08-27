"""Pydantic schemas and request/response models for PayPilot API."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """System health check and provider readiness response."""
    status: str = Field(default="healthy", description="Service health status")
    service: str = Field(default="paypilot", description="Service identifier")
    llm_provider: str = Field(description="Configured / Active LLM provider (e.g. nvidia)")
    model: str = Field(description="Active LLM model name")
    is_live_llm: bool = Field(description="True if live NVIDIA API connection is active")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp of the health check",
    )


class ReadinessResponse(BaseModel):
    """System readiness probe response verifying required subsystem availability."""
    status: str = Field(default="ready", description="Overall readiness status ('ready' or 'unready')")
    service: str = Field(default="paypilot", description="Service identifier")
    checks: Dict[str, bool] = Field(description="Component-level readiness checklist")
    details: Dict[str, Any] = Field(description="Component health details (non-sensitive)")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp of readiness probe",
    )


class AnalyzeRequest(BaseModel):
    """Request payload for merchant diagnostic inquiry."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Merchant business query regarding revenue, payments, checkout, or refunds (max 1000 characters)",
        examples=["Why did my revenue decrease and what should I do?"],
    )


class PrioritizedActionItem(BaseModel):
    """Individual ranked revenue recovery action item."""
    rank: int = Field(description="Priority rank (1 being highest priority, P1)")
    action: str = Field(description="Actionable recommendation title")
    problem: str = Field(description="Diagnosed root-cause friction problem")
    affected_area: str = Field(description="Functional area of impact (e.g. UPI Gateway, Mobile Checkout UX)")
    estimated_revenue_impact_inr: float = Field(description="Estimated recoverable revenue opportunity in INR")
    observed_loss_inr: float = Field(description="Total observed gross transaction loss in INR")
    confidence: float = Field(description="Statistical confidence score between 0.0 and 1.0")
    effort: str = Field(description="Implementation effort: 'Low', 'Medium', or 'High'")
    urgency: str = Field(description="Operational urgency: 'High', 'Medium', or 'Low'")
    priority_score: float = Field(description="Deterministic composite score between 0.0 and 100.0")
    reasoning: str = Field(description="Analytical justification for the recommendation")
    metrics: Optional[Dict[str, Any]] = Field(default=None, description="Factual underlying metrics")


class ExecutionMetadata(BaseModel):
    """Structured observability metadata for request lifecycle tracking."""
    request_id: str = Field(description="Unique UUID tracking this request")
    trace_id: Optional[str] = Field(default=None, description="Distributed trace tracking ID")
    query: str = Field(description="Original merchant inquiry")
    detected_intent: str = Field(description="Intent classified by Supervisor router")
    executed_agents: List[str] = Field(description="Specialist agents dispatched during execution")
    execution_duration_ms: float = Field(description="Total pipeline execution duration in milliseconds")
    llm_provider: str = Field(description="LLM provider utilized (NVIDIA or deterministic fallback)")
    model: str = Field(description="Model identifier utilized")
    node_models: Optional[Dict[str, str]] = Field(default=None, description="Granular model identifiers utilized per agent node")
    is_live_llm: bool = Field(description="Whether live LLM inference was used")
    success: bool = Field(default=True, description="Pipeline execution success flag")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC completion timestamp",
    )


class AnalyzeResponse(BaseModel):
    """Complete structured response for merchant analysis and action plan."""
    query: str = Field(description="Echo of merchant inquiry")
    intent: str = Field(description="Detected business intent")
    agents_participated: List[str] = Field(description="Specialist agent nodes that executed")
    key_facts: Dict[str, Any] = Field(description="High-level factual business health summary metrics")
    revenue_leaks: List[str] = Field(description="Ranked list of top revenue leakages identified")
    prioritized_actions: List[PrioritizedActionItem] = Field(description="Ranked, measurable recovery actions")
    executive_recommendation: str = Field(description="Decisive recommendation on what the merchant must execute first")
    final_answer: str = Field(description="Complete executive briefing report")
    estimated_recovery: Dict[str, Any] = Field(description="Aggregate estimated recoverable opportunity summary")
    llm_provider: str = Field(description="Active LLM provider name")
    model: str = Field(description="Active LLM model name")
    node_models: Optional[Dict[str, str]] = Field(default=None, description="Granular model identifiers utilized per agent node")
    is_live_llm: bool = Field(description="Whether live LLM inference was used")
    execution_metadata: ExecutionMetadata = Field(description="Observability and timing metadata")


class ErrorResponse(BaseModel):
    """Standardized API error response."""
    error: str = Field(description="Error category or code")
    detail: str = Field(description="Safe, non-confidential error description")
    request_id: Optional[str] = Field(default=None, description="Request tracking ID")
    trace_id: Optional[str] = Field(default=None, description="Trace tracking ID")
    status_code: int = Field(description="HTTP status code")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp of error",
    )


class AuditEventSchema(BaseModel):
    """Single structured audit log event."""
    event_id: str
    event_type: str
    timestamp: str
    request_id: str
    endpoint: str
    http_method: str
    client_id: str
    role: str
    status: str
    status_code: int
    duration_ms: Optional[float] = None
    intent: Optional[str] = None
    executed_agents: Optional[List[str]] = None
    llm_provider: Optional[str] = None
    model: Optional[str] = None
    retry_count: Optional[int] = None
    fallback_used: Optional[bool] = None
    error_category: Optional[str] = None
    query_summary: Optional[str] = None


class AuditTrailResponse(BaseModel):
    """Paginated or bounded audit log trail for compliance review."""
    total_events_retained: int = Field(default=0, description="Total audit events currently in bounded retention")
    total_events: Optional[int] = Field(default=None, description="Total events alias")
    limit: int = Field(description="Pagination limit")
    offset: int = Field(default=0, description="Pagination offset")
    events: List[AuditEventSchema] = Field(description="List of audit events in reverse-chronological order")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC response generation timestamp",
    )



class JobCreateRequest(BaseModel):
    """Payload for submitting a new background job."""
    query: str = Field(
        ...,
        description="Merchant business query or analysis task description",
        min_length=1,
        max_length=500,
        examples=["Perform a complete audit of my revenue leakage and payment drops."],
    )
    task_type: str = Field(
        default="async_analysis",
        description="Categorical job type",
        examples=["async_analysis"],
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional non-sensitive metadata for job tracking",
    )


class JobResponse(BaseModel):
    """Structured response detailing background job lifecycle and results."""
    job_id: str = Field(description="Unique background job identifier")
    task_type: str = Field(description="Type of background task")
    client_id: str = Field(description="Owner principal identifier")
    role: str = Field(description="Owner role")
    request_id: Optional[str] = Field(default=None, description="Correlated request identifier")
    trace_id: Optional[str] = Field(default=None, description="Correlated distributed trace identifier")
    status: str = Field(description="Job status: queued, running, completed, failed, cancelled")
    created_at: str = Field(description="UTC creation timestamp")
    started_at: Optional[str] = Field(default=None, description="UTC execution start timestamp")
    completed_at: Optional[str] = Field(default=None, description="UTC completion timestamp")
    duration_ms: Optional[float] = Field(default=None, description="Execution duration in ms")
    query_summary: Optional[str] = Field(default=None, description="Sanitized query summary")
    result: Optional[Dict[str, Any]] = Field(default=None, description="Structured task results")
    error: Optional[Dict[str, Any]] = Field(default=None, description="Categorized error payload")


class JobListResponse(BaseModel):
    """Paginated list of background jobs for authenticated user."""
    total_jobs: int = Field(description="Total jobs matching query scope")
    limit: int = Field(description="Pagination limit")
    offset: int = Field(description="Pagination offset")
    jobs: List[JobResponse] = Field(description="List of background jobs")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC timestamp",
    )


class TraceSpanSchema(BaseModel):
    """Single span representation in a distributed trace."""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    request_id: Optional[str] = None
    operation_name: str
    component: str
    start_time: str
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None
    status: str
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TraceResponseSchema(BaseModel):
    """Complete trace hierarchy details."""
    trace_id: str
    span_count: int
    root_operation: str
    root_component: str
    total_duration_ms: float
    status: str
    spans: List[TraceSpanSchema]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SLOBreachSchema(BaseModel):
    """SLO breach event details."""
    slo_name: str
    observed_value: float
    target_value: float
    unit: str
    status: str
    severity: str
    timestamp: str
    details: Dict[str, Any] = Field(default_factory=dict)


class SLOResponseSchema(BaseModel):
    """SLO operational status and breach report."""
    overall_status: str
    total_slos_evaluated: int
    active_breaches_count: int
    new_alerts_emitted_count: int
    evaluated_slos: List[SLOBreachSchema]
    active_breaches: List[SLOBreachSchema]
    new_alerts_emitted: List[SLOBreachSchema]
    metrics_evaluated: Dict[str, Any]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ConfigDiagnosticsSchema(BaseModel):
    """Non-secret configuration diagnostics schema."""
    status: str = Field(description="Configuration validation status")
    environment: str = Field(description="Active environment profile")
    llm_provider: str = Field(description="Active LLM provider")
    model: str = Field(description="Active LLM model")
    database_backend: str = Field(description="Active transaction data backend")
    job_store: str = Field(description="Active job store backend")
    rate_limit_backend: str = Field(description="Active rate limit backend")
    tracing: str = Field(description="Tracing state ('enabled' or 'disabled')")
    secrets_status: Dict[str, str] = Field(description="Status of secrets (configured / not_configured)")
    snapshot: Dict[str, Any] = Field(description="Redacted non-secret configuration snapshot")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

