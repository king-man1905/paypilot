/**
 * PayPilot Type-Safe API Client
 *
 * Read-only telemetry endpoints (health/readiness/jobs list/audit/SLO/config) fall back to
 * demo mock data when the backend is unreachable, since they only affect dashboard chrome.
 * Result-bearing endpoints (analyze/submitJob/deployRecommendation) do NOT fall back to mock
 * data — a failed request must surface as a visible error, never as a fabricated AI result.
 */

import {
  AnalyzeResponse,
  AuditTrailResponse,
  ConfigDiagnosticsSchema,
  DeployRecommendationResponse,
  HealthResponse,
  JobListResponse,
  JobResponse,
  PrioritizedActionItem,
  ReadinessResponse,
  SLOResponseSchema,
} from '../types/api';
import {
  MOCK_AUDIT_LOGS,
  MOCK_CONFIG_DIAGNOSTICS,
  MOCK_JOBS,
  MOCK_SLO_STATUS,
} from './mockData';

const DEFAULT_PROD_API_URL = 'https://paypilot-pjye.onrender.com';

const getBaseUrl = (): string => {
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL?.trim()) {
    return import.meta.env.VITE_API_BASE_URL.trim();
  }
  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname;
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      return '';
    }
  }
  return DEFAULT_PROD_API_URL;
};

export const BASE_URL = getBaseUrl().replace(/\/+$/, '');

class PayPilotApiClient {
  private getHeaders(customHeaders?: Record<string, string>): HeadersInit {
    // No hardcoded key fallback: an unconfigured key means an empty header, which the backend
    // correctly rejects with 401 — never silently authenticate with a baked-in credential.
    const apiKey = (typeof localStorage !== 'undefined' && localStorage.getItem('paypilot_api_key')) || '';
    const clientId = (typeof localStorage !== 'undefined' && localStorage.getItem('paypilot_client_id')) || 'merchant_enterprise_01';

    return {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey,
      'X-Client-ID': clientId,
      ...(customHeaders || {}),
    };
  }

  /**
   * Health Liveness Probe
   */
  async getHealth(): Promise<HealthResponse> {
    try {
      const res = await fetch(`${BASE_URL}/health`, {
        headers: this.getHeaders(),
      });
      if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
      return await res.json();
    } catch {
      return {
        status: 'healthy',
        service: 'paypilot',
        llm_provider: 'nvidia',
        model: 'nvidia/nemotron-3-super-120b-a12b',
        is_live_llm: false,
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Readiness Probe
   */
  async getReadiness(): Promise<ReadinessResponse> {
    try {
      const res = await fetch(`${BASE_URL}/ready`, {
        headers: this.getHeaders(),
      });
      if (!res.ok) throw new Error(`Readiness check failed: ${res.status}`);
      return await res.json();
    } catch {
      return {
        status: 'ready',
        service: 'paypilot',
        checks: {
          dataset_accessible: true,
          analytics_engine_ready: true,
          llm_provider_initialized: true,
          job_runner_ready: true,
          accepting_traffic: true,
        },
        details: {
          total_transactions_loaded: 15000,
          active_llm_provider: 'nvidia',
          model: 'nvidia/nemotron-3-super-120b-a12b',
          is_live_llm: false,
          runner_state: 'RUNNING',
        },
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Real-Time Synchronous Multi-Agent Analysis.
   *
   * Intentionally does NOT catch-and-fall-back to mock data: a failed/unreachable backend
   * must surface as a visible error to the caller, never as a silently substituted fake
   * analysis result (see IntelligencePage's error state handling).
   */
  async analyze(query: string): Promise<AnalyzeResponse> {
    const res = await fetch(`${BASE_URL}/api/v1/analyze`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ query: query.trim() }),
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail || `Analysis failed: ${res.statusText}`);
    }

    return await res.json();
  }

  /**
   * Submit Asynchronous Background Analysis / Deployment Job.
   *
   * Intentionally does NOT fall back to a fabricated local job on failure — see analyze().
   */
  async submitJob(
    query: string,
    idempotencyKey?: string,
    taskType: string = 'async_analysis',
    metadata?: Record<string, any>
  ): Promise<JobResponse> {
    const customHeaders: Record<string, string> = {};
    if (idempotencyKey) {
      customHeaders['Idempotency-Key'] = idempotencyKey;
    }

    const res = await fetch(`${BASE_URL}/api/v1/jobs`, {
      method: 'POST',
      headers: this.getHeaders(customHeaders),
      body: JSON.stringify({
        query: query.trim(),
        task_type: taskType,
        ...(metadata ? { metadata } : {}),
      }),
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail || `Job submission failed: ${res.statusText}`);
    }

    return await res.json();
  }

  /**
   * Deploy Automated Revenue Recovery Recommendation.
   *
   * Falls back from the dedicated /api/v1/recommendations/deploy route to /api/v1/jobs only on
   * a 404 (older backend revision without the dedicated route) — that is a real, intentional
   * compatibility path. It does NOT fall back to a fabricated "success" response on network or
   * server failure: a deployment that never reached the backend must not be reported as enqueued.
   */
  async deployRecommendation(
    item: PrioritizedActionItem,
    idempotencyKey?: string,
    parameters?: Record<string, any>
  ): Promise<DeployRecommendationResponse> {
    const customHeaders: Record<string, string> = {};
    const idemp =
      idempotencyKey ||
      `idemp_deploy_${item.rank}_${Date.now()}_${Math.random().toString(36).substring(7)}`;
    customHeaders['Idempotency-Key'] = idemp;

    // 1. Try dedicated recommendations deployment endpoint
    const res = await fetch(`${BASE_URL}/api/v1/recommendations/deploy`, {
      method: 'POST',
      headers: this.getHeaders(customHeaders),
      body: JSON.stringify({
        action_rank: item.rank,
        action_title: item.action,
        affected_area: item.affected_area,
        estimated_revenue_impact_inr: item.estimated_revenue_impact_inr,
        parameters: parameters || {},
      }),
    });

    if (res.ok) {
      return (await res.json()) as DeployRecommendationResponse;
    }

    // 2. If 404 (remote server running earlier revision without dedicated route), fall back to /api/v1/jobs
    if (res.status === 404) {
      const deploymentQuery = `Deploy recommendation P${item.rank}: ${item.action}`;
      const job = await this.submitJob(
        deploymentQuery,
        idemp,
        'action_deployment',
        {
          action_rank: item.rank,
          action_title: item.action,
          affected_area: item.affected_area,
          estimated_revenue_impact_inr: item.estimated_revenue_impact_inr,
          ...(parameters || {}),
        }
      );

      return {
        deployment_id: `dep_${job.job_id.replace(/^job_/, '')}`,
        job_id: job.job_id,
        action_rank: item.rank,
        action_title: item.action,
        status: job.status,
        enqueued_at: job.created_at,
        client_id: job.client_id,
        role: job.role,
        estimated_revenue_impact_inr: item.estimated_revenue_impact_inr,
        message: `Recommendation P${item.rank} (${item.action}) successfully enqueued for automated rollout.`,
        timestamp: new Date().toISOString(),
      };
    }

    const errPayload = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errPayload.detail || `Deployment failed with status ${res.status}`);
  }

  /**
   * List Background Jobs
   */
  async listJobs(limit = 20, offset = 0, statusFilter?: string): Promise<JobListResponse> {
    try {
      const params = new URLSearchParams();
      params.set('limit', limit.toString());
      params.set('offset', offset.toString());
      if (statusFilter) params.set('status_filter', statusFilter);

      const res = await fetch(`${BASE_URL}/api/v1/jobs?${params.toString()}`, {
        headers: this.getHeaders(),
      });

      if (!res.ok) throw new Error(`List jobs failed: ${res.status}`);
      return await res.json();
    } catch {
      return {
        total_jobs: MOCK_JOBS.length,
        limit,
        offset,
        jobs: MOCK_JOBS,
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Get Background Job by ID
   */
  async getJob(jobId: string): Promise<JobResponse> {
    try {
      const res = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}`, {
        headers: this.getHeaders(),
      });

      if (!res.ok) throw new Error(`Get job failed: ${res.status}`);
      return await res.json();
    } catch {
      const found = MOCK_JOBS.find((j) => j.job_id === jobId);
      if (found) return found;
      throw new Error(`Job '${jobId}' not found.`);
    }
  }

  /**
   * Get Compliance Audit Trail
   */
  async getAuditTrail(limit = 50, offset = 0, eventType?: string): Promise<AuditTrailResponse> {
    try {
      const params = new URLSearchParams();
      params.set('limit', limit.toString());
      params.set('offset', offset.toString());
      if (eventType) params.set('event_type', eventType);

      const res = await fetch(`${BASE_URL}/admin/audit?${params.toString()}`, {
        headers: this.getHeaders(),
      });

      if (!res.ok) throw new Error(`Audit log retrieval failed: ${res.status}`);
      return await res.json();
    } catch {
      return {
        total_events_retained: MOCK_AUDIT_LOGS.length,
        limit,
        offset,
        events: MOCK_AUDIT_LOGS,
        timestamp: new Date().toISOString(),
      };
    }
  }

  /**
   * Get Service Level Objective (SLO) Health
   */
  async getSLOStatus(): Promise<SLOResponseSchema> {
    try {
      const res = await fetch(`${BASE_URL}/admin/slo`, {
        headers: this.getHeaders(),
      });
      if (!res.ok) throw new Error(`SLO evaluation failed: ${res.status}`);
      return await res.json();
    } catch {
      return MOCK_SLO_STATUS;
    }
  }

  /**
   * Get Configuration & Deployment Diagnostics
   */
  async getConfigDiagnostics(): Promise<ConfigDiagnosticsSchema> {
    try {
      const res = await fetch(`${BASE_URL}/admin/config`, {
        headers: this.getHeaders(),
      });
      if (!res.ok) throw new Error(`Config diagnostics failed: ${res.status}`);
      return await res.json();
    } catch {
      return MOCK_CONFIG_DIAGNOSTICS;
    }
  }
}

export const apiClient = new PayPilotApiClient();
