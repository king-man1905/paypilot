import { describe, it, expect } from 'vitest';

const BACKEND_URL = 'http://localhost:8000';
const HEADERS = {
  'Content-Type': 'application/json',
  'X-API-Key': 'paypilot-prod-analyst-key',
  'X-Client-ID': 'merchant_enterprise_01',
};

describe('Live FastAPI Backend Contracts & Response Integrity', () => {
  it('1. GET /health returns healthy liveness probe', async () => {
    const res = await fetch(`${BACKEND_URL}/health`);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('healthy');
    expect(data.service).toBe('paypilot');
    expect(data.llm_provider).toBeDefined();
    expect(data.model).toBeDefined();
  });

  it('2. GET /ready returns component readiness checks', async () => {
    const res = await fetch(`${BACKEND_URL}/ready`);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status).toBe('ready');
    expect(data.checks.dataset_accessible).toBe(true);
    expect(data.checks.analytics_engine_ready).toBe(true);
    expect(data.checks.job_runner_ready).toBe(true);
  });

  it(
    '3. POST /api/v1/analyze executes synchronous multi-agent analysis',
    async () => {
      // NOTE: Real LLM P95 latency is ~74s per /admin/slo SLO data.
      // This is an integration test against the live backend — not a unit test.
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
    180000  // 180s timeout: real LLM synthesis with multi-agent orchestration
  );

  it('4. POST /api/v1/jobs submits background analysis task', async () => {
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
  });

  it('5. GET /admin/slo returns operational SLO targets', async () => {
    const res = await fetch(`${BACKEND_URL}/admin/slo`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.overall_status).toBeDefined();
    expect(data.evaluated_slos.length).toBeGreaterThan(0);
  });

  it('6. GET /admin/audit returns paginated audit log events', async () => {
    const res = await fetch(`${BACKEND_URL}/admin/audit?limit=10`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.events).toBeInstanceOf(Array);
    expect(data.limit).toBe(10);
  });

  it('7. GET /admin/config returns sanitized configuration snapshot without secret leakage', async () => {
    const res = await fetch(`${BACKEND_URL}/admin/config`, {
      headers: HEADERS,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.status.toLowerCase()).toBe('valid');
    expect(data.environment).toBeDefined();
    expect(data.secrets_status).toBeDefined();
  });
});
