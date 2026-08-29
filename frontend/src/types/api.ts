/**
 * PayPilot Frontend TypeScript API Types & Models
 * Perfectly aligned with backend Pydantic models in backend/api/schemas.py
 */

export type CurrencyCode = 'INR' | 'USD' | 'GBP' | 'EUR' | 'JPY';

export interface CurrencyConfig {
  code: CurrencyCode;
  symbol: string;
  name: string;
  exchangeRateToINR: number; // 1 Base Currency = X INR (e.g. 1 USD = 86.5 INR)
  locale: string;
}

export interface HealthResponse {
  status: string;
  service: string;
  llm_provider: string;
  model: string;
  is_live_llm: boolean;
  timestamp: string;
}

export interface ReadinessResponse {
  status: string;
  service: string;
  checks: {
    dataset_accessible?: boolean;
    analytics_engine_ready?: boolean;
    llm_provider_initialized?: boolean;
    job_runner_ready?: boolean;
    accepting_traffic?: boolean;
    [key: string]: boolean | undefined;
  };
  details: {
    total_transactions_loaded?: number;
    active_llm_provider?: string;
    model?: string;
    is_live_llm?: boolean;
    runner_state?: string;
    [key: string]: any;
  };
  timestamp: string;
}

export interface PrioritizedActionItem {
  rank: number;
  action: string;
  problem: string;
  affected_area: string;
  estimated_revenue_impact_inr: number;
  observed_loss_inr: number;
  confidence: number;
  effort: 'Low' | 'Medium' | 'High' | string;
  urgency: 'Low' | 'Medium' | 'High' | string;
  priority_score: number;
  reasoning: string;
  metrics?: Record<string, any> | null;
}

export interface ExecutionMetadata {
  request_id: string;
  trace_id?: string | null;
  query: string;
  detected_intent: string;
  executed_agents: string[];
  execution_duration_ms: number;
  llm_provider: string;
  model: string;
  is_live_llm: boolean;
  success: boolean;
  timestamp: string;
}

export interface AnalyzeResponse {
  query: string;
  intent: string;
  agents_participated: string[];
  key_facts: Record<string, any>;
  revenue_leaks: string[];
  prioritized_actions: PrioritizedActionItem[];
  executive_recommendation: string;
  final_answer: string;
  estimated_recovery: Record<string, any>;
  llm_provider: string;
  model: string;
  is_live_llm: boolean;
  node_models?: Record<string, string> | null;
  execution_metadata: ExecutionMetadata;
}

export interface DeployRecommendationRequest {
  action_rank: number;
  action_title: string;
  affected_area?: string;
  estimated_revenue_impact_inr?: number;
  parameters?: Record<string, any>;
}

export interface DeployRecommendationResponse {
  deployment_id: string;
  job_id: string;
  action_rank: number;
  action_title: string;
  status: string;
  enqueued_at: string;
  client_id: string;
  role: string;
  estimated_revenue_impact_inr: number;
  message: string;
  timestamp: string;
}

export interface JobResponse {
  job_id: string;
  task_type: string;
  client_id: string;
  role: string;
  request_id?: string | null;
  trace_id?: string | null;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | string;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  query_summary?: string | null;
  result?: {
    intent?: string;
    executed_agents?: string[];
    priority_actions?: PrioritizedActionItem[];
    recovery_actions?: PrioritizedActionItem[];
    final_answer?: string;
    estimated_recovery?: Record<string, any>;
    key_facts?: Record<string, any>;
    [key: string]: any;
  } | null;
  error?: Record<string, any> | null;
}

export interface JobListResponse {
  total_jobs: number;
  limit: number;
  offset: number;
  jobs: JobResponse[];
  timestamp: string;
}

export interface AuditEventSchema {
  event_id: string;
  event_type: string;
  timestamp: string;
  request_id: string;
  endpoint: string;
  http_method: string;
  client_id: string;
  role: string;
  status: string;
  status_code: number;
  duration_ms?: number | null;
  intent?: string | null;
  executed_agents?: string[] | null;
  llm_provider?: string | null;
  model?: string | null;
  retry_count?: number | null;
  fallback_used?: boolean | null;
  error_category?: string | null;
  query_summary?: string | null;
}

export interface AuditTrailResponse {
  total_events_retained: number;
  total_events?: number | null;
  limit: number;
  offset: number;
  events: AuditEventSchema[];
  timestamp: string;
}

export interface SLOBreachSchema {
  slo_name: string;
  observed_value: number;
  target_value: number;
  unit: string;
  status: 'healthy' | 'warning' | 'breached' | string;
  severity: string;
  timestamp: string;
  details: Record<string, any>;
}

export interface SLOResponseSchema {
  overall_status: 'HEALTHY' | 'BREACHED' | string;
  total_slos_evaluated: number;
  active_breaches_count: number;
  new_alerts_emitted_count: number;
  evaluated_slos: SLOBreachSchema[];
  active_breaches: SLOBreachSchema[];
  /** May be empty array or absent — always guard before iteration */
  new_alerts_emitted?: SLOBreachSchema[] | null;
  /** May be null or absent from older backend versions */
  metrics_evaluated?: Record<string, number | null> | null;
  timestamp: string;
}

export interface ConfigDiagnosticsSchema {
  status: string;
  environment: string;
  llm_provider: string;
  model: string;
  database_backend: string;
  job_store: string;
  rate_limit_backend: string;
  tracing: string;
  secrets_status: Record<string, string>;
  snapshot: Record<string, any>;
  timestamp: string;
}

export interface TransactionRecord {
  transaction_id: string;
  timestamp: string;
  merchant_id: string;
  customer_id: string;
  amount: number; // in INR
  payment_method: 'UPI' | 'Credit_Card' | 'Debit_Card' | 'Net_Banking' | 'Wallet' | string;
  payment_status: 'SUCCESS' | 'FAILED' | 'PENDING' | 'REFUNDED' | string;
  failure_reason: string;
  device_type: 'Mobile' | 'Desktop' | 'Tablet' | string;
  customer_type: 'New' | 'Returning' | 'VIP' | string;
  product_category: 'Electronics' | 'Fashion' | 'Beauty' | 'Home' | 'Grocery' | string;
  refund_status: 'NO_REFUND' | 'REFUNDED' | 'PENDING' | string;
  checkout_step_reached: 'CART' | 'SHIPPING' | 'PAYMENT' | 'PAYMENT_COMPLETED' | string;
}
