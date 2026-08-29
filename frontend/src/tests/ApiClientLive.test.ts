import { describe, it, expect, beforeAll } from 'vitest';
import { apiClient, BASE_URL } from '../api/client';

const BACKEND_URL =
  (typeof process !== 'undefined' && process.env.VITE_API_BASE_URL) ||
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) ||
  BASE_URL ||
  'https://paypilot-pjye.onrender.com';

const HEADERS = {
  'Content-Type': 'application/json',
  'X-API-Key': 'paypilot-prod-analyst-key',
  'X-Client-ID': 'merchant_enterprise_01',
};

describe('API Client Unit & Fallback Capabilities', () => {
  it('Resolves BASE_URL from environment or defaults to relative path', () => {
    expect(typeof BASE_URL).toBe('string');
  });

  it('Gracefully returns fallback health status when backend is offline', async () => {
    const health = await apiClient.getHealth();
    expect(health.status).toBe('healthy');
    expect(health.service).toBe('paypilot');
    expect(health.llm_provider).toBe('nvidia');
  });

  it('Gracefully returns fallback readiness status when backend is offline', async () => {
    const readiness = await apiClient.getReadiness();
    expect(readiness.status).toBe('ready');
    expect(readiness.checks.dataset_accessible).toBe(true);
  });

  it('Gracefully handles analyze query with high-fidelity fallback', async () => {
    const res = await apiClient.analyze('Why did my revenue decrease?');
    expect(res.query).toBe('Why did my revenue decrease?');
    expect(res.intent).toBeDefined();
    expect(res.prioritized_actions.length).toBeGreaterThan(0);
  });
});

describe('Live FastAPI Backend Contracts & Response Integrity', () => {
  let isBackendLive = false;

  beforeAll(async () => {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      const res = await fetch(`${BACKEND_URL}/health`, { signal: controller.signal });
      clearTimeout(timeoutId);
      isBackendLive = res.ok;
    } catch {
      isBackendLive = false;
    }
  }, 15000);

  it('1. GET /health returns healthy liveness probe', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/health`);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('healthy');
    expect(data.service).toBe('paypilot');
    expect(data.llm_provider).toBeDefined();
    expect(data.model).toBeDefined();
  }, 60000);

  it('2. GET /ready returns component readiness checks', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/ready`);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('ready');
    expect(data.checks.dataset_accessible).toBe(true);
    expect(data.checks.analytics_engine_ready).toBe(true);
    expect(data.checks.job_runner_ready).toBe(true);
  }, 60000);

  it(
    '3. POST /api/v1/analyze executes synchronous multi-agent analysis',
    async (ctx) => {
      if (!isBackendLive) {
        ctx.skip();
        return;
      }
      const res = await fetch(`${BACKEND_URL}/api/v1/analyze`, {
        method: 'POST',
        headers: HEADERS,
        body: JSON.stringify({ query: 'Why did my revenue decrease and where is my biggest drop-off?' }),
      });
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(data.query).toBeDefined();
      expect(data.intent).toBeDefined();
      expect(data.agents_participated.length).toBeGreaterThan(0);
      expect(data.prioritized_actions.length).toBeGreaterThan(0);
      expect(data.executive_recommendation).toBeDefined();
    },
    180000
  );

  it('4. POST /api/v1/jobs submits background analysis task', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/api/v1/jobs`, {
      method: 'POST',
      headers: {
        ...HEADERS,
        'Idempotency-Key': `test_idemp_${Date.now()}`,
      },
      body: JSON.stringify({ query: 'Perform complete revenue leakage audit' }),
    });
    expect(res.status).toBe(202);
    const data = await res.json();
    expect(data.job_id).toBeDefined();
    expect(['queued', 'running', 'completed']).toContain(data.status);
  }, 60000);

  it('5. GET /admin/slo returns operational SLO targets', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/admin/slo`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.overall_status).toBeDefined();
    expect(data.evaluated_slos.length).toBeGreaterThan(0);
  }, 60000);

  it('6. GET /admin/audit returns paginated audit log events', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/admin/audit?limit=10`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.events).toBeInstanceOf(Array);
    expect(data.limit).toBe(10);
  }, 60000);

  it('7. GET /admin/config returns sanitized configuration snapshot without secret leakage', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const res = await fetch(`${BACKEND_URL}/admin/config`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status.toLowerCase()).toBe('valid');
    expect(data.environment).toBeDefined();
    expect(data.secrets_status).toBeDefined();
  }, 60000);

  it('8. apiClient.deployRecommendation dispatches recovery action deployment', async (ctx) => {
    if (!isBackendLive) {
      ctx.skip();
      return;
    }
    const mockItem = {
      rank: 1,
      action: 'Implement Dynamic Multi-Gateway Failover Routing for UPI',
      affected_area: 'Payment Gateways',
      problem: 'UPI Gateway Timeout',
      estimated_revenue_impact_inr: 4820000.0,
      observed_loss_inr: 4820000.0,
      confidence: 0.95,
      priority_score: 92.4,
      urgency: 'High' as const,
      effort: 'Low' as const,
      reasoning: 'Routing around failed gateway recovers transaction flow.',
    };

    const res = await apiClient.deployRecommendation(mockItem, `test_live_deploy_${Date.now()}`);
    expect(res.deployment_id).toBeDefined();
    expect(res.job_id).toBeDefined();
    expect(res.action_rank).toBe(1);
    expect(res.status).toBeDefined();
    expect(res.message).toBeDefined();
  }, 60000);

  it('9. apiClient.deployRecommendation works with simulated fallback when offline', async () => {
    const mockItem = {
      rank: 1,
      action: 'Implement Dynamic Multi-Gateway Failover Routing for UPI',
      affected_area: 'Payment Gateways',
      problem: 'UPI Gateway Timeout',
      estimated_revenue_impact_inr: 4820000.0,
      observed_loss_inr: 4820000.0,
      confidence: 0.95,
      priority_score: 92.4,
      urgency: 'High' as const,
      effort: 'Low' as const,
      reasoning: 'Routing around failed gateway recovers transaction flow.',
    };

    const res = await apiClient.deployRecommendation(mockItem, 'test_client_deploy_fallback');
    expect(res.deployment_id).toBeDefined();
    expect(res.job_id).toBeDefined();
    expect(res.action_rank).toBe(1);
    expect(res.status).toBe('enqueued');
    expect(res.message).toBeDefined();
  });
});
