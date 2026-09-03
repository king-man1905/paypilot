import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { apiClient, BASE_URL, resetSessionToken } from '../api/client';

const BACKEND_URL =
  (typeof process !== 'undefined' && process.env.VITE_API_BASE_URL) ||
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_BASE_URL) ||
  BASE_URL ||
  'https://paypilot-pjye.onrender.com';

// No hardcoded key: the old committed value is permanently compromised. A real key is only
// ever supplied at test-run time via env var — never baked into source. Tests that require
// authentication skip gracefully when it isn't provided (see canTestAuthenticated below).
const TEST_API_KEY =
  (typeof process !== 'undefined' && process.env.PAYPILOT_TEST_API_KEY) ||
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_TEST_API_KEY) ||
  '';

const HEADERS = {
  'Content-Type': 'application/json',
  'X-API-Key': TEST_API_KEY,
  'X-Client-ID': 'merchant_enterprise_01',
};

describe('API Client Unit & Fallback Capabilities', () => {
  beforeEach(() => {
    // Reset session token state between tests to ensure isolation
    resetSessionToken();
    localStorage.removeItem('paypilot_api_key');
  });

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

  it('Throws a visible error from analyze() when the backend is unreachable — must NOT silently return fake mock data', async () => {
    // Regression test for the removed silent-mock-fallback anti-pattern: a failed/unreachable
    // backend must surface as a rejected promise, never as a fabricated "successful" analysis.
    await expect(apiClient.analyze('Why did my revenue decrease?')).rejects.toThrow();
  });

  it('Sends whatever key is configured in localStorage as X-API-Key — no hardcoded fallback', async () => {
    // Regression test for the removed hardcoded key fallback: the header must reflect exactly
    // what the user configured (via Settings), never a value baked into source.
    const configuredKey = 'test-configured-key-abc123';
    localStorage.setItem('paypilot_api_key', configuredKey);

    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    try {
      await apiClient.analyze('test query').catch(() => {});
      const call = fetchSpy.mock.calls.find(([url]) => String(url).includes('/api/v1/analyze'));
      expect(call).toBeDefined();
      const [, init] = call!;
      const headers = init?.headers as Record<string, string>;
      expect(headers['X-API-Key']).toBe(configuredKey);
    } finally {
      fetchSpy.mockRestore();
      localStorage.removeItem('paypilot_api_key');
    }
  });

  it('Does not send a hardcoded credential when no key is configured — uses session token or empty auth', async () => {
    // When no manual key is set, the client tries to acquire a session token from the backend.
    // In the test environment (backend offline), session token acquisition fails silently,
    // so no auth header is sent. The key property: no hardcoded key ever appears.
    localStorage.removeItem('paypilot_api_key');
    resetSessionToken();

    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    try {
      await apiClient.analyze('test query').catch(() => {});
      const call = fetchSpy.mock.calls.find(([url]) => String(url).includes('/api/v1/analyze'));
      expect(call).toBeDefined();
      const [, init] = call!;
      const headers = init?.headers as Record<string, string>;
      // Must NOT contain any hardcoded API key
      expect(headers['X-API-Key']).toBeUndefined();
      expect(JSON.stringify(headers)).not.toContain('paypilot-prod');
    } finally {
      fetchSpy.mockRestore();
    }
  });
});

describe('Live FastAPI Backend Contracts & Response Integrity', () => {
  let isBackendLive = false;
  // Authenticated endpoints require a real key supplied via env var (see TEST_API_KEY above) —
  // without one, this suite can prove the backend is reachable but not exercise auth'd routes.
  const canTestAuthenticated = () => isBackendLive && !!TEST_API_KEY;

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
      if (!canTestAuthenticated()) {
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
    if (!canTestAuthenticated()) {
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
    if (!canTestAuthenticated()) {
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
    if (!canTestAuthenticated()) {
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
    if (!canTestAuthenticated()) {
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
    // apiClient resolves its own BASE_URL to a relative path in this jsdom harness, and
    // relative fetches are always intercepted by the global test fetch mock (tests/setup.ts) —
    // so apiClient itself can never reach the real backend from inside this suite, even though
    // isBackendLive (a separate absolute-URL probe) may be true. Skip rather than assert against
    // the mock's fixed response. (Previously masked by deployRecommendation's removed silent
    // mock-fallback, which made this test's assertions pass vacuously regardless.)
    if (!isBackendLive || !BASE_URL) {
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

  it('9. apiClient.deployRecommendation throws when the backend is unreachable — must NOT silently report a fake "enqueued" deployment', async () => {
    // Regression test: reporting a deployment as "enqueued" when it never reached the backend
    // would mislead a merchant into believing an action was queued when nothing happened.
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

    await expect(
      apiClient.deployRecommendation(mockItem, 'test_client_deploy_fallback')
    ).rejects.toThrow();
  });
});
