/**
 * Audit & Security Center
 *
 * DATA SOURCES — all live from backend:
 *   SLO status:    /admin/slo  — via useSLO() shared context (no duplicate fetch)
 *   Audit events:  /admin/audit — fetched locally on mount
 *
 * NEVER shows hardcoded HEALTHY/BREACHED status.
 * NEVER shows fake event IDs, fake latency, or fabricated retention counts.
 * If backend is BREACHED, the UI explicitly shows BREACHED.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  CheckCircle2,
  Search,
  Download,
  Eye,
  AlertCircle,
  RefreshCw,
  XCircle,
  Loader2,
} from 'lucide-react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { Drawer } from '../components/common/Drawer';
import { LoadingSkeleton } from '../components/common/LoadingSkeleton';
import { AuditEventSchema, AuditTrailResponse } from '../types/api';
import { apiClient } from '../api/client';
import { useSLO } from '../context/SLOContext';

export const AuditSecurityPage: React.FC = () => {
  // ── SLO from shared context (no duplicate /admin/slo request) ────────────
  const { sloData, sloLoading, sloError, refetchSLO, isHealthy, breachCount } = useSLO();

  // ── Audit trail — fetched independently ──────────────────────────────────
  const [auditData, setAuditData] = useState<AuditTrailResponse | null>(null);
  const [auditLoading, setAuditLoading] = useState(true);
  const [auditError, setAuditError] = useState<string | null>(null);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [selectedEvent, setSelectedEvent] = useState<AuditEventSchema | null>(null);
  const [eventTypeFilter, setEventTypeFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [isAuditRefreshing, setIsAuditRefreshing] = useState(false);

  const fetchAudit = useCallback(async (silent = false) => {
    if (!silent) setAuditLoading(true);
    else setIsAuditRefreshing(true);
    setAuditError(null);
    try {
      const data = await apiClient.getAuditTrail(50, 0);
      setAuditData(data);
    } catch (err: any) {
      setAuditError(err?.message || 'Failed to load audit events from /admin/audit');
      setAuditData(null);
    } finally {
      setAuditLoading(false);
      setIsAuditRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAudit();
  }, [fetchAudit]);

  // ── Filtering ─────────────────────────────────────────────────────────────
  const filteredEvents = (auditData?.events ?? []).filter((e) => {
    if (eventTypeFilter !== 'ALL' && e.event_type !== eventTypeFilter) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      return (
        e.event_id.toLowerCase().includes(q) ||
        e.request_id.toLowerCase().includes(q) ||
        e.endpoint.toLowerCase().includes(q) ||
        e.event_type.toLowerCase().includes(q)
      );
    }
    return true;
  });

  // ── SLO metric helpers ────────────────────────────────────────────────────
  const totalSLOs = sloData?.total_slos_evaluated ?? 0;
  const p95SLO = sloData?.evaluated_slos?.find((s) => s.slo_name.includes('latency'));
  const errorRateSLO = sloData?.evaluated_slos?.find(
    (s) => s.slo_name.includes('error_rate') || s.slo_name.includes('api_error')
  );

  return (
    <div className="space-y-8 animate-fadeIn">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
            Audit &amp; Security Center
          </h2>
          <p className="text-xs sm:text-sm font-medium text-slate-500 mt-1">
            Immutable compliance event ledger · live SLO verification · distributed execution traces.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              <RefreshCw
                className={`w-3.5 h-3.5 ${isAuditRefreshing ? 'animate-spin' : ''}`}
              />
            }
            onClick={() => {
              refetchSLO();
              fetchAudit(true);
            }}
          >
            Refresh
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Export Audit
          </Button>
        </div>
      </div>

      {/* ── Top 4 Summary Cards (live SLO) ───────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {/* SLO Overall Status */}
        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              SLO Overall Status
            </span>
            {sloLoading ? (
              <div className="mt-2 flex items-center gap-2 text-slate-400">
                <Loader2 className="w-5 h-5 animate-spin" />
                <span className="text-sm">Loading…</span>
              </div>
            ) : sloError || !sloData ? (
              <h3 className="text-lg font-extrabold text-slate-400 mt-1 flex items-center gap-2">
                <AlertCircle className="w-5 h-5" />
                <span>Unavailable</span>
              </h3>
            ) : (
              <h3
                className={`text-2xl font-extrabold mt-1 flex items-center gap-2 ${
                  isHealthy ? 'text-emerald-700' : 'text-red-700'
                }`}
              >
                {isHealthy ? (
                  <CheckCircle2 className="w-6 h-6 text-emerald-600" />
                ) : (
                  <AlertCircle className="w-6 h-6 text-red-600" />
                )}
                <span>{sloData.overall_status}</span>
              </h3>
            )}
          </div>
          <p className="text-xs text-slate-500 font-medium mt-3">
            {sloLoading
              ? 'Fetching from /admin/slo…'
              : sloError || !sloData
              ? 'Could not reach /admin/slo'
              : isHealthy
              ? `${totalSLOs}/${totalSLOs} SLOs meeting targets`
              : `${breachCount} active breach${breachCount !== 1 ? 'es' : ''} (${totalSLOs} evaluated)`}
          </p>
        </Card>

        {/* P95 Latency */}
        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              P95 Query Latency
            </span>
            {sloLoading ? (
              <div className="h-8 mt-2 bg-slate-100 rounded animate-pulse" />
            ) : !p95SLO ? (
              <h3 className="text-2xl font-extrabold text-slate-400 mt-1 font-mono">—</h3>
            ) : (
              <h3
                className={`text-2xl font-extrabold mt-1 font-mono ${
                  p95SLO.status === 'HEALTHY' ? 'text-slate-900' : 'text-red-700'
                }`}
              >
                {p95SLO.observed_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                <span className="text-base font-bold ml-1">{p95SLO.unit}</span>
              </h3>
            )}
          </div>
          <p
            className={`text-xs font-semibold mt-3 ${
              !p95SLO || p95SLO.status === 'HEALTHY' ? 'text-emerald-700' : 'text-red-600'
            }`}
          >
            {p95SLO
              ? `Target ≤ ${p95SLO.target_value.toLocaleString()} ${p95SLO.unit} — ${p95SLO.status}`
              : 'Loading…'}
          </p>
        </Card>

        {/* API Error Rate */}
        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              API Error Rate
            </span>
            {sloLoading ? (
              <div className="h-8 mt-2 bg-slate-100 rounded animate-pulse" />
            ) : !errorRateSLO ? (
              <h3 className="text-2xl font-extrabold text-slate-400 mt-1 font-mono">—</h3>
            ) : (
              <h3
                className={`text-2xl font-extrabold mt-1 font-mono ${
                  errorRateSLO.status === 'HEALTHY' ? 'text-emerald-700' : 'text-red-700'
                }`}
              >
                {errorRateSLO.observed_value.toFixed(2)}%
              </h3>
            )}
          </div>
          <p className="text-xs text-slate-500 font-medium mt-3">
            {errorRateSLO
              ? `${errorRateSLO.details?.error_count ?? 0} errors / ${errorRateSLO.details?.total_requests ?? '?'} requests`
              : 'Loading…'}
          </p>
        </Card>

        {/* Audit Event Retention — live from API */}
        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Audit Events Retained
            </span>
            {auditLoading ? (
              <div className="h-8 mt-2 bg-slate-100 rounded animate-pulse" />
            ) : (
              <h3 className="text-2xl font-extrabold text-slate-900 mt-1 font-mono">
                {auditData?.total_events_retained?.toLocaleString() ?? '—'}
              </h3>
            )}
          </div>
          <p className="text-xs text-slate-500 font-medium mt-3">
            Ring-buffer compliance log · /admin/audit
          </p>
        </Card>
      </div>

      {/* ── SLO Health Detail Grid (live) ────────────────────────────────── */}
      <Card className="p-6">
        <div className="flex justify-between items-center mb-5">
          <div>
            <h3 className="text-base font-bold text-slate-900">
              Service Level Objectives (SLO) Health
            </h3>
            <p className="text-xs text-slate-500">
              Live from /admin/slo ·{' '}
              {sloData ? `${totalSLOs} SLOs evaluated` : sloLoading ? 'loading…' : 'unavailable'}
            </p>
          </div>
          {!sloLoading && (
            <Badge
              variant={sloError || !sloData ? 'neutral' : isHealthy ? 'success' : 'error'}
              size="md"
            >
              {sloError || !sloData
                ? 'Unavailable'
                : isHealthy
                ? 'All Targets Met'
                : `${breachCount} Breach${breachCount !== 1 ? 'es' : ''}`}
            </Badge>
          )}
        </div>

        {sloLoading && <LoadingSkeleton rows={3} />}

        {!sloLoading && (sloError || !sloData) && (
          <div className="flex items-center gap-3 p-4 bg-slate-50 rounded-xl border border-slate-200 text-sm text-slate-500">
            <XCircle className="w-5 h-5 text-slate-400 flex-shrink-0" />
            <span>
              Could not load SLO data from backend.{' '}
              <button
                onClick={refetchSLO}
                className="text-primary font-bold hover:underline"
              >
                Retry
              </button>
            </span>
          </div>
        )}

        {!sloLoading && sloData && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {sloData.evaluated_slos.map((slo) => {
              const isBreached = slo.status !== 'HEALTHY';
              return (
                <div
                  key={slo.slo_name}
                  className={`p-4 rounded-xl border space-y-2 ${
                    isBreached ? 'bg-red-50 border-red-200' : 'bg-slate-50 border-slate-200'
                  }`}
                >
                  <div className="flex justify-between items-start">
                    <span
                      className={`text-xs font-bold leading-snug ${
                        isBreached ? 'text-red-800' : 'text-slate-800'
                      }`}
                    >
                      {slo.slo_name.replace(/_/g, ' ')}
                    </span>
                    <span
                      className={`w-2 h-2 rounded-full flex-shrink-0 mt-0.5 ${
                        isBreached ? 'bg-red-500' : 'bg-emerald-500'
                      }`}
                    />
                  </div>
                  <div className="flex items-baseline gap-1">
                    <span
                      className={`text-xl font-extrabold font-mono ${
                        isBreached ? 'text-red-700' : 'text-slate-900'
                      }`}
                    >
                      {slo.observed_value.toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })}
                    </span>
                    <span className="text-xs text-slate-400 font-mono">{slo.unit}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 pt-1 border-t border-slate-200/60 flex justify-between">
                    <span>Target:</span>
                    <span className="font-mono font-bold text-slate-700">
                      ≤ {slo.target_value.toLocaleString()} {slo.unit}
                    </span>
                  </div>
                  <div className="flex justify-between items-center">
                    <Badge variant={isBreached ? 'error' : 'success'} size="sm">
                      {slo.status}
                    </Badge>
                    {slo.severity && (
                      <span className="text-[10px] text-slate-400 font-mono uppercase">
                        {slo.severity}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* ── Compliance Audit Trail Table (live) ──────────────────────────── */}
      <Card className="p-0 overflow-hidden">
        <div className="p-5 border-b border-slate-200 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 bg-slate-50/50">
          <div>
            <h3 className="text-base font-bold text-slate-900">Compliance Audit Ledger</h3>
            <p className="text-xs text-slate-500">
              Live from /admin/audit ·{' '}
              <span className="font-mono font-semibold text-slate-700">
                {auditLoading ? '…' : filteredEvents.length}
              </span>{' '}
              matching events
            </p>
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-64">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search event ID, endpoint…"
                className="w-full bg-white border border-slate-200 rounded-lg pl-8 pr-3 py-1.5 text-xs font-medium text-slate-800 placeholder-slate-400 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
              />
            </div>
            <select
              value={eventTypeFilter}
              onChange={(e) => setEventTypeFilter(e.target.value)}
              className="bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-700 focus:outline-none focus:border-primary"
            >
              <option value="ALL">All Event Types</option>
              <option value="analysis_executed">Analysis Executed</option>
              <option value="job_submitted">Job Submitted</option>
              <option value="request_completed">Request Completed</option>
              <option value="readiness_probe">Readiness Probe</option>
            </select>
          </div>
        </div>

        {auditLoading && (
          <div className="p-6">
            <LoadingSkeleton rows={5} />
          </div>
        )}

        {!auditLoading && auditError && (
          <div className="p-8 text-center space-y-3">
            <XCircle className="w-8 h-8 text-red-400 mx-auto" />
            <p className="text-sm font-semibold text-red-800">Failed to load audit events</p>
            <p className="text-xs text-slate-500">{auditError}</p>
            <Button variant="primary" size="sm" onClick={() => fetchAudit()}>
              Retry
            </Button>
          </div>
        )}

        {!auditLoading && !auditError && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="bg-slate-50/80 border-b border-slate-200 text-slate-400 font-bold uppercase tracking-wider">
                  <th className="py-3.5 pl-5">Event ID</th>
                  <th className="py-3.5">Timestamp</th>
                  <th className="py-3.5">Event Type</th>
                  <th className="py-3.5">Endpoint</th>
                  <th className="py-3.5">Method</th>
                  <th className="py-3.5">Client</th>
                  <th className="py-3.5">Status</th>
                  <th className="py-3.5">Latency</th>
                  <th className="py-3.5 pr-5 text-right">Inspect</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 text-slate-700">
                {filteredEvents.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="py-12 text-center text-slate-400 text-sm font-medium">
                      No audit events match the current filters.
                    </td>
                  </tr>
                ) : (
                  filteredEvents.map((evt) => (
                    <tr
                      key={evt.event_id}
                      onClick={() => setSelectedEvent(evt)}
                      className="hover:bg-slate-50/80 transition-colors cursor-pointer"
                    >
                      <td className="py-3.5 pl-5 font-mono font-bold text-slate-900 text-[11px]">
                        {evt.event_id}
                      </td>
                      <td className="py-3.5 text-slate-500 font-mono text-[11px]">
                        {evt.timestamp.slice(0, 19).replace('T', ' ')}
                      </td>
                      <td className="py-3.5">
                        <span className="px-2 py-0.5 rounded bg-slate-100 font-mono text-[11px] text-slate-700 font-semibold">
                          {evt.event_type}
                        </span>
                      </td>
                      <td className="py-3.5 font-mono text-slate-600 text-[11px]">
                        {evt.endpoint}
                      </td>
                      <td className="py-3.5 font-mono font-bold text-slate-700">
                        {evt.http_method}
                      </td>
                      <td className="py-3.5 font-mono text-slate-600 text-[11px]">
                        {evt.client_id}
                      </td>
                      <td className="py-3.5">
                        <Badge
                          variant={
                            evt.status_code < 300
                              ? 'success'
                              : evt.status_code < 500
                              ? 'warning'
                              : 'error'
                          }
                          size="sm"
                        >
                          {evt.status_code}
                        </Badge>
                      </td>
                      <td className="py-3.5 font-mono text-slate-500">
                        {evt.duration_ms != null ? `${evt.duration_ms} ms` : '—'}
                      </td>
                      <td className="py-3.5 pr-5 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          leftIcon={<Eye className="w-3.5 h-3.5" />}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedEvent(evt);
                          }}
                        >
                          Inspect
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Event Detail Drawer ───────────────────────────────────────────── */}
      <Drawer
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
        title="Audit Event Details"
        subtitle={selectedEvent ? `Event ID: ${selectedEvent.event_id}` : ''}
        width="lg"
      >
        {selectedEvent && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-slate-400 block font-medium">Request ID</span>
                <span className="font-mono font-bold text-slate-900 break-all">
                  {selectedEvent.request_id}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-slate-400 block font-medium">HTTP Status</span>
                <span
                  className={`font-mono font-bold ${
                    selectedEvent.status_code < 400 ? 'text-emerald-700' : 'text-red-700'
                  }`}
                >
                  {selectedEvent.status_code} ({selectedEvent.status})
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-slate-400 block font-medium">LLM / Model</span>
                <span className="font-mono font-bold text-slate-900">
                  {selectedEvent.llm_provider ?? 'Deterministic'} /{' '}
                  {selectedEvent.model ?? 'N/A'}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-200">
                <span className="text-slate-400 block font-medium">Duration</span>
                <span className="font-mono font-bold text-slate-900">
                  {selectedEvent.duration_ms != null ? `${selectedEvent.duration_ms} ms` : '—'}
                </span>
              </div>
            </div>

            {selectedEvent.query_summary && (
              <div className="p-4 bg-slate-50 rounded-xl border border-slate-200 text-xs">
                <span className="text-slate-400 font-bold uppercase block mb-1">
                  Query Summary
                </span>
                <p className="text-slate-800 font-medium">{selectedEvent.query_summary}</p>
              </div>
            )}

            {selectedEvent.executed_agents && selectedEvent.executed_agents.length > 0 && (
              <div className="p-4 bg-slate-50 rounded-xl border border-slate-200 text-xs">
                <span className="text-slate-400 font-bold uppercase block mb-2">
                  Executed Agents
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {selectedEvent.executed_agents.map((agent) => (
                    <span
                      key={agent}
                      className="px-2 py-0.5 rounded-md bg-primary/10 text-primary text-[11px] font-mono font-bold border border-primary/20"
                    >
                      {agent}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selectedEvent.fallback_used && (
              <div className="p-3 bg-amber-50 rounded-xl border border-amber-200 text-xs font-semibold text-amber-800 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0" />
                LLM fallback (deterministic) was used for this request.
              </div>
            )}

            <div>
              <span className="text-slate-400 font-bold uppercase text-xs block mb-2">
                Raw JSON Payload
              </span>
              <pre className="bg-slate-900 text-slate-200 p-4 rounded-xl text-[11px] font-mono overflow-x-auto max-h-72">
                {JSON.stringify(selectedEvent, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
