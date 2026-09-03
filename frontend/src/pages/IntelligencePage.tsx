import React, { useState } from 'react';
import {
  Bot,
  ArrowRight,
  Terminal,
  Zap,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { FormattedCurrency } from '../components/common/FormattedCurrency';
import { apiClient } from '../api/client';
import { AnalyzeResponse, JobResponse, PrioritizedActionItem } from '../types/api';
import { MOCK_JOBS } from '../api/mockData';

export const IntelligencePage: React.FC = () => {
  const [query, setQuery] = useState('Why did my revenue decrease and where is my biggest drop-off?');
  const [executionMode, setExecutionMode] = useState<'sync' | 'async'>('sync');
  const [isLoading, setIsLoading] = useState(false);
  // Intentionally starts empty — no fake analysis is ever pre-seeded. A real result only
  // appears here after a successful backend response (see handleRunAnalysis).
  const [analysisResult, setAnalysisResult] = useState<AnalyzeResponse | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobResponse[]>(MOCK_JOBS);
  const [activeEvidenceTab, setActiveEvidenceTab] = useState<'revenue' | 'payment' | 'checkout' | 'customer'>('revenue');
  const [deployingRank, setDeployingRank] = useState<number | null>(null);
  const [deployedRanks, setDeployedRanks] = useState<Record<number, { status: 'success' | 'error'; jobId?: string; message?: string }>>({});
  const [deployMessage, setDeployMessage] = useState<{ type: 'success' | 'error'; text: string; rank: number } | null>(null);

  const sampleQueries = [
    'Why did my revenue decrease and where is my biggest drop-off?',
    'Analyze UPI payment timeouts and suggest recovery actions.',
    'Why is mobile checkout conversion lagging desktop?',
    'What product categories have the highest refund rates?',
  ];

  const handleRunAnalysis = async () => {
    if (!query.trim()) return;
    setIsLoading(true);
    setAnalysisError(null);

    try {
      if (executionMode === 'sync') {
        const result = await apiClient.analyze(query);
        setAnalysisResult(result);
      } else {
        const idempotencyKey = `idemp_${Date.now()}_${Math.random().toString(36).substring(7)}`;
        const job = await apiClient.submitJob(query, idempotencyKey);
        setJobs((prev) => [job, ...prev]);
        if (job.result) {
          setAnalysisResult(job.result as AnalyzeResponse);
        }
      }
    } catch (err: any) {
      console.error('Error running analysis:', err);
      // Surface the failure visibly — never silently keep showing stale or fake data as if
      // this request succeeded.
      setAnalysisError(
        err?.message || 'Unable to reach the PayPilot backend. The analysis could not be completed.'
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeployRecommendation = async (item: PrioritizedActionItem) => {
    setDeployingRank(item.rank);
    setDeployMessage(null);
    try {
      const idempotencyKey = `idemp_deploy_${item.rank}_${Date.now()}_${Math.random().toString(36).substring(7)}`;
      const deployRes = await apiClient.deployRecommendation(item, idempotencyKey);

      // Create job representation for the background job queue ledger
      const job: JobResponse = {
        job_id: deployRes.job_id,
        task_type: 'action_deployment',
        client_id: deployRes.client_id,
        role: deployRes.role,
        status: deployRes.status,
        created_at: deployRes.enqueued_at,
        query_summary: `P${deployRes.action_rank}: ${deployRes.action_title}`,
        result: {
          deployment_id: deployRes.deployment_id,
          action_rank: deployRes.action_rank,
          action_title: deployRes.action_title,
          estimated_revenue_impact_inr: deployRes.estimated_revenue_impact_inr,
        },
      };

      setJobs((prev) => [job, ...prev]);
      setDeployedRanks((prev) => ({
        ...prev,
        [item.rank]: {
          status: 'success',
          jobId: deployRes.job_id,
          message: deployRes.message,
        },
      }));
      setDeployMessage({
        type: 'success',
        text: deployRes.message,
        rank: item.rank,
      });
    } catch (err: any) {
      console.error('Error deploying recommendation:', err);
      setDeployedRanks((prev) => ({
        ...prev,
        [item.rank]: {
          status: 'error',
          message: err?.message || 'Deployment dispatch failed.',
        },
      }));
      setDeployMessage({
        type: 'error',
        text: `Failed to deploy P${item.rank}: ${err?.message || 'Network or server error.'}`,
        rank: item.rank,
      });
    } finally {
      setDeployingRank(null);
    }
  };

  return (
    <div className="space-y-8 animate-fadeIn">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
              AI Intelligence Center
            </h2>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-emerald-100 text-emerald-800 border border-emerald-300">
              LangGraph Multi-Agent Studio
            </span>
          </div>
          <p className="text-xs sm:text-sm font-medium text-slate-500">
            Autonomous multi-agent orchestration synthesizing revenue recovery action plans.
          </p>
        </div>
        <div className="flex items-center gap-2 bg-slate-100 p-1 rounded-xl border border-slate-200 text-xs font-semibold">
          <button
            onClick={() => setExecutionMode('sync')}
            className={`px-3 py-1.5 rounded-lg transition-all ${
              executionMode === 'sync'
                ? 'bg-white text-slate-900 shadow-xs font-bold'
                : 'text-slate-500 hover:text-slate-900'
            }`}
          >
            Real-Time Sync
          </button>
          <button
            onClick={() => setExecutionMode('async')}
            className={`px-3 py-1.5 rounded-lg transition-all ${
              executionMode === 'async'
                ? 'bg-white text-slate-900 shadow-xs font-bold'
                : 'text-slate-500 hover:text-slate-900'
            }`}
          >
            Async Job Queue
          </button>
        </div>
      </div>

      {/* Query Terminal Card */}
      <Card className="p-6 bg-gradient-to-b from-white to-slate-50/50 border-slate-300/80 shadow-premium-lg">
        <div className="flex items-center gap-2 mb-3 text-xs font-bold text-slate-700">
          <Terminal className="w-4 h-4 text-primary" />
          <span>Merchant Diagnostic Inquiry Terminal</span>
        </div>

        <div className="space-y-3">
          <div className="relative">
            <textarea
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask a diagnostic query (e.g. 'Why did revenue drop this month and how can I recover lost sales?')..."
              className="w-full bg-white border border-slate-300 rounded-xl p-4 text-sm font-medium text-slate-900 placeholder-slate-400 focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all resize-none"
            />
            <div className="absolute right-3 bottom-3">
              <Button
                variant="primary"
                size="md"
                onClick={handleRunAnalysis}
                isLoading={isLoading}
                leftIcon={<Zap className="w-4 h-4" />}
              >
                {executionMode === 'sync' ? 'Execute Multi-Agent Pipeline' : 'Enqueue Background Task'}
              </Button>
            </div>
          </div>

          {/* Quick Query Presets */}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Presets:</span>
            {sampleQueries.map((q, idx) => (
              <button
                key={idx}
                onClick={() => setQuery(q)}
                className="text-xs bg-white hover:bg-slate-100 text-slate-600 hover:text-slate-900 border border-slate-200 px-2.5 py-1 rounded-lg transition-colors truncate max-w-xs text-left"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* Analysis Error State — visible, not silently masked with fake data */}
      {analysisError && (
        <div className="p-4 rounded-xl border border-red-200 bg-red-50 text-red-900 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold">Analysis failed</p>
            <p className="text-xs mt-0.5">{analysisError}</p>
          </div>
        </div>
      )}

      {/* Empty State — no query has been run yet, no fake analysis is shown */}
      {!analysisResult && !isLoading && !analysisError && (
        <Card className="p-10 text-center border-dashed">
          <Bot className="w-8 h-8 text-slate-300 mx-auto mb-3" />
          <p className="text-sm font-semibold text-slate-600">No analysis yet</p>
          <p className="text-xs text-slate-400 mt-1">
            Run a diagnostic query above to execute the multi-agent pipeline against live merchant data.
          </p>
        </Card>
      )}

      {/* Analysis Results Display */}
      {analysisResult && (
        <div className="space-y-6">
          {/* Executive Strategic Synthesis Card */}
          <div className="bg-slate-900 text-white rounded-2xl p-6 shadow-2xl border border-slate-800 relative overflow-hidden">
            <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-4 border-b border-slate-800 pb-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center justify-center">
                  <Bot className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">Executive Synthesis & Decision Brief</h3>
                  <p className="text-xs text-slate-400">
                    Intent: <span className="text-emerald-400 font-mono font-bold">{analysisResult.intent}</span> | Model: <span className="font-mono text-slate-300">{analysisResult.model}</span>
                  </p>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-slate-400 font-semibold">Participating Agents:</span>
                {analysisResult.agents_participated.map((agent) => (
                  <span
                    key={agent}
                    className="px-2 py-0.5 rounded-md bg-slate-800 text-slate-300 font-mono text-[11px] border border-slate-700"
                  >
                    {agent.replace('_', ' ')}
                  </span>
                ))}
              </div>
            </div>

            {/* Executive Recommendation Quote */}
            <div className="bg-slate-800/80 rounded-xl p-4 border border-slate-700/80 mb-4">
              <span className="text-[10px] font-extrabold uppercase tracking-wider text-emerald-400 block mb-1">
                Decisive Action Recommendation
              </span>
              <p className="text-sm font-semibold text-slate-100 leading-relaxed">
                {analysisResult.executive_recommendation}
              </p>
            </div>

            {/* Metric Facts Summary Strip — real values only, honest "Unavailable" when absent */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2">
              <div className="bg-slate-800/40 rounded-xl p-3 border border-slate-700/50">
                <span className="text-[11px] text-slate-400 block font-medium">Estimated Recoverable Pool</span>
                {(() => {
                  const rec = analysisResult.estimated_recovery?.estimated_recovery_from_prioritized_actions_inr
                    ?? analysisResult.estimated_recovery?.total_estimated_recoverable_inr;
                  return rec != null ? (
                    <span className="text-lg font-extrabold text-emerald-400 font-mono">
                      <FormattedCurrency amountInINR={rec} />
                    </span>
                  ) : (
                    <span className="text-lg font-extrabold text-slate-500">Unavailable</span>
                  );
                })()}
              </div>
              <div className="bg-slate-800/40 rounded-xl p-3 border border-slate-700/50">
                <span className="text-[11px] text-slate-400 block font-medium">Execution Pipeline Duration</span>
                <span className="text-lg font-extrabold text-white font-mono">
                  {analysisResult.execution_metadata?.execution_duration_ms != null
                    ? `${analysisResult.execution_metadata.execution_duration_ms} ms`
                    : 'Unavailable'}
                </span>
              </div>
              <div className="bg-slate-800/40 rounded-xl p-3 border border-slate-700/50">
                <span className="text-[11px] text-slate-400 block font-medium">Primary Failure Factor</span>
                {(() => {
                  const worst = analysisResult.key_facts?.highest_failure_method;
                  return worst?.method ? (
                    <span className="text-sm font-extrabold text-amber-400">
                      {worst.method} ({worst.failure_rate_pct}%)
                    </span>
                  ) : (
                    <span className="text-sm font-extrabold text-slate-500">Unavailable</span>
                  );
                })()}
              </div>
            </div>
          </div>

          {/* Ranked Prioritized Actions (P1, P2, P3...) */}
          <div>
            <div className="flex justify-between items-center mb-4">
              <div>
                <h3 className="text-lg font-extrabold text-slate-900 tracking-tight">
                  Ranked Revenue Recovery Actions
                </h3>
                <p className="text-xs text-slate-500">
                  Deterministic ranking based on estimated impact, observed loss, confidence, and implementation effort.
                </p>
              </div>
              <Badge variant="primary" size="md">
                {analysisResult.prioritized_actions?.length || 3} Actions Ranked
              </Badge>
            </div>

            {/* Deployment Feedback Banner */}
            {deployMessage && (
              <div
                className={`mb-4 p-3.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all ${
                  deployMessage.type === 'success'
                    ? 'bg-emerald-50 text-emerald-900 border-emerald-200 shadow-xs'
                    : 'bg-red-50 text-red-900 border-red-200 shadow-xs'
                }`}
              >
                <div className="flex items-center gap-2">
                  {deployMessage.type === 'success' ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
                  ) : (
                    <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
                  )}
                  <span>{deployMessage.text}</span>
                </div>
                <button
                  onClick={() => setDeployMessage(null)}
                  className="text-slate-400 hover:text-slate-700 text-xs px-1.5 py-0.5 rounded-md"
                  aria-label="Dismiss notification"
                >
                  ✕
                </button>
              </div>
            )}

            <div className="space-y-4">
              {analysisResult.prioritized_actions?.map((item: PrioritizedActionItem) => {
                const deployedStatus = deployedRanks[item.rank];
                const isDeploying = deployingRank === item.rank;
                const isDeployed = deployedStatus?.status === 'success';
                const hasDeployError = deployedStatus?.status === 'error';

                return (
                  <Card
                    key={item.rank}
                    className="p-6 border-l-4 border-l-primary hover:shadow-premium-lg transition-all"
                  >
                    <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-4 mb-3">
                      <div className="flex items-center gap-3">
                        <span className="w-8 h-8 rounded-xl bg-primary text-white flex items-center justify-center font-extrabold text-sm shadow-xs">
                          P{item.rank}
                        </span>
                        <div>
                          <h4 className="text-base font-bold text-slate-900">{item.action}</h4>
                          <p className="text-xs text-slate-500 font-medium">{item.affected_area}</p>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="neutral" size="sm">
                          Effort: <strong className="ml-1 text-slate-900">{item.effort}</strong>
                        </Badge>
                        <Badge variant="error" size="sm">
                          Urgency: <strong className="ml-1">{item.urgency}</strong>
                        </Badge>
                        <Badge variant="success" size="sm">
                          Score: <strong className="ml-1">{item.priority_score.toFixed(1)}/100</strong>
                        </Badge>
                      </div>
                    </div>

                    {/* Problem & Impact Details */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 my-4 p-4 bg-slate-50 rounded-xl border border-slate-100 text-xs">
                      <div>
                        <span className="text-slate-400 font-bold uppercase block mb-1">Diagnosed Friction</span>
                        <p className="text-slate-800 font-medium">{item.problem}</p>
                      </div>
                      <div>
                        <span className="text-slate-400 font-bold uppercase block mb-1">Estimated Recoverable GTV</span>
                        <p className="text-base font-extrabold text-emerald-700 font-mono">
                          <FormattedCurrency amountInINR={item.estimated_revenue_impact_inr} />
                        </p>
                        <span className="text-[10px] text-slate-400">
                          Observed Loss: <FormattedCurrency amountInINR={item.observed_loss_inr} />
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-400 font-bold uppercase block mb-1">Analytical Reasoning</span>
                        <p className="text-slate-700 leading-relaxed">{item.reasoning}</p>
                      </div>
                    </div>

                    {/* Action CTA */}
                    <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 pt-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-400 font-mono">
                          Confidence: {(item.confidence * 100).toFixed(0)}%
                        </span>
                        {isDeployed && (
                          <span className="inline-flex items-center gap-1 text-[11px] font-bold text-emerald-700 bg-emerald-50 px-2.5 py-0.5 rounded-full border border-emerald-200">
                            <CheckCircle2 className="w-3 h-3 text-emerald-600" />
                            Deployed ({deployedStatus?.jobId})
                          </span>
                        )}
                        {hasDeployError && (
                          <span className="inline-flex items-center gap-1 text-[11px] font-bold text-red-700 bg-red-50 px-2.5 py-0.5 rounded-full border border-red-200">
                            <AlertCircle className="w-3 h-3 text-red-600" />
                            Deployment Failed
                          </span>
                        )}
                      </div>
                      <Button
                        variant={isDeployed ? 'secondary' : 'primary'}
                        size="sm"
                        onClick={() => handleDeployRecommendation(item)}
                        isLoading={isDeploying}
                        leftIcon={
                          isDeployed ? (
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
                          ) : undefined
                        }
                        rightIcon={
                          isDeployed ? undefined : (
                            <ArrowRight className="w-3.5 h-3.5" />
                          )
                        }
                        className={
                          isDeployed
                            ? 'bg-emerald-50 text-emerald-800 border-emerald-300 hover:bg-emerald-100'
                            : ''
                        }
                      >
                        {isDeployed
                          ? 'Recommendation Deployed'
                          : hasDeployError
                          ? 'Retry Deployment'
                          : 'Deploy Recommendation'}
                      </Button>
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>

          {/* Specialist Agent Evidence Tabs */}
          <Card className="p-6">
            <h3 className="text-base font-bold text-slate-900 mb-1">Deterministic Evidence Ledger</h3>
            <p className="text-xs text-slate-500 mb-4">
              Verifiable facts computed by specialist analysis agents without LLM hallucinations.
            </p>

            <div className="flex items-center gap-2 border-b border-slate-200 pb-3 mb-4">
              {[
                { key: 'revenue', label: 'Revenue Agent' },
                { key: 'payment', label: 'Payment Gateway Agent' },
                { key: 'checkout', label: 'Checkout Funnel Agent' },
                { key: 'customer', label: 'Customer & Refund Agent' },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveEvidenceTab(tab.key as any)}
                  className={`text-xs px-3.5 py-1.5 rounded-lg font-semibold transition-colors ${
                    activeEvidenceTab === tab.key
                      ? 'bg-primary text-white shadow-xs'
                      : 'text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Tab Contents — sourced only from analysisResult; shows "Unavailable" rather than
                inventing a number when the API response does not carry that field. */}
            <div className="text-xs space-y-3">
              {activeEvidenceTab === 'revenue' && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="text-slate-400 block font-medium">Total Realized Revenue</span>
                    <span className="text-sm font-bold text-slate-900">
                      {analysisResult.key_facts?.total_revenue_inr != null ? (
                        <FormattedCurrency amountInINR={analysisResult.key_facts.total_revenue_inr} />
                      ) : (
                        'Unavailable'
                      )}
                    </span>
                  </div>
                  <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                    <span className="text-slate-400 block font-medium">Identified Recoverable Opportunity</span>
                    <span className="text-sm font-bold text-slate-900">
                      {analysisResult.key_facts?.identified_recoverable_opportunity_inr != null ? (
                        <FormattedCurrency amountInINR={analysisResult.key_facts.identified_recoverable_opportunity_inr} />
                      ) : (
                        'Unavailable'
                      )}
                    </span>
                    <span className="text-[10px] text-slate-400 block mt-0.5">Estimated, not confirmed recovered revenue</span>
                  </div>
                </div>
              )}

              {activeEvidenceTab === 'payment' && (
                <div className="p-4 bg-slate-50 rounded-xl border border-slate-200">
                  <h4 className="font-bold text-slate-800 mb-2">Worst Performing Payment Method</h4>
                  {analysisResult.key_facts?.highest_failure_method?.method ? (
                    <p className="text-slate-600 leading-relaxed">
                      <strong>{analysisResult.key_facts.highest_failure_method.method}</strong> has the
                      highest failure rate at <strong>{analysisResult.key_facts.highest_failure_method.failure_rate_pct}%</strong>,
                      with an overall payment success rate of{' '}
                      {analysisResult.key_facts?.payment_success_rate_pct != null
                        ? `${analysisResult.key_facts.payment_success_rate_pct}%`
                        : 'an unavailable rate'}.
                    </p>
                  ) : (
                    <p className="text-slate-400">Unavailable — no payment evidence returned for this query.</p>
                  )}
                </div>
              )}

              {activeEvidenceTab === 'checkout' && (
                <div className="p-4 bg-slate-50 rounded-xl border border-slate-200">
                  <h4 className="font-bold text-slate-800 mb-2">Device Conversion Differential</h4>
                  {analysisResult.key_facts?.mobile_conversion_rate_pct != null &&
                  analysisResult.key_facts?.desktop_conversion_rate_pct != null ? (
                    <p className="text-slate-600 leading-relaxed">
                      Mobile conversion rate is <strong>{analysisResult.key_facts.mobile_conversion_rate_pct}%</strong> compared
                      to Desktop conversion of <strong>{analysisResult.key_facts.desktop_conversion_rate_pct}%</strong> (
                      {(analysisResult.key_facts.desktop_conversion_rate_pct - analysisResult.key_facts.mobile_conversion_rate_pct).toFixed(2)}
                      {' '}point gap).
                    </p>
                  ) : (
                    <p className="text-slate-400">Unavailable — no checkout evidence returned for this query.</p>
                  )}
                </div>
              )}

              {activeEvidenceTab === 'customer' && (
                <div className="p-4 bg-slate-50 rounded-xl border border-slate-200">
                  <h4 className="font-bold text-slate-800 mb-2">Category Return Rate Anomaly</h4>
                  {analysisResult.key_facts?.highest_refund_category?.category ? (
                    <p className="text-slate-600 leading-relaxed">
                      <strong>{analysisResult.key_facts.highest_refund_category.category}</strong> exhibits a{' '}
                      <strong>{analysisResult.key_facts.highest_refund_category.refund_rate_pct}%</strong> refund rate
                      {analysisResult.key_facts.highest_refund_category.refunded_orders_count != null && (
                        <> across {analysisResult.key_facts.highest_refund_category.refunded_orders_count} refunded orders</>
                      )}.
                    </p>
                  ) : (
                    <p className="text-slate-400">Unavailable — no customer/refund evidence returned for this query.</p>
                  )}
                </div>
              )}
            </div>
          </Card>
        </div>
      )}

      {/* Background Jobs Execution Ledger */}
      <Card className="p-6">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h3 className="text-base font-bold text-slate-900">Background Job Execution Queue</h3>
            <p className="text-xs text-slate-500">Asynchronous diagnostic tasks enqueued across worker replicas</p>
          </div>
          <Badge variant="neutral" size="sm">{jobs.length} Jobs Total</Badge>
        </div>

        <div className="overflow-x-auto -mx-6 px-6">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-400 font-bold uppercase tracking-wider">
                <th className="pb-3">Job ID</th>
                <th className="pb-3">Task Query</th>
                <th className="pb-3">Status</th>
                <th className="pb-3">Duration</th>
                <th className="pb-3">Created At</th>
                <th className="pb-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {jobs.map((job) => (
                <tr key={job.job_id} className="hover:bg-slate-50/80">
                  <td className="py-3 font-mono font-bold text-slate-900">{job.job_id}</td>
                  <td className="py-3 max-w-xs truncate">{job.query_summary || 'Diagnostic inquiry'}</td>
                  <td className="py-3">
                    <Badge
                      variant={
                        job.status === 'completed'
                          ? 'success'
                          : job.status === 'running'
                          ? 'primary'
                          : 'neutral'
                      }
                      size="sm"
                    >
                      {job.status.toUpperCase()}
                    </Badge>
                  </td>
                  <td className="py-3 font-mono text-slate-500">
                    {job.duration_ms ? `${job.duration_ms.toFixed(1)} ms` : 'In Progress...'}
                  </td>
                  <td className="py-3 text-slate-400 font-mono text-[11px]">{job.created_at.slice(0, 19)}</td>
                  <td className="py-3 text-right">
                    {job.result && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setAnalysisResult(job.result as AnalyzeResponse)}
                      >
                        Load Report
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};
