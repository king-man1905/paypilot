import React, { useState } from 'react';
import {
  Search,
  Globe,
  Bell,
  CheckCircle2,
  AlertCircle,
  ChevronDown,
  Shield,
  Menu,
  Loader2,
} from 'lucide-react';
import { useCurrency, CURRENCY_CONFIGS } from '../../context/CurrencyContext';
import { useSLO } from '../../context/SLOContext';
import { CurrencyCode } from '../../types/api';

interface HeaderProps {
  onSearchChange?: (val: string) => void;
  onMobileMenuToggle?: () => void;
  environmentName?: string;
  isBackendHealthy?: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  onSearchChange,
  onMobileMenuToggle,
  environmentName = 'Production',
  isBackendHealthy = true,
}) => {
  const { currency, setCurrency, config } = useCurrency();
  const { sloData, sloLoading, sloError, isHealthy, breachCount } = useSLO();
  const [currencyDropdownOpen, setCurrencyDropdownOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [searchValue, setSearchValue] = useState('');

  const handleSearch = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchValue(e.target.value);
    onSearchChange?.(e.target.value);
  };

  const currencyList = Object.keys(CURRENCY_CONFIGS) as CurrencyCode[];

  // Derive notification content from live SLO — no hardcoded values
  const renderSLONotification = () => {
    if (sloLoading) {
      return (
        <div className="flex items-start gap-2.5 p-2 rounded-lg bg-slate-50">
          <Loader2 className="w-4 h-4 text-slate-400 mt-0.5 flex-shrink-0 animate-spin" />
          <div>
            <p className="text-xs font-semibold text-slate-700">Loading SLO status...</p>
            <p className="text-[10px] text-slate-400">Fetching from /admin/slo</p>
          </div>
        </div>
      );
    }

    if (sloError || !sloData) {
      return (
        <div className="flex items-start gap-2.5 p-2 rounded-lg bg-slate-50">
          <AlertCircle className="w-4 h-4 text-slate-400 mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-xs font-semibold text-slate-700">SLO status unavailable</p>
            <p className="text-[10px] text-slate-400">Visit Audit &amp; Security to retry</p>
          </div>
        </div>
      );
    }

    if (!isHealthy) {
      const p95 = sloData.evaluated_slos?.find((s) => s.slo_name.includes('latency'));
      return (
        <div className="flex items-start gap-2.5 p-2 rounded-lg bg-red-50 border border-red-100">
          <AlertCircle className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-xs font-semibold text-red-900">
              {breachCount} SLO Breach{breachCount !== 1 ? 'es' : ''} — {sloData.overall_status}
            </p>
            {p95 && (
              <p className="text-[10px] text-red-700 font-mono">
                P95 latency:{' '}
                {p95.observed_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}{' '}
                {p95.unit} (target: {p95.target_value.toLocaleString()})
              </p>
            )}
            <p className="text-[10px] text-red-500 mt-0.5">
              Visit Audit &amp; Security for full SLO report
            </p>
          </div>
        </div>
      );
    }

    // HEALTHY — but show real values, not hardcoded ones
    const p95 = sloData.evaluated_slos?.find((s) => s.slo_name.includes('latency'));
    return (
      <div className="flex items-start gap-2.5 p-2 rounded-lg bg-emerald-50 border border-emerald-100">
        <CheckCircle2 className="w-4 h-4 text-emerald-600 mt-0.5 flex-shrink-0" />
        <div>
          <p className="text-xs font-semibold text-slate-800">
            All {sloData.total_slos_evaluated} SLOs healthy
          </p>
          {p95 && (
            <p className="text-[10px] text-slate-500 font-mono">
              P95:{' '}
              {p95.observed_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}{' '}
              {p95.unit}
            </p>
          )}
        </div>
      </div>
    );
  };

  // Derive the notification bell color from live data
  const bellBadgeColor = sloLoading
    ? 'bg-slate-400'
    : sloError || !sloData
    ? 'bg-slate-400'
    : !isHealthy
    ? 'bg-red-500 animate-pulse'
    : 'bg-emerald-500';

  return (
    <header className="bg-white/90 backdrop-blur-md border-b border-slate-200/90 sticky top-0 z-30 flex items-center justify-between h-16 px-6 lg:px-8">
      {/* Left: Mobile Toggle & Global Search */}
      <div className="flex items-center gap-4 flex-1 max-w-lg">
        <button
          onClick={onMobileMenuToggle}
          className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100 lg:hidden"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="relative w-full max-w-md">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchValue}
            onChange={handleSearch}
            placeholder="Search transactions, customers, audit events..."
            className="w-full bg-slate-50 border border-slate-200 rounded-xl pl-10 pr-4 py-2 text-xs font-medium text-slate-800 placeholder-slate-400 focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-all"
          />
        </div>
      </div>

      {/* Right: Environment Pill, Currency Selector, Notifications, Profile */}
      <div className="flex items-center gap-3">
        {/* Backend & Environment Status Pill */}
        <div className="hidden sm:flex items-center gap-2 px-3 py-1 bg-slate-100 rounded-full border border-slate-200 text-xs font-medium text-slate-700">
          <span
            className={`w-2 h-2 rounded-full ${
              isBackendHealthy ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'
            }`}
          />
          <span className="font-semibold">{environmentName}</span>
          <span className="text-slate-400">|</span>
          <span className="text-[11px] text-slate-500 font-mono">v1.24.0</span>
        </div>

        {/* Currency Switcher */}
        <div className="relative">
          <button
            onClick={() => setCurrencyDropdownOpen(!currencyDropdownOpen)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-xl text-xs font-bold text-slate-800 transition-colors shadow-xs"
            title="Switch Global Currency"
          >
            <Globe className="w-3.5 h-3.5 text-primary" />
            <span className="font-mono">{config.code}</span>
            <span className="text-slate-400">({config.symbol})</span>
            <ChevronDown className="w-3 h-3 text-slate-400" />
          </button>

          {currencyDropdownOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setCurrencyDropdownOpen(false)}
              />
              <div className="absolute right-0 mt-2 w-56 bg-white border border-slate-200 rounded-xl shadow-xl z-50 p-1.5 py-2">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 px-3 py-1">
                  Select Display Currency
                </div>
                {currencyList.map((code) => {
                  const item = CURRENCY_CONFIGS[code];
                  const isSelected = currency === code;
                  return (
                    <button
                      key={code}
                      onClick={() => {
                        setCurrency(code);
                        setCurrencyDropdownOpen(false);
                      }}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
                        isSelected
                          ? 'bg-primary/10 text-primary font-bold'
                          : 'text-slate-700 hover:bg-slate-50'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-bold text-slate-900 w-5">{item.symbol}</span>
                        <span>{item.name}</span>
                      </div>
                      <span className="font-mono text-[10px] text-slate-400">{item.code}</span>
                    </button>
                  );
                })}
                {/* Bug 3 fix: honest label for static rates */}
                <div className="px-3 pt-2 mt-1 border-t border-slate-100">
                  <p className="text-[9px] text-slate-400 italic leading-snug">
                    Conversion uses fixed reference rates — not live FX data.
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Notifications — driven by live SLO, no hardcoded values */}
        <div className="relative">
          <button
            onClick={() => setNotificationsOpen(!notificationsOpen)}
            className="p-2 text-slate-500 hover:text-slate-900 rounded-xl hover:bg-slate-100 transition-colors relative"
          >
            <Bell className="w-4 h-4" />
            <span className={`w-2 h-2 rounded-full absolute top-1.5 right-1.5 ${bellBadgeColor}`} />
          </button>

          {notificationsOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setNotificationsOpen(false)} />
              <div className="absolute right-0 mt-2 w-80 bg-white border border-slate-200 rounded-xl shadow-xl z-50 p-4">
                <div className="flex items-center justify-between mb-3 border-b border-slate-100 pb-2">
                  <h4 className="text-xs font-bold text-slate-900">System Notifications</h4>
                  <span
                    className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                      sloLoading || sloError || !sloData
                        ? 'bg-slate-100 text-slate-500'
                        : !isHealthy
                        ? 'bg-red-100 text-red-800'
                        : 'bg-emerald-100 text-emerald-800'
                    }`}
                  >
                    {sloLoading
                      ? 'Loading…'
                      : sloError || !sloData
                      ? 'Unavailable'
                      : !isHealthy
                      ? `${breachCount} Breach${breachCount !== 1 ? 'es' : ''}`
                      : 'All Healthy'}
                  </span>
                </div>
                <div className="space-y-2.5">
                  {/* Live SLO notification */}
                  {renderSLONotification()}

                  {/* Static: agent pipeline status */}
                  <div className="flex items-start gap-2.5 p-2 rounded-lg bg-slate-50">
                    <Shield className="w-4 h-4 text-primary mt-0.5 flex-shrink-0" />
                    <div>
                      <p className="text-xs font-semibold text-slate-800">
                        Multi-Agent Pipeline Ready
                      </p>
                      <p className="text-[10px] text-slate-500">
                        LangGraph agents operational — visit AI Intelligence
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* User Profile */}
        <div className="flex items-center gap-2 pl-2 border-l border-slate-200">
          <div className="w-8 h-8 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center text-primary font-bold text-xs">
            PA
          </div>
          <div className="hidden md:block text-left">
            <p className="text-xs font-bold text-slate-900 leading-tight">PayPilot Admin</p>
            <p className="text-[10px] text-slate-400 font-medium">Head of Payments</p>
          </div>
        </div>
      </div>
    </header>
  );
};
