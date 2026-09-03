from datetime import datetime, timezone
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.api.schemas import (
    HealthResponse,
    ReadinessResponse,
    AnalyzeRequest,
    AnalyzeResponse,
    PrioritizedActionItem,
    ExecutionMetadata,
    ErrorResponse,
    AuditTrailResponse,
    AuditEventSchema,
    JobCreateRequest,
    JobResponse,
    JobListResponse,
    DeployRecommendationRequest,
    DeployRecommendationResponse,
    TraceResponseSchema,
    TraceSpanSchema,
    SLOResponseSchema,
    SLOBreachSchema,
    ConfigDiagnosticsSchema,
)
from backend.agents.llm_factory import get_llm_info
from backend.config import (
    DATA_PATH,
    MAX_QUERY_LENGTH,
    NVIDIA_MODEL,
    SUPERVISOR_MODEL,
    AGGREGATOR_MODEL,
    RECOVERY_MODEL,
    get_config_diagnostics,
)
from backend.graph.run import run_pipeline
from backend.jobs import (
    JobQueueFullError,
    JobRunnerDrainingError,
    JobRunnerStoppedError,
    get_job_runner,
    run_async_analysis_task,
)
from backend.observability.metrics import (
    record_concurrency_rejection,
    record_idempotency_conflict,
    record_idempotency_replay,
    record_queue_full_rejection,
    record_quota_rejection,
)
from backend.observability.audit import record_audit_event
from backend.observability.slo import evaluate_slo_breaches
from backend.observability.tracing import get_trace_store
from backend.security.auth import AuthenticatedUser, require_admin, require_analyst
from backend.security.idempotency import (
    IdempotencyReservationStatus,
    compute_payload_hash,
    fingerprint_idempotency_key,
    get_idempotency_store,
    validate_idempotency_key,
)
from backend.security.quotas import get_quota_manager


logger = logging.getLogger("paypilot.api")

router = APIRouter()



def _extract_revenue_leaks(evidence: Dict[str, Any]) -> List[str]:
    """Extracts human-readable summary of identified revenue leaks from evidence."""
    leaks = []
    if "payment" in evidence:
        pay = evidence["payment"]
        worst_m = pay.get("highest_failure_method", {})
        reasons = pay.get("top_overall_failure_reasons", [])
        if worst_m and worst_m.get("method"):
            leaks.append(
                f"Payment Method Friction: {worst_m['method']} failure rate is {worst_m.get('failure_rate_pct', 0)}%."
            )
        if reasons:
            r0 = reasons[0]
            cnt = r0.get("count", r0.get("failure_count", 0))
            loss = r0.get("lost_revenue_inr", r0.get("lost_amount_inr", 0.0))
            leaks.append(
                f"Technical Drop-off: '{r0.get('failure_reason', 'Unknown')}' ({cnt} txns, INR {loss:,.2f} lost)."
            )

    if "checkout" in evidence:
        chk = evidence["checkout"]
        gap = chk.get("mobile_desktop_conversion_gap_pct", 0.0)
        if gap > 0:
            leaks.append(f"Device Conversion Gap: Mobile checkout conversion lags Desktop by {gap}%.")

    if "customer" in evidence:
        high_ref = evidence["customer"].get("highest_refund_category", {})
        if high_ref and high_ref.get("category"):
            leaks.append(
                f"Refund Anomaly: {high_ref['category']} product category shows an elevated refund rate of {high_ref.get('refund_rate_pct', 0)}%."
            )

    return leaks if leaks else ["No critical revenue leaks detected."]


def _extract_executive_recommendation(
    actions: List[Dict[str, Any]],
    final_answer: str,
) -> str:
    """Extracts or constructs a concise executive recommendation."""
    if actions:
        p1 = actions[0]
        rec = f"Execute P1 ({p1.get('action', 'Immediate Action')}) as primary priority to recover an estimated INR {p1.get('estimated_revenue_impact_inr', 0.0):,.2f} ({p1.get('effort', 'Medium')} Effort, {p1.get('urgency', 'High')} Urgency)."
        if len(actions) > 1:
            p2 = actions[1]
            rec += f" Follow with P2 ({p2.get('action', 'Secondary Action')}) to unlock an additional estimated INR {p2.get('estimated_revenue_impact_inr', 0.0):,.2f}."
        return rec

    # Fallback to search in final answer
    if "EXECUTIVE RECOMMENDATION" in final_answer:
        parts = final_answer.split("EXECUTIVE RECOMMENDATION")
        if len(parts) > 1:
            return parts[1].strip().lstrip("-").strip()

    return "Maintain continuous monitoring across active payment gateways and checkout funnels."


@router.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Liveness Probe Check",
    description="Returns the operational status and active LLM provider metadata without exposing secrets.",
)
async def health_check() -> HealthResponse:
    """Fast liveness probe check."""
    llm_info = get_llm_info()
    return HealthResponse(
        status="healthy",
        service="paypilot",
        llm_provider=llm_info.get("active_provider", "nvidia"),
        model=llm_info.get("active_model", NVIDIA_MODEL),
        is_live_llm=llm_info.get("is_live_llm", False),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={
        200: {"model": ReadinessResponse, "description": "System is ready to process queries"},
        503: {"model": ErrorResponse, "description": "Subsystems not ready or service is draining"},
    },
    tags=["System"],
    summary="Readiness Probe Check",
    description="Verifies dataset accessibility, analytics engine state, database readiness, and shutdown lifecycle.",
)
async def readiness_check() -> ReadinessResponse:
    """Verifies that all required application components and datasets are ready."""
    from backend.api.main import is_shutting_down

    # 1. Check if application is in shutdown / draining lifecycle
    shutting_down = is_shutting_down()
    runner = get_job_runner()
    is_draining = runner.is_draining or runner.is_stopped or shutting_down

    if shutting_down or is_draining:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unready: Application is shutting down or draining background tasks.",
            headers={"Retry-After": "15"},
        )

    dataset_ready = DATA_PATH.exists()
    analytics_ready = False
    analytics_count = 0
    if dataset_ready:
        try:
            from backend.tools.analytics import load_transaction_data
            df = load_transaction_data()
            analytics_count = len(df)
            analytics_ready = analytics_count > 0
        except Exception as e:
            logger.warning(f"Readiness check analytics notice: {type(e).__name__}: {e}")

    llm_info = get_llm_info()
    llm_ready = True  # LLM or deterministic fallback is always operational
    runner_ready = runner.is_running

    is_ready = dataset_ready and analytics_ready and runner_ready

    readiness = ReadinessResponse(
        status="ready" if is_ready else "unready",
        service="paypilot",
        checks={
            "dataset_accessible": dataset_ready,
            "analytics_engine_ready": analytics_ready,
            "llm_provider_initialized": llm_ready,
            "job_runner_ready": runner_ready,
            "accepting_traffic": not shutting_down,
        },
        details={
            "total_transactions_loaded": analytics_count,
            "active_llm_provider": llm_info.get("active_provider", "nvidia"),
            "model": llm_info.get("active_model", NVIDIA_MODEL),
            "is_live_llm": llm_info.get("is_live_llm", False),
            "runner_state": runner.state.value,
        },
    )

    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service unready: dataset or analytics component unavailable (dataset_ready={dataset_ready}, analytics_ready={analytics_ready}).",
        )

    return readiness


@router.post(
    "/api/v1/auth/session",
    tags=["System"],
    summary="Acquire Frontend Session Token",
    description=(
        "Issues a short-lived, HMAC-signed session token for static frontend clients. "
        "The frontend calls this endpoint on initialization; the token is used as a "
        "Bearer credential for all subsequent API calls. The actual API key is never "
        "transmitted to the browser. Requests must originate from a CORS-allowed origin."
    ),
    responses={
        200: {"description": "Session token issued successfully"},
        403: {"description": "Origin not allowed or API key not configured"},
    },
)
async def acquire_session_token(
    raw_request: Request,
) -> Dict[str, Any]:
    """Issues a session token for the requesting frontend origin.

    Security: This endpoint does NOT require an existing API key. It is protected by:
    1. CORS origin allowlist (only the deployed frontend can call it)
    2. The token it issues grants analyst-level access only (never admin)
    3. Tokens are short-lived (default 1 hour TTL)
    """
    from backend.config import CORS_ALLOWED_ORIGINS
    from backend.security.session import create_session_token, SESSION_TOKEN_TTL_SECONDS

    # Extract origin from the request
    origin = raw_request.headers.get("origin", "").strip()

    if not origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin header is required for session token issuance.",
        )

    # Validate the origin is in the CORS allowlist
    if origin not in CORS_ALLOWED_ORIGINS:
        logger.warning(f"Session token request from disallowed origin: {origin}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin not in allowed origins list.",
        )

    token = create_session_token(origin)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session token issuance is unavailable (API key not configured).",
        )

    return {
        "session_token": token,
        "token_type": "Bearer",
        "expires_in_seconds": SESSION_TOKEN_TTL_SECONDS,
        "granted_role": "analyst",
    }



@router.get(
    "/metrics",
    tags=["System"],
    summary="Observability & Operational Metrics Telemetry",
    description="Returns thread-safe aggregated telemetry on requests, agent performance, LLM provider metrics, and error taxonomies.",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Admin access required"},
    },
)
async def get_metrics(
    user: AuthenticatedUser = Depends(require_admin),
) -> Dict[str, Any]:
    """Returns runtime telemetry snapshot."""
    from backend.observability.metrics import get_metrics_snapshot
    return get_metrics_snapshot()


@router.get(
    "/admin/audit",
    response_model=AuditTrailResponse,
    responses={
        200: {"model": AuditTrailResponse, "description": "Paginated audit trail log"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Admin access required"},
    },
    tags=["System"],
    summary="Administrative Compliance & Traceability Audit Log",
    description="Returns paginated structured audit events for compliance, security monitoring, and operational traceability.",
)
async def get_audit_trail(
    limit: int = 50,
    offset: int = 0,
    event_type: Optional[str] = None,
    request_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(require_admin),
) -> AuditTrailResponse:
    """Returns paginated audit records for administrative and compliance review."""
    from datetime import datetime, timezone
    from backend.observability.audit import get_audit_store

    store = get_audit_store()
    events = store.get_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        request_id=request_id,
    )
    total = store.count()

    event_schemas = [
        AuditEventSchema(
            event_id=e.event_id,
            timestamp=e.timestamp,
            event_type=e.event_type,
            request_id=e.request_id,
            endpoint=e.endpoint,
            http_method=e.http_method,
            client_id=e.client_id,
            role=e.role,
            intent=e.intent,
            executed_agents=e.executed_agents,
            status=e.status,
            status_code=e.status_code,
            duration_ms=e.duration_ms,
            llm_provider=e.llm_provider,
            model=e.model,
            retry_count=e.retry_count,
            fallback_used=e.fallback_used,
            error_category=e.error_category,
            query_summary=e.query_summary,
        )
        for e in events
    ]

    return AuditTrailResponse(
        total_events_retained=total,
        limit=limit,
        offset=offset,
        events=event_schemas,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/api/v1/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"model": JobResponse, "description": "Job accepted and queued for background execution"},
        400: {"model": ErrorResponse, "description": "Invalid query or payload"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        409: {"model": ErrorResponse, "description": "Idempotency Conflict"},
        429: {"model": ErrorResponse, "description": "Queue full or rate limit or quota exceeded"},
    },
    tags=["Jobs"],
    summary="Submit Asynchronous Analysis Job",
    description="Dispatches a long-running merchant diagnostic task to the background execution queue with idempotency protection.",
)
async def submit_job(
    request: JobCreateRequest,
    raw_request: Request,
    user: AuthenticatedUser = Depends(require_analyst),
) -> JobResponse:
    """Submits an asynchronous background diagnostic task with idempotency and quota enforcement."""
    cleaned_query = request.query.strip()
    if not cleaned_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job query cannot be empty or whitespace.",
        )

    if len(cleaned_query) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job query exceeds maximum allowable length of {MAX_QUERY_LENGTH} characters.",
        )

    runner = get_job_runner()
    request_id = getattr(raw_request.state, "request_id", str(uuid.uuid4()))
    trace_id = getattr(raw_request.state, "trace_id", None)

    # 1. Idempotency Key Handling
    idempotency_key = raw_request.headers.get("Idempotency-Key")
    payload_hash = ""
    is_idempotent = False
    clean_key = ""
    reservation_succeeded = False
    reservation_completed = False

    if idempotency_key is not None and idempotency_key.strip():
        clean_key = idempotency_key.strip()
        is_valid, err_msg = validate_idempotency_key(clean_key)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Idempotency-Key header: {err_msg}",
            )

        payload_hash = compute_payload_hash({
            "query": cleaned_query,
            "task_type": request.task_type,
            "metadata": request.metadata or {},
        })

        idempotency_store = get_idempotency_store()
        res_status, existing_rec = idempotency_store.reserve(
            tenant_id=user.client_id,
            key=clean_key,
            payload_hash=payload_hash,
        )

        if res_status == IdempotencyReservationStatus.CONFLICT:
            record_idempotency_conflict()
            record_audit_event(
                event_type="idempotency_conflict",
                request_id=request_id,
                endpoint="/api/v1/jobs",
                http_method="POST",
                client_id=user.client_id,
                role=user.role,
                status="rejected",
                status_code=status.HTTP_409_CONFLICT,
                error_category="idempotency_conflict",
                query_summary=f"conflict:{fingerprint_idempotency_key(clean_key)}",
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency conflict: The provided Idempotency-Key has already been used for a different request payload.",
            )

        if res_status == IdempotencyReservationStatus.REPLAY:
            record_idempotency_replay()
            # If the winning concurrent request is still queueing/completing, wait briefly for its record
            if existing_rec and not existing_rec.response_payload and not existing_rec.job_id:
                completed_rec = idempotency_store.wait_for_completion(user.client_id, clean_key, timeout_seconds=4.0)
                if completed_rec:
                    existing_rec = completed_rec

            record_audit_event(
                event_type="idempotency_replay",
                request_id=request_id,
                endpoint="/api/v1/jobs",
                http_method="POST",
                client_id=user.client_id,
                role=user.role,
                status="success",
                status_code=status.HTTP_202_ACCEPTED,
                query_summary=f"replay:{fingerprint_idempotency_key(clean_key)}",
            )

            if existing_rec and existing_rec.job_id:
                existing_job = runner.get_job(existing_rec.job_id, client_id=user.client_id, role=user.role)
                if existing_job:
                    err_dict = None
                    if existing_job.error:
                        if isinstance(existing_job.error, dict):
                            err_dict = existing_job.error
                        else:
                            err_dict = {"message": str(existing_job.error)}
                    return JobResponse(
                        job_id=existing_job.job_id,
                        task_type=existing_job.task_type,
                        client_id=existing_job.client_id,
                        role=existing_job.role,
                        request_id=existing_job.request_id,
                        trace_id=existing_job.trace_id,
                        status=existing_job.status,
                        created_at=existing_job.created_at,
                        started_at=existing_job.started_at,
                        completed_at=existing_job.completed_at,
                        duration_ms=existing_job.duration_ms,
                        query_summary=cleaned_query[:60],
                        result=existing_job.result,
                        error=err_dict,
                    )

            if existing_rec and existing_rec.response_payload:
                return JobResponse(**existing_rec.response_payload)
            
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Concurrent job submission in progress for this Idempotency-Key. Please retry shortly.",
            )

        is_idempotent = True
        reservation_succeeded = True

    # 2. Check Tenant Daily Job Quota
    quota_mgr = get_quota_manager()
    quota_allowed, current_jobs, max_jobs = quota_mgr.check_and_consume_job_quota(user.client_id)
    if not quota_allowed:
        if is_idempotent and reservation_succeeded:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        record_quota_rejection()
        record_audit_event(
            event_type="quota_exceeded",
            request_id=request_id,
            endpoint="/api/v1/jobs",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="rejected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_category="quota_exceeded",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily background job quota exceeded ({current_jobs}/{max_jobs} jobs today).",
            headers={"Retry-After": "3600"},
        )

    # 3. Check Tenant Active Concurrent Job Limit
    conc_allowed, active_jobs, max_active = quota_mgr.check_concurrent_job_limit(user.client_id)
    if not conc_allowed:
        if is_idempotent and reservation_succeeded:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        quota_mgr.rollback_job_quota(user.client_id)
        record_concurrency_rejection()
        record_audit_event(
            event_type="concurrency_limit_exceeded",
            request_id=request_id,
            endpoint="/api/v1/jobs",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="rejected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_category="concurrency_limit_exceeded",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Tenant concurrent active job limit reached ({active_jobs}/{max_active} active jobs). Please wait for ongoing jobs to complete.",
            headers={"Retry-After": "10"},
        )

    # 4. Submit Job
    try:
        quota_mgr.record_job_started(user.client_id)
        job = runner.submit_job(
            task_type=request.task_type,
            client_id=user.client_id,
            role=user.role,
            request_id=request_id,
            trace_id=trace_id,
            parameters={"query": cleaned_query, "metadata": request.metadata or {}},
            target_fn=run_async_analysis_task,
            query=cleaned_query,
        )

        resp = JobResponse(
            job_id=job.job_id,
            task_type=job.task_type,
            client_id=job.client_id,
            role=job.role,
            request_id=job.request_id,
            trace_id=job.trace_id,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            duration_ms=job.duration_ms,
            query_summary=cleaned_query[:60],
            result=job.result,
            error=job.error,
        )

        if is_idempotent and clean_key:
            get_idempotency_store().complete(
                tenant_id=user.client_id,
                key=clean_key,
                job_id=job.job_id,
                response_payload=resp.model_dump(),
                status="completed",
            )
            reservation_completed = True

        return resp
    except (JobRunnerDrainingError, JobRunnerStoppedError) as exc:
        record_queue_full_rejection()
        quota_mgr.record_job_finished(user.client_id)
        quota_mgr.rollback_job_quota(user.client_id)
        if is_idempotent and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"Retry-After": "15"},
        )
    except JobQueueFullError as exc:
        record_queue_full_rejection()
        quota_mgr.record_job_finished(user.client_id)
        quota_mgr.rollback_job_quota(user.client_id)
        if is_idempotent and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "15"},
        )
    except Exception as exc:
        quota_mgr.record_job_finished(user.client_id)
        quota_mgr.rollback_job_quota(user.client_id)
        if is_idempotent and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        logger.error(f"Failed to submit background job for tenant '{user.client_id}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error while submitting background job.",
        )


@router.get(
    "/api/v1/jobs/{job_id}",
    response_model=JobResponse,
    responses={
        200: {"model": JobResponse, "description": "Job status and output payload"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Cannot access jobs owned by another client"},
        404: {"model": ErrorResponse, "description": "Job not found"},
    },
    tags=["Jobs"],
    summary="Get Background Job Status",
    description="Retrieves execution status, progress, and results for a background job.",
)
async def get_job_status(
    job_id: str,
    user: AuthenticatedUser = Depends(require_analyst),
) -> JobResponse:
    """Fetches job lifecycle status and results respecting tenant ownership."""
    runner = get_job_runner()
    job = runner.get_job(job_id=job_id, client_id=user.client_id, role=user.role)
    if not job:
        unfiltered = runner.store.get_job(job_id=job_id)
        if unfiltered and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: You do not have permission to access this background job.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    return JobResponse(
        job_id=job.job_id,
        task_type=job.task_type,
        client_id=job.client_id,
        role=job.role,
        request_id=job.request_id,
        trace_id=job.trace_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_ms=job.duration_ms,
        query_summary=job.parameters.get("query", "")[:60] if job.parameters else None,
        result=job.result,
        error=job.error,
    )


@router.get(
    "/api/v1/jobs",
    response_model=JobListResponse,
    responses={
        200: {"model": JobListResponse, "description": "Paginated jobs list"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
    },
    tags=["Jobs"],
    summary="List Background Jobs",
    description="Returns paginated background jobs for the authenticated principal (or all for admin).",
)
async def list_jobs(
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
    user: AuthenticatedUser = Depends(require_analyst),
) -> JobListResponse:
    """Lists paginated background jobs."""
    runner = get_job_runner()
    jobs = runner.list_jobs(
        client_id=user.client_id,
        role=user.role,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    total = runner.count(client_id=user.client_id, role=user.role)

    job_schemas = [
        JobResponse(
            job_id=j.job_id,
            task_type=j.task_type,
            client_id=j.client_id,
            role=j.role,
            request_id=j.request_id,
            trace_id=j.trace_id,
            status=j.status,
            created_at=j.created_at,
            started_at=j.started_at,
            completed_at=j.completed_at,
            duration_ms=j.duration_ms,
            query_summary=j.parameters.get("query", "")[:60] if j.parameters else None,
            result=j.result,
            error=j.error,
        )
        for j in jobs
    ]

    return JobListResponse(
        total_jobs=total,
        limit=limit,
        offset=offset,
        jobs=job_schemas,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.post(
    "/api/v1/recommendations/deploy",
    response_model=DeployRecommendationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"model": DeployRecommendationResponse, "description": "Recommendation deployment accepted and enqueued"},
        400: {"model": ErrorResponse, "description": "Invalid action rank or payload"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        409: {"model": ErrorResponse, "description": "Idempotency Conflict"},
        422: {"model": ErrorResponse, "description": "Validation Error"},
        429: {"model": ErrorResponse, "description": "Queue full or quota exceeded"},
    },
    tags=["Actions"],
    summary="Deploy Automated Recovery Recommendation",
    description="Dispatches a targeted automated revenue recovery rollout task into the background execution pool with idempotency protection and audit logging.",
)
async def deploy_recommendation(
    request: DeployRecommendationRequest,
    raw_request: Request,
    user: AuthenticatedUser = Depends(require_analyst),
) -> DeployRecommendationResponse:
    """Deploys an approved revenue recovery action to the background execution runner."""
    cleaned_title = request.action_title.strip()
    if not cleaned_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recommendation action title cannot be empty or whitespace.",
        )

    if request.action_rank < 1 or request.action_rank > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action_rank {request.action_rank}. Must be between 1 and 20.",
        )

    runner = get_job_runner()
    request_id = getattr(raw_request.state, "request_id", str(uuid.uuid4()))
    trace_id = getattr(raw_request.state, "trace_id", None)
    deployment_id = f"dep_{uuid.uuid4().hex[:12]}"

    # 1. Idempotency Handling
    idempotency_key = raw_request.headers.get("Idempotency-Key")
    clean_key = ""
    is_idempotent = False
    reservation_succeeded = False
    reservation_completed = False

    if idempotency_key is not None and idempotency_key.strip():
        clean_key = idempotency_key.strip()
        is_valid, err_msg = validate_idempotency_key(clean_key)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid Idempotency-Key header: {err_msg}",
            )

        payload_hash = compute_payload_hash({
            "action_rank": request.action_rank,
            "action_title": cleaned_title,
            "affected_area": request.affected_area or "",
            "estimated_revenue_impact_inr": float(request.estimated_revenue_impact_inr or 0.0),
        })

        idempotency_store = get_idempotency_store()
        res_status, existing_rec = idempotency_store.reserve(
            tenant_id=user.client_id,
            key=clean_key,
            payload_hash=payload_hash,
            ttl_seconds=86400,
        )

        if res_status == IdempotencyReservationStatus.REPLAY:
            record_idempotency_replay()
            logger.info(
                f"[{request_id}] Returning replayed recommendation deployment for key {fingerprint_idempotency_key(clean_key)}"
            )
            return DeployRecommendationResponse(**existing_rec.response_payload)

        elif res_status == IdempotencyReservationStatus.CONFLICT:
            record_idempotency_conflict()
            logger.warning(
                f"[{request_id}] Idempotency conflict for key {fingerprint_idempotency_key(clean_key)}"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Idempotency conflict: Key '{clean_key}' was previously used with different parameters.",
            )

        reservation_succeeded = True
        is_idempotent = True

    # 2. Check Tenant Daily Job Quota
    quota_mgr = get_quota_manager()
    quota_allowed, current_jobs, max_jobs = quota_mgr.check_and_consume_job_quota(user.client_id)
    if not quota_allowed:
        if is_idempotent and reservation_succeeded:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        record_quota_rejection()
        record_audit_event(
            event_type="quota_exceeded",
            request_id=request_id,
            endpoint="/api/v1/recommendations/deploy",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="rejected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_category="quota_exceeded",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily background job quota exceeded ({current_jobs}/{max_jobs} jobs today).",
            headers={"Retry-After": "3600"},
        )

    # 3. Check Tenant Active Concurrent Job Limit
    conc_allowed, active_jobs, max_active = quota_mgr.check_concurrent_job_limit(user.client_id)
    if not conc_allowed:
        if is_idempotent and reservation_succeeded:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        quota_mgr.rollback_job_quota(user.client_id)
        record_concurrency_rejection()
        record_audit_event(
            event_type="concurrency_limit_exceeded",
            request_id=request_id,
            endpoint="/api/v1/recommendations/deploy",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="rejected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_category="concurrency_limit_exceeded",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Tenant concurrent active job limit reached ({active_jobs}/{max_active} active jobs). Please wait for ongoing jobs to complete.",
            headers={"Retry-After": "10"},
        )

    # 4. Submit Background Execution Job
    try:
        quota_mgr.record_job_started(user.client_id)
        job_query = f"Deploy recommendation P{request.action_rank}: {cleaned_title}"
        job = runner.submit_job(
            task_type="action_deployment",
            client_id=user.client_id,
            role=user.role,
            request_id=request_id,
            trace_id=trace_id,
            parameters={
                "query": job_query,
                "deployment_id": deployment_id,
                "action_rank": request.action_rank,
                "action_title": cleaned_title,
                "affected_area": request.affected_area,
                "estimated_revenue_impact_inr": request.estimated_revenue_impact_inr or 0.0,
                "parameters": request.parameters or {},
            },
            target_fn=run_async_analysis_task,
            query=job_query,
        )

        response_payload = DeployRecommendationResponse(
            deployment_id=deployment_id,
            job_id=job.job_id,
            action_rank=request.action_rank,
            action_title=cleaned_title,
            status="enqueued",
            enqueued_at=datetime.now(timezone.utc).isoformat(),
            client_id=user.client_id,
            role=user.role,
            estimated_revenue_impact_inr=float(request.estimated_revenue_impact_inr or 0.0),
            message=f"Recommendation P{request.action_rank} ({cleaned_title}) successfully enqueued for automated rollout.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 5. Audit Trail Event
        record_audit_event(
            event_type="recommendation_deployed",
            request_id=request_id,
            endpoint="/api/v1/recommendations/deploy",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="accepted",
            status_code=202,
            query_summary=f"P{request.action_rank}: {cleaned_title[:50]}",
        )

        # 6. Complete Idempotency Reservation
        if is_idempotent and clean_key:
            get_idempotency_store().complete(
                tenant_id=user.client_id,
                key=clean_key,
                job_id=job.job_id,
                response_payload=response_payload.model_dump(),
                status="completed",
            )
            reservation_completed = True

        return response_payload

    except (JobRunnerDrainingError, JobRunnerStoppedError) as exc:
        quota_mgr.record_job_finished(user.client_id)
        if is_idempotent and clean_key and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service draining/stopped: {str(exc)}",
            headers={"Retry-After": "30"},
        )
    except JobQueueFullError as exc:
        quota_mgr.record_job_finished(user.client_id)
        record_queue_full_rejection()
        if is_idempotent and clean_key and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "10"},
        )
    except Exception as exc:
        quota_mgr.record_job_finished(user.client_id)
        if is_idempotent and clean_key and reservation_succeeded and not reservation_completed:
            get_idempotency_store().cancel_reservation(user.client_id, clean_key)
        logger.error(f"[{request_id}] Failed to deploy recommendation: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while dispatching the recommendation deployment.",
        )


@router.post(
    "/api/v1/analyze",
    response_model=AnalyzeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or oversized query"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Internal workflow error"},
    },
    tags=["Analysis"],
    summary="Execute Agentic Revenue Recovery Analysis",
    description="Invokes the full PayPilot LangGraph multi-agent pipeline to diagnose revenue loss and generate ranked action plans.",
)
async def analyze_merchant_query(
    request: AnalyzeRequest,
    raw_request: Request,
    user: AuthenticatedUser = Depends(require_analyst),
) -> AnalyzeResponse:
    """Runs the LangGraph multi-agent diagnostic and action prioritization pipeline."""

    # 1. Validation
    cleaned_query = request.query.strip()
    raw_request.state.client_id = user.client_id
    raw_request.state.role = user.role
    raw_request.state.query_summary = cleaned_query

    if not cleaned_query:
        logger.warning("Rejecting empty/whitespace query in /api/v1/analyze.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Merchant query cannot be empty or whitespace.",
        )

    if len(cleaned_query) > MAX_QUERY_LENGTH:
        logger.warning(f"Rejecting oversized query ({len(cleaned_query)} chars) in /api/v1/analyze.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query exceeds maximum allowable length of {MAX_QUERY_LENGTH} characters.",
        )

    # 2. Extract or generate tracking Request ID
    request_id = getattr(raw_request.state, "request_id", str(uuid.uuid4()))
    start_time = time.perf_counter()

    # 3. Check Tenant Daily Analyze Quota
    quota_mgr = get_quota_manager()
    quota_allowed, current_reqs, max_reqs = quota_mgr.check_and_consume_analyze_quota(user.client_id)
    if not quota_allowed:
        record_quota_rejection()
        record_audit_event(
            event_type="quota_exceeded",
            request_id=request_id,
            endpoint="/api/v1/analyze",
            http_method="POST",
            client_id=user.client_id,
            role=user.role,
            status="rejected",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_category="quota_exceeded",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily analysis quota exceeded for tenant ({current_reqs}/{max_reqs} requests today).",
            headers={"Retry-After": "3600"},
        )

    logger.info(f"[{request_id}] Starting PayPilot analysis for query: '{cleaned_query[:60]}...'")

    try:
        # 4. Execute existing LangGraph multi-agent workflow asynchronously in thread pool
        import asyncio
        result = await asyncio.to_thread(run_pipeline, cleaned_query)

        # 4. Measure duration
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        llm_info = get_llm_info()

        # 5. Extract structured fields from state
        intent = result.get("intent", "general_business_analysis")
        raw_request.state.intent = intent
        executed_agents = result.get("executed_agents", [])
        raw_request.state.executed_agents = executed_agents
        raw_request.state.llm_provider = llm_info.get("active_provider", "nvidia")
        active_model_str = llm_info.get("active_model", NVIDIA_MODEL)
        raw_request.state.model = active_model_str
        raw_request.state.fallback_used = not llm_info.get("is_live_llm", False)

        node_models_dict = {
            "supervisor": llm_info.get("supervisor_model", SUPERVISOR_MODEL),
            "aggregator": llm_info.get("aggregator_model", AGGREGATOR_MODEL),
            "recovery": llm_info.get("recovery_model", RECOVERY_MODEL),
        } if llm_info.get("is_live_llm") else None

        evidence = result.get("evidence", {}) or {}
        analysis = result.get("analysis", {}) or {}
        key_facts = analysis.get("key_facts", {})
        raw_actions = result.get("priority_actions", []) or result.get("recovery_actions", [])
        final_answer = result.get("final_answer", "") or ""
        estimated_recovery = result.get("estimated_recovery", {}) or {}

        # Format prioritized actions into Pydantic models
        prioritized_items: List[PrioritizedActionItem] = []
        for a in raw_actions:
            prioritized_items.append(
                PrioritizedActionItem(
                    rank=a.get("rank", len(prioritized_items) + 1),
                    action=a.get("action", "Action Item"),
                    problem=a.get("problem", "Identified friction point"),
                    affected_area=a.get("affected_area", "Payment Operations"),
                    estimated_revenue_impact_inr=float(a.get("estimated_revenue_impact_inr", 0.0)),
                    observed_loss_inr=float(a.get("observed_loss_inr", 0.0)),
                    confidence=float(a.get("confidence", 0.8)),
                    effort=a.get("effort", "Medium"),
                    urgency=a.get("urgency", "Medium"),
                    priority_score=float(a.get("priority_score", 0.0)),
                    reasoning=a.get("reasoning", ""),
                    metrics=a.get("metrics"),
                )
            )

        # Generate summary lists
        revenue_leaks = _extract_revenue_leaks(evidence)
        exec_recommendation = _extract_executive_recommendation(raw_actions, final_answer)

        # Compile execution metadata
        trace_id = getattr(raw_request.state, "trace_id", None)
        meta = ExecutionMetadata(
            request_id=request_id,
            trace_id=trace_id,
            query=cleaned_query,
            detected_intent=intent,
            executed_agents=executed_agents,
            execution_duration_ms=duration_ms,
            llm_provider=llm_info.get("active_provider", "nvidia"),
            model=active_model_str,
            node_models=node_models_dict,
            is_live_llm=llm_info.get("is_live_llm", False),
            success=True,
        )

        logger.info(
            f"[{request_id}][{trace_id}] Analysis completed in {duration_ms}ms | Intent: '{intent}' | "
            f"Agents: {executed_agents} | Actions: {len(prioritized_items)}"
        )

        return AnalyzeResponse(
            query=cleaned_query,
            intent=intent,
            agents_participated=executed_agents,
            key_facts=key_facts,
            revenue_leaks=revenue_leaks,
            prioritized_actions=prioritized_items,
            executive_recommendation=exec_recommendation,
            final_answer=final_answer,
            estimated_recovery=estimated_recovery,
            llm_provider=llm_info.get("active_provider", "nvidia"),
            model=active_model_str,
            node_models=node_models_dict,
            is_live_llm=llm_info.get("is_live_llm", False),
            execution_metadata=meta,
        )

    except HTTPException:
        raise
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.error(f"[{request_id}] Workflow exception after {duration_ms}ms: {type(exc).__name__}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during workflow execution. Please verify internal services and try again.",
        )


@router.get(
    "/admin/traces/{trace_id}",
    response_model=TraceResponseSchema,
    responses={
        200: {"model": TraceResponseSchema, "description": "Distributed trace spans and execution hierarchy"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Administrator access required"},
        404: {"model": ErrorResponse, "description": "Trace not found or evicted"},
    },
    tags=["Admin"],
    summary="Get Distributed Trace Spans",
    description="Retrieves the complete hierarchical span tree for a distributed trace. Requires administrator privileges.",
)
async def get_trace_details(
    trace_id: str,
    user: AuthenticatedUser = Depends(require_admin),
) -> TraceResponseSchema:
    """Fetches full hierarchical span tree for an administrative distributed trace audit."""
    clean_id = trace_id.strip()
    store = get_trace_store()
    spans = store.get_trace(clean_id)
    if not spans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace '{clean_id}' not found or has been evicted from memory.",
        )

    root_span = spans[0]
    total_dur = 0.0
    if root_span.duration_ms is not None:
        total_dur = root_span.duration_ms
    elif len(spans) > 1:
        total_dur = sum(s.duration_ms or 0.0 for s in spans)

    has_errors = any(s.status == "ERROR" for s in spans)

    span_schemas = [
        TraceSpanSchema(
            trace_id=s.trace_id,
            span_id=s.span_id,
            parent_span_id=s.parent_span_id,
            request_id=s.request_id,
            operation_name=s.operation_name,
            component=s.component,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_ms=s.duration_ms,
            status=s.status,
            error_category=s.error_category,
            error_message=s.error_message,
            metadata=s.metadata,
        )
        for s in spans
    ]

    return TraceResponseSchema(
        trace_id=clean_id,
        span_count=len(spans),
        root_operation=root_span.operation_name,
        root_component=root_span.component,
        total_duration_ms=round(total_dur, 2),
        status="ERROR" if has_errors else "OK",
        spans=span_schemas,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/admin/slo",
    response_model=SLOResponseSchema,
    responses={
        200: {"model": SLOResponseSchema, "description": "Operational SLO evaluation and breach report"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Administrator access required"},
    },
    tags=["Admin"],
    summary="Get SLO Status & Breach Report",
    description="Evaluates operational metrics against configured SLO targets and reports breach statuses with alert deduplication. Requires administrator privileges.",
)
async def get_slo_status(
    user: AuthenticatedUser = Depends(require_admin),
) -> SLOResponseSchema:
    """Evaluates active operational metrics against configured SLO targets."""
    eval_result = evaluate_slo_breaches()
    return SLOResponseSchema(
        overall_status=eval_result["overall_status"],
        total_slos_evaluated=eval_result["total_slos_evaluated"],
        active_breaches_count=eval_result["active_breaches_count"],
        new_alerts_emitted_count=eval_result["new_alerts_emitted_count"],
        evaluated_slos=eval_result["evaluated_slos"],
        active_breaches=eval_result["active_breaches"],
        new_alerts_emitted=eval_result["new_alerts_emitted"],
        metrics_evaluated=eval_result["metrics_evaluated"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/admin/config",
    response_model=ConfigDiagnosticsSchema,
    responses={
        200: {"model": ConfigDiagnosticsSchema, "description": "Operational configuration diagnostics and non-secret snapshot"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Forbidden - Administrator access required"},
    },
    tags=["Admin"],
    summary="Get Configuration Diagnostics",
    description="Returns sanitized configuration topology and secret configuration presence statuses without exposing raw credentials. Requires administrator privileges.",
)
async def get_configuration_diagnostics(
    user: AuthenticatedUser = Depends(require_admin),
) -> ConfigDiagnosticsSchema:
    """Retrieves safe configuration diagnostics and sanitized snapshot."""
    diag = get_config_diagnostics()
    return ConfigDiagnosticsSchema(
        status=diag["status"],
        environment=diag["environment"],
        llm_provider=diag["llm_provider"],
        model=diag["model"],
        database_backend=diag["database_backend"],
        job_store=diag["job_store"],
        rate_limit_backend=diag["rate_limit_backend"],
        tracing=diag["tracing"],
        secrets_status=diag["secrets_status"],
        snapshot=diag["snapshot"],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


