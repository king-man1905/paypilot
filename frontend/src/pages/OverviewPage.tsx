import React, { useState } from 'react';
import {
  Wallet,
  TrendingUp,
  CreditCard,
  Lightbulb,
  Sparkles,
  ArrowRight,
  RefreshCw,
  CheckCircle2,
  Smartphone,
  Monitor,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { FormattedCurrency } from '../components/common/FormattedCurrency';
import { useCurrency } from '../context/CurrencyContext';
import {
  MOCK_SUMMARY,
  MOCK_PAYMENT_METHODS_HEALTH,
  MOCK_TRANSACTIONS,
} from '../api/mockData';

interface OverviewPageProps {
  onNavigateToTab: (tab: any) => void;
  onOpenAnalysis: (query?: string) => void;
}

export const OverviewPage: React.FC<OverviewPageProps> = ({
  onNavigateToTab,
}) => {
  const { convertFromINR, config } = useCurrency();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = () => {
    setIsRefreshing(true);
    setTimeout(() => setIsRefreshing(false), 600);
  };

  // Chart data converted to active currency scale
  const chartData = [
    { month: 'Jan', gross: convertFromINR(12500000), realized: convertFromINR(11200000), recoverable: convertFromINR(950000) },
    { month: 'Feb', gross: convertFromINR(14200000), realized: convertFromINR(12600000), recoverable: convertFromINR(1150000) },
    { month: 'Mar', gross: convertFromINR(13100000), realized: convertFromINR(10900000), recoverable: convertFromINR(1650000) },
    { month: 'Apr', gross: convertFromINR(15800000), realized: convertFromINR(13700000), recoverable: convertFromINR(1400000) },
    { month: 'May', gross: convertFromINR(14100000), realized: convertFromINR(11850000), recoverable: convertFromINR(1700000) },
    { month: 'Jun', gross: convertFromINR(14550000), realized: convertFromINR(12205000), recoverable: convertFromINR(1850000) },
  ];

  return (
    <div className="space-y-8 animate-fadeIn">
      {/* Top Header Section */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
            Financial Overview
          </h2>
          <p className="text-xs sm:text-sm font-medium text-slate-500 mt-1">
            Dataset analytics from 15,000 transactions — use AI Intelligence for live multi-agent analysis.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleRefresh}
            isLoading={isRefreshing}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Refresh
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => onNavigateToTab('intelligence')}
            leftIcon={<Sparkles className="w-3.5 h-3.5 text-white" />}
          >
            Run AI Diagnosis
          </Button>
        </div>
      </div>

      {/* AI Diagnostic Briefing Card */}
      <div className="bg-gradient-to-r from-emerald-900 via-primary to-slate-900 rounded-2xl p-6 text-white shadow-premium-lg relative overflow-hidden">
        {/* Subtle background glow */}
        <div className="absolute right-0 top-0 w-96 h-96 bg-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-6 relative z-10">
          <div className="flex items-start gap-4 flex-1">
            <div className="p-3 bg-white/10 backdrop-blur-md rounded-xl text-emerald-300 border border-white/10 flex-shrink-0">
              <Lightbulb className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <span className="text-xs font-bold uppercase tracking-wider text-emerald-300">
                  Autonomous Executive Briefing
                </span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-emerald-400/20 text-emerald-200 border border-emerald-300/30">
                  LIVE PIPELINE
                </span>
              </div>
              <h3 className="text-base sm:text-lg font-bold text-white mb-2 leading-snug">
                Identified{' '}
                <FormattedCurrency
                  amountInINR={MOCK_SUMMARY.recoverable_opportunity_inr}
                  className="text-emerald-300 font-extrabold"
                />{' '}
                in Recoverable Revenue Leakages
              </h3>
              <p className="text-xs sm:text-sm text-slate-200/90 leading-relaxed max-w-3xl">
                Primary friction point is concentrated in{' '}
                <strong className="text-white">UPI Gateway Timeouts (79.4% success)</strong> and{' '}
                <strong className="text-white">Mobile Checkout Shipping Abandonment (18.6% gap)</strong>.
                Deploying smart multi-rail routing is estimated to recover 80% of lost transactions.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 flex-shrink-0 w-full md:w-auto">
            <Button
              variant="secondary"
              size="md"
              className="w-full md:w-auto bg-white text-emerald-950 font-bold hover:bg-emerald-50 border-0 shadow-md"
              onClick={() => onNavigateToTab('intelligence')}
              rightIcon={<ArrowRight className="w-4 h-4 text-emerald-900" />}
            >
              Inspect Ranked Actions (P1–P3)
            </Button>
          </div>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {/* Total Gross Volume */}
        <Card className="relative overflow-hidden flex flex-col justify-between">
          <div className="flex justify-between items-start mb-3">
            <div>
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                Total Gross Volume
              </p>
              <h3 className="text-2xl font-extrabold text-slate-900 mt-1">
                <FormattedCurrency amountInINR={MOCK_SUMMARY.total_gross_volume_inr} />
              </h3>
            </div>
            <div className="p-2.5 bg-slate-100 rounded-xl text-slate-600">
              <Wallet className="w-5 h-5" />
            </div>
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
            <Badge variant="success" size="sm" icon={<TrendingUp className="w-3 h-3" />}>
              +12.5%
            </Badge>
            <span className="text-[11px] text-slate-500 font-medium">vs last month</span>
          </div>
        </Card>

        {/* Realized Revenue */}
        <Card className="relative overflow-hidden flex flex-col justify-between">
          <div className="flex justify-between items-start mb-3">
            <div>
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                Net Realized Revenue
              </p>
              <h3 className="text-2xl font-extrabold text-slate-900 mt-1">
                <FormattedCurrency amountInINR={MOCK_SUMMARY.realized_revenue_inr} />
              </h3>
            </div>
            <div className="p-2.5 bg-slate-100 rounded-xl text-slate-600">
              <CreditCard className="w-5 h-5" />
            </div>
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
            <Badge variant="success" size="sm" icon={<TrendingUp className="w-3 h-3" />}>
              +8.2%
            </Badge>
            <span className="text-[11px] text-slate-500 font-medium">86.0% conversion</span>
          </div>
        </Card>

        {/* Overall Success Rate */}
        <Card className="relative overflow-hidden flex flex-col justify-between">
          <div className="flex justify-between items-start mb-3">
            <div>
              <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                Payment Success Rate
              </p>
              <h3 className="text-2xl font-extrabold text-slate-900 mt-1 font-mono">
                {MOCK_SUMMARY.overall_success_rate_pct}%
              </h3>
            </div>
            <div className="p-2.5 bg-emerald-50 rounded-xl text-emerald-700">
              <CheckCircle2 className="w-5 h-5" />
            </div>
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-slate-100">
            <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
              <div
                className="bg-emerald-500 h-1.5 rounded-full"
                style={{ width: `${MOCK_SUMMARY.overall_success_rate_pct}%` }}
              />
            </div>
            <span className="text-[11px] text-slate-500 font-medium whitespace-nowrap">
              Target: 95%
            </span>
          </div>
        </Card>

        {/* Recoverable Opportunity */}
        <Card className="relative overflow-hidden flex flex-col justify-between bg-gradient-to-br from-emerald-50/60 to-white border-emerald-200">
          <div className="flex justify-between items-start mb-3">
            <div>
              <p className="text-xs font-bold text-emerald-800 uppercase tracking-wider">
                Recoverable Opportunity
              </p>
              <h3 className="text-2xl font-extrabold text-emerald-700 mt-1">
                <FormattedCurrency amountInINR={MOCK_SUMMARY.recoverable_opportunity_inr} />
              </h3>
            </div>
            <div className="p-2.5 bg-emerald-100 text-emerald-800 rounded-xl">
              <Sparkles className="w-5 h-5" />
            </div>
          </div>
          <div className="flex items-center justify-between pt-2 border-t border-emerald-100">
            <span className="text-[11px] text-emerald-800 font-bold">3 Actionable Fixes</span>
            <button
              onClick={() => onNavigateToTab('intelligence')}
              className="text-xs font-bold text-primary hover:underline flex items-center gap-1"
            >
              <span>Execute</span>
              <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        </Card>
      </div>

      {/* Main Charts & Analytics Split Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Revenue & Recovery Trends Chart */}
        <Card className="lg:col-span-2 flex flex-col justify-between">
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 mb-6">
            <div>
              <h3 className="text-base font-bold text-slate-900">Revenue & Recovery Trajectory</h3>
              <p className="text-xs text-slate-500">Gross volume vs. realized revenue & recoverable pool ({config.code})</p>
            </div>
            <div className="flex items-center gap-4 text-xs font-semibold">
              <span className="flex items-center gap-1.5 text-slate-600">
                <span className="w-2.5 h-2.5 rounded-full bg-slate-400" />
                Gross Volume
              </span>
              <span className="flex items-center gap-1.5 text-emerald-700">
                <span className="w-2.5 h-2.5 rounded-full bg-primary" />
                Realized
              </span>
              <span className="flex items-center gap-1.5 text-amber-600">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
                Recoverable
              </span>
            </div>
          </div>

          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorRealized" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="colorGross" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#94a3b8" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#94a3b8" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
                <XAxis dataKey="month" tickLine={false} axisLine={false} tick={{ fill: '#64748b', fontSize: 12 }} />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{ fill: '#64748b', fontSize: 11, fontFamily: 'monospace' }}
                  tickFormatter={(val) => `${config.symbol}${(val / 1000).toFixed(0)}k`}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      return (
                        <div className="bg-slate-900 text-white p-3 rounded-xl shadow-xl text-xs font-mono">
                          <p className="font-sans font-bold text-slate-300 mb-1">{payload[0].payload.month}</p>
                          <p className="text-emerald-400">
                            Realized: {config.symbol}{Number(payload[1]?.value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </p>
                          <p className="text-slate-300">
                            Gross: {config.symbol}{Number(payload[0]?.value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </p>
                          <p className="text-amber-400">
                            Recoverable: {config.symbol}{Number(payload[2]?.value || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          </p>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Area type="monotone" dataKey="gross" stroke="#94a3b8" strokeWidth={2} fillOpacity={1} fill="url(#colorGross)" />
                <Area type="monotone" dataKey="realized" stroke="#006c49" strokeWidth={2.5} fillOpacity={1} fill="url(#colorRealized)" />
                <Area type="monotone" dataKey="recoverable" stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="4 4" fill="none" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Right Col: Payment Methods Health */}
        <Card className="flex flex-col justify-between">
          <div>
            <div className="flex justify-between items-center mb-4">
              <div>
                <h3 className="text-base font-bold text-slate-900">Payment Gateway Health</h3>
                <p className="text-xs text-slate-500">Live success rates across rails</p>
              </div>
              <Badge variant="warning" size="sm">UPI Friction</Badge>
            </div>

            <div className="space-y-4">
              {MOCK_PAYMENT_METHODS_HEALTH.map((method) => (
                <div key={method.method} className="space-y-1.5">
                  <div className="flex justify-between items-center text-xs">
                    <span className="font-semibold text-slate-800">{method.method.replace('_', ' ')}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-slate-400 font-mono">{method.share_pct}% share</span>
                      <span
                        className={`font-mono font-bold ${
                          method.success_rate_pct < 85 ? 'text-amber-600' : 'text-emerald-700'
                        }`}
                      >
                        {method.success_rate_pct}%
                      </span>
                    </div>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
                    <div
                      className={`h-2 rounded-full ${
                        method.success_rate_pct < 85 ? 'bg-amber-500' : 'bg-primary'
                      }`}
                      style={{ width: `${method.success_rate_pct}%` }}
                    />
                  </div>
                  {method.lost_inr > 0 && (
                    <p className="text-[11px] text-slate-400 font-mono">
                      Lost GTV: <FormattedCurrency amountInINR={method.lost_inr} hideDecimals />
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="pt-4 mt-4 border-t border-slate-100">
            <Button
              variant="outline"
              size="sm"
              className="w-full justify-between"
              onClick={() => onNavigateToTab('analytics')}
              rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
            >
              View Detailed Analytics
            </Button>
          </div>
        </Card>
      </div>

      {/* Recent Transactions Quick Table */}
      <Card>
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 mb-5">
          <div>
            <h3 className="text-base font-bold text-slate-900">Recent Transactions</h3>
            <p className="text-xs text-slate-500">Live ledger feed across active merchant channels</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onNavigateToTab('transactions')}
            rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
          >
            View All Transactions (15k)
          </Button>
        </div>

        <div className="overflow-x-auto -mx-5 px-5">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 text-slate-400 font-bold uppercase tracking-wider">
                <th className="pb-3 pl-2">Transaction ID</th>
                <th className="pb-3">Timestamp</th>
                <th className="pb-3">Customer</th>
                <th className="pb-3">Payment Method</th>
                <th className="pb-3">Amount</th>
                <th className="pb-3">Status</th>
                <th className="pb-3 text-right pr-2">Device</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {MOCK_TRANSACTIONS.slice(0, 5).map((txn) => (
                <tr key={txn.transaction_id} className="hover:bg-slate-50/80 transition-colors">
                  <td className="py-3.5 pl-2 font-mono font-semibold text-slate-900">
                    {txn.transaction_id}
                  </td>
                  <td className="py-3.5 text-slate-500">{txn.timestamp}</td>
                  <td className="py-3.5 font-mono text-slate-600">{txn.customer_id}</td>
                  <td className="py-3.5">
                    <span className="px-2 py-1 bg-slate-100 rounded-md font-semibold text-slate-700 text-[11px]">
                      {txn.payment_method.replace('_', ' ')}
                    </span>
                  </td>
                  <td className="py-3.5 font-bold text-slate-900">
                    <FormattedCurrency amountInINR={txn.amount} />
                  </td>
                  <td className="py-3.5">
                    <Badge
                      variant={
                        txn.payment_status === 'SUCCESS'
                          ? 'success'
                          : txn.payment_status === 'FAILED'
                          ? 'error'
                          : 'warning'
                      }
                      size="sm"
                    >
                      {txn.payment_status}
                    </Badge>
                  </td>
                  <td className="py-3.5 text-right pr-2">
                    <span className="inline-flex items-center gap-1 text-slate-500">
                      {txn.device_type === 'Mobile' ? (
                        <Smartphone className="w-3.5 h-3.5" />
                      ) : (
                        <Monitor className="w-3.5 h-3.5" />
                      )}
                      {txn.device_type}
                    </span>
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
