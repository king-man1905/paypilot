import React, { useState, useEffect } from 'react';
import {
  Key,
  Globe,
  Server,
  Users,
  Copy,
  Check,
  Eye,
  EyeOff,
  RefreshCw,
  Plus,
  Sliders,
} from 'lucide-react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { LoadingSkeleton } from '../components/common/LoadingSkeleton';
import { useCurrency, CURRENCY_CONFIGS } from '../context/CurrencyContext';
import { useAuth } from '../context/AuthContext';
import { CurrencyCode } from '../types/api';
import { ConfigDiagnosticsSchema } from '../types/api';
import { apiClient } from '../api/client';

export const SettingsPage: React.FC = () => {
  const { currency, setCurrency } = useCurrency();
  const { apiKey, clientId, role, setApiKey, setClientId, setRole } = useAuth();
  const [showApiKey, setShowApiKey] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [configData, setConfigData] = useState<ConfigDiagnosticsSchema | null>(null);
  const [isLoadingConfig, setIsLoadingConfig] = useState(true);

  // Fetch real config from backend on mount
  useEffect(() => {
    apiClient.getConfigDiagnostics()
      .then((data) => setConfigData(data))
      .catch(() => setConfigData(null))
      .finally(() => setIsLoadingConfig(false));
  }, []);

  const teamMembers = [
    {
      name: 'Priya Sharma',
      email: 'priya.sharma@paypilot-enterprise.com',
      role: 'Admin',
      status: 'Active',
      lastActive: '2 mins ago',
      avatar: 'PS',
    },
    {
      name: 'Aman Verma',
      email: 'aman.verma@paypilot-enterprise.com',
      role: 'Analyst',
      status: 'Active',
      lastActive: '1 hour ago',
      avatar: 'AV',
    },
    {
      name: 'DevOps Orchestrator',
      email: 'kube-bot@paypilot-enterprise.com',
      role: 'System Bot',
      status: 'Active',
      lastActive: 'Just now',
      avatar: 'KB',
    },
  ];

  const handleCopyApiKey = () => {
    navigator.clipboard.writeText(apiKey);
    setCopiedKey(true);
    setTimeout(() => setCopiedKey(false), 2000);
  };

  const handleRegenerateKey = () => {
    const newKey = `paypilot_${Math.random().toString(36).substring(2, 15)}_${Math.random().toString(36).substring(2, 15)}`;
    setApiKey(newKey);
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 3000);
  };

  return (
    <div className="space-y-8 animate-fadeIn max-w-5xl">
      {/* Header */}
      <div>
        <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
          Settings &amp; Preferences
        </h2>
        <p className="text-xs sm:text-sm font-medium text-slate-500 mt-1">
          Manage API keys, global currency defaults, tenant quotas, and team access permissions.
        </p>
      </div>

      {savedSuccess && (
        <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-xs font-bold text-emerald-800 flex items-center gap-2">
          <Check className="w-4 h-4 text-emerald-600" />
          <span>Configuration preferences updated successfully.</span>
        </div>
      )}

      {/* 1. Global Currency & Regional Preferences */}
      <Card className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 bg-primary-light text-primary rounded-xl">
            <Globe className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-slate-900">
              Global Currency &amp; Locale Formatting
            </h3>
            <p className="text-xs text-slate-500">
              Configure display currency. Uses{' '}
              <strong>static reference exchange rates</strong> — not live FX data.
              All financial figures on dashboards are converted from the INR base dataset.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-3 pt-2">
          {(Object.keys(CURRENCY_CONFIGS) as CurrencyCode[]).map((code) => {
            const curr = CURRENCY_CONFIGS[code];
            const isSelected = currency === code;
            return (
              <div
                key={code}
                onClick={() => setCurrency(code)}
                className={`p-4 rounded-xl border transition-all cursor-pointer text-center ${
                  isSelected
                    ? 'border-primary bg-primary/5 ring-2 ring-primary/20 shadow-xs'
                    : 'border-slate-200 bg-white hover:border-slate-300'
                }`}
              >
                <span className="font-mono text-2xl font-extrabold text-slate-900 block mb-1">
                  {curr.symbol}
                </span>
                <span className="font-bold text-xs text-slate-800 block">{curr.code}</span>
                <span className="text-[10px] text-slate-400 block mt-0.5">{curr.name}</span>
                <span className="text-[10px] font-mono text-slate-500 block mt-2 pt-2 border-t border-slate-100">
                  1 {curr.code} = {curr.exchangeRateToINR} INR
                </span>
                <span className="text-[9px] text-slate-400 block mt-0.5 italic">
                  (static reference rate)
                </span>
              </div>
            );
          })}
        </div>
      </Card>

      {/* 2. Tenant API Authentication Key */}
      <Card className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 bg-slate-100 text-slate-700 rounded-xl">
            <Key className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-slate-900">API Key &amp; Tenant Identity</h3>
            <p className="text-xs text-slate-500">
              Secure bearer credentials used for automated ingestion and diagnostic queries.
            </p>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-bold text-slate-700 block mb-1.5">
                Tenant Client ID
              </label>
              <input
                type="text"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs font-mono text-slate-800 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-slate-700 block mb-1.5">Active Role</label>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as any)}
                className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2 text-xs font-semibold text-slate-800 focus:outline-none focus:border-primary"
              >
                <option value="admin">Administrator (Full Access)</option>
                <option value="analyst">Financial Analyst (Read / Analyze)</option>
              </select>
            </div>
          </div>

          <div>
            <label className="text-xs font-bold text-slate-700 block mb-1.5">Secret API Key</label>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <input
                  type={showApiKey ? 'text' : 'password'}
                  readOnly
                  value={apiKey}
                  className="w-full bg-slate-50 border border-slate-200 rounded-lg pl-3.5 pr-10 py-2 text-xs font-mono text-slate-800 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>

              <Button
                variant="secondary"
                size="md"
                onClick={handleCopyApiKey}
                leftIcon={
                  copiedKey ? (
                    <Check className="w-4 h-4 text-emerald-600" />
                  ) : (
                    <Copy className="w-4 h-4" />
                  )
                }
              >
                {copiedKey ? 'Copied' : 'Copy'}
              </Button>

              <Button
                variant="secondary"
                size="md"
                onClick={handleRegenerateKey}
                leftIcon={<RefreshCw className="w-4 h-4" />}
              >
                Roll Key
              </Button>
            </div>
          </div>
        </div>
      </Card>

      {/* 3. Operational Quotas & Rate Limits */}
      <Card className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 bg-amber-50 text-amber-700 rounded-xl">
            <Sliders className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-slate-900">
              Tenant Quota &amp; Rate Limit Utilization
            </h3>
            <p className="text-xs text-slate-500">
              Tier limits enforced by backend Redis and TokenBucket guards
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 pt-2">
          <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-2">
            <div className="flex justify-between text-xs font-semibold">
              <span className="text-slate-700">Daily Analysis Queries</span>
              <span className="font-mono text-slate-900">14 / 100</span>
            </div>
            <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
              <div className="bg-primary h-2 rounded-full" style={{ width: '14%' }} />
            </div>
            <p className="text-[10px] text-slate-400">Resets at midnight UTC</p>
          </div>

          <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-2">
            <div className="flex justify-between text-xs font-semibold">
              <span className="text-slate-700">Daily Background Jobs</span>
              <span className="font-mono text-slate-900">3 / 20</span>
            </div>
            <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
              <div className="bg-primary h-2 rounded-full" style={{ width: '15%' }} />
            </div>
            <p className="text-[10px] text-slate-400">Resets at midnight UTC</p>
          </div>

          <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-2">
            <div className="flex justify-between text-xs font-semibold">
              <span className="text-slate-700">Max Concurrent Tasks</span>
              <span className="font-mono text-slate-900">1 / 5</span>
            </div>
            <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
              <div className="bg-primary h-2 rounded-full" style={{ width: '20%' }} />
            </div>
            <p className="text-[10px] text-slate-400">Active worker pool</p>
          </div>
        </div>
      </Card>

      {/* 4. Configuration Diagnostics — from live /admin/config */}
      <Card className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2.5 bg-slate-100 text-slate-700 rounded-xl">
            <Server className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-slate-900">
              System Configuration Diagnostics
            </h3>
            <p className="text-xs text-slate-500">
              Live topology from /admin/config — reflects actual running environment
            </p>
          </div>
        </div>

        {isLoadingConfig ? (
          <LoadingSkeleton rows={2} />
        ) : configData ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs pt-1">
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block font-medium">Environment</span>
                <span className="font-bold text-slate-900 font-mono uppercase">
                  {configData.environment}
                </span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block font-medium">LLM Provider</span>
                <span className="font-bold text-slate-900 font-mono">{configData.llm_provider}</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block font-medium">Database Store</span>
                <span className="font-bold text-slate-900 font-mono">{configData.database_backend}</span>
              </div>
              <div className="p-3 bg-slate-50 rounded-lg border border-slate-100">
                <span className="text-slate-400 block font-medium">Rate Limiter</span>
                <span className="font-bold text-slate-900 font-mono">{configData.rate_limit_backend}</span>
              </div>
            </div>

            {/* Secrets Status */}
            <div className="mt-4 pt-4 border-t border-slate-100">
              <h4 className="text-xs font-bold text-slate-700 mb-2 uppercase tracking-wider">
                Secrets Status
              </h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(configData.secrets_status || {}).map(([key, val]) => (
                  <span
                    key={key}
                    className={`px-2.5 py-1 rounded-lg text-[11px] font-mono font-bold border ${
                      val === 'configured'
                        ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                        : 'bg-amber-50 text-amber-800 border-amber-200'
                    }`}
                  >
                    {key}: <span className="font-normal">{val}</span>
                  </span>
                ))}
              </div>
            </div>
          </>
        ) : (
          <p className="text-xs text-slate-400 mt-2">
            Configuration unavailable — backend may be unreachable.
          </p>
        )}
      </Card>

      {/* 5. Team Members Directory */}
      <Card className="p-6">
        <div className="flex justify-between items-center mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-slate-100 text-slate-700 rounded-xl">
              <Users className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">
                Team Directory &amp; Access Control
              </h3>
              <p className="text-xs text-slate-500">Manage user access and assigned privileges</p>
            </div>
          </div>
          <Button variant="primary" size="sm" leftIcon={<Plus className="w-3.5 h-3.5" />}>
            Invite Member
          </Button>
        </div>

        <div className="divide-y divide-slate-100">
          {teamMembers.map((member) => (
            <div key={member.email} className="py-3 flex items-center justify-between text-xs">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-primary/10 text-primary font-bold flex items-center justify-center">
                  {member.avatar}
                </div>
                <div>
                  <h4 className="font-bold text-slate-900">{member.name}</h4>
                  <p className="text-[11px] text-slate-500 font-mono">{member.email}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Badge
                  variant={member.role === 'Admin' ? 'primary' : 'neutral'}
                  size="sm"
                >
                  {member.role}
                </Badge>
                <span className="text-slate-400 font-mono text-[11px]">{member.lastActive}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
