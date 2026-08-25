import React, { useState } from 'react';
import {
  TrendingDown,
  Smartphone,
  Monitor,
  Sparkles,
  ArrowRight,
  Download,
} from 'lucide-react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { FormattedCurrency } from '../components/common/FormattedCurrency';
import {
  MOCK_CHECKOUT_FUNNEL,
  MOCK_DEVICE_COMPARISON,
  MOCK_SUMMARY,
} from '../api/mockData';

export const AnalyticsPage: React.FC<{ onNavigateToAI?: () => void }> = ({ onNavigateToAI }) => {
  const [timeRange, setTimeRange] = useState('30d');

  // Category refund distribution data
  const categoryData = [
    { name: 'Fashion', refundRate: 14.8, volumeINR: 28400000, refundINR: 4203200, color: '#ef4444' },
    { name: 'Electronics', refundRate: 6.2, volumeINR: 34500000, refundINR: 2139000, color: '#10b981' },
    { name: 'Home & Living', refundRate: 5.1, volumeINR: 11200000, refundINR: 571200, color: '#3b82f6' },
    { name: 'Beauty', refundRate: 4.8, volumeINR: 6500000, refundINR: 312000, color: '#8b5cf6' },
    { name: 'Grocery', refundRate: 2.1, volumeINR: 3650000, refundINR: 76650, color: '#006c49' },
  ];

  // Failure reasons data
  const failureReasonsData = [
    { reason: 'Gateway Timeout (504)', count: 940, lostINR: 5200000 },
    { reason: 'Issuer Bank Unavailable', count: 520, lostINR: 2900000 },
    { reason: '3DS Auth Failed', count: 380, lostINR: 2150000 },
    { reason: 'Insufficient Funds', count: 260, lostINR: 1545000 },
  ];

  return (
    <div className="space-y-8 animate-fadeIn">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
            Diagnostic Analytics Hub
          </h2>
          <p className="text-xs sm:text-sm font-medium text-slate-500 mt-1">
            Multidimensional funnel breakdown, checkout conversion drops, device friction, and refund anomalies.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value)}
            className="bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs font-semibold text-slate-700 shadow-xs focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="7d">Last 7 Days</option>
            <option value="30d">Last 30 Days</option>
            <option value="90d">Last Quarter</option>
            <option value="1y">Year to Date</option>
          </select>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Export Metrics
          </Button>
        </div>
      </div>

      {/* Top 4 Insight Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Funnel Conversion Rate
            </span>
            <h3 className="text-2xl font-extrabold text-slate-900 mt-1 font-mono">61.2%</h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-amber-600 font-semibold mt-3">
            <TrendingDown className="w-3.5 h-3.5" />
            <span>-3.4% drop at Shipping step</span>
          </div>
        </Card>

        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Mobile vs Desktop Gap
            </span>
            <h3 className="text-2xl font-extrabold text-red-600 mt-1 font-mono">18.6%</h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-slate-500 font-medium mt-3">
            <span>Mobile 55.2% vs Desktop 73.8%</span>
          </div>
        </Card>

        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Total Lost GTV
            </span>
            <h3 className="text-2xl font-extrabold text-slate-900 mt-1">
              <FormattedCurrency amountInINR={MOCK_SUMMARY.total_lost_revenue_inr} />
            </h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-emerald-700 font-semibold mt-3">
            <Sparkles className="w-3.5 h-3.5" />
            <span><FormattedCurrency amountInINR={MOCK_SUMMARY.recoverable_opportunity_inr} /> recoverable</span>
          </div>
        </Card>

        <Card className="flex flex-col justify-between">
          <div>
            <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              High Refund Category
            </span>
            <h3 className="text-2xl font-extrabold text-amber-600 mt-1 font-mono">14.8%</h3>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-slate-500 font-medium mt-3">
            <span>Fashion category sizing returns</span>
          </div>
        </Card>
      </div>

      {/* Funnel & Device Split Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Checkout Conversion Funnel */}
        <Card className="flex flex-col justify-between">
          <div className="flex justify-between items-center mb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">Checkout Conversion Funnel</h3>
              <p className="text-xs text-slate-500">Step-by-step visitor progression from Cart to Order Completion</p>
            </div>
            <Badge variant="neutral" size="sm">24,500 Total Sessions</Badge>
          </div>

          <div className="space-y-4 my-2">
            {MOCK_CHECKOUT_FUNNEL.map((step, idx) => (
              <div key={step.step} className="space-y-1.5">
                <div className="flex justify-between items-center text-xs">
                  <span className="font-bold text-slate-800 flex items-center gap-2">
                    <span className="w-5 h-5 rounded-full bg-slate-100 text-slate-700 flex items-center justify-center font-mono text-[10px]">
                      {idx + 1}
                    </span>
                    {step.step}
                  </span>
                  <div className="flex items-center gap-3">
                    <span className="text-slate-500 font-mono">{step.count.toLocaleString()} sessions</span>
                    <span className="font-mono font-bold text-slate-900">{step.conversion_pct}%</span>
                    {step.drop_pct > 0 && (
                      <span className="text-[11px] text-red-600 font-semibold">
                        -{step.drop_pct}%
                      </span>
                    )}
                  </div>
                </div>
                <div className="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
                  <div
                    className="bg-primary h-2.5 rounded-full transition-all duration-300"
                    style={{ width: `${step.conversion_pct}%` }}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-600">
            <span>Primary Abandonment: <strong>Shipping Details (-19.2%)</strong></span>
            {onNavigateToAI && (
              <button
                onClick={onNavigateToAI}
                className="text-primary font-bold hover:underline flex items-center gap-1"
              >
                <span>View AI Fix (P2)</span>
                <ArrowRight className="w-3 h-3" />
              </button>
            )}
          </div>
        </Card>

        {/* Device Performance Disparity */}
        <Card className="flex flex-col justify-between">
          <div className="flex justify-between items-center mb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">Device Channel Conversion Gap</h3>
              <p className="text-xs text-slate-500">Mobile friction analysis vs. Desktop benchmark</p>
            </div>
            <Badge variant="error" size="sm">18.6% Gap</Badge>
          </div>

          <div className="grid grid-cols-2 gap-4 my-2">
            {/* Mobile Card */}
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center gap-2 mb-2 text-slate-700">
                <Smartphone className="w-5 h-5 text-primary" />
                <h4 className="text-sm font-bold">Mobile Devices</h4>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-slate-500">Conversion Rate</p>
                <h5 className="text-xl font-extrabold text-slate-900 font-mono">
                  {MOCK_DEVICE_COMPARISON.mobile.conversion_rate_pct}%
                </h5>
              </div>
              <div className="mt-3 pt-2 border-t border-slate-200 text-[11px] text-slate-600 space-y-0.5">
                <div>Sessions: <span className="font-mono font-bold">16,500</span></div>
                <div>Completed: <span className="font-mono font-bold">9,100</span></div>
                <div className="text-red-600 font-semibold">Failure Rate: 20.8%</div>
              </div>
            </div>

            {/* Desktop Card */}
            <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center gap-2 mb-2 text-slate-700">
                <Monitor className="w-5 h-5 text-slate-600" />
                <h4 className="text-sm font-bold">Desktop Browser</h4>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-slate-500">Conversion Rate</p>
                <h5 className="text-xl font-extrabold text-emerald-700 font-mono">
                  {MOCK_DEVICE_COMPARISON.desktop.conversion_rate_pct}%
                </h5>
              </div>
              <div className="mt-3 pt-2 border-t border-slate-200 text-[11px] text-slate-600 space-y-0.5">
                <div>Sessions: <span className="font-mono font-bold">8,000</span></div>
                <div>Completed: <span className="font-mono font-bold">5,900</span></div>
                <div className="text-emerald-700 font-semibold">Failure Rate: 7.9%</div>
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 bg-red-50/50 -mx-5 -mb-5 p-4 rounded-b-xl border border-red-100 flex items-center justify-between text-xs">
            <span className="text-red-800 font-medium">
              Estimated Mobile Lost Revenue:{' '}
              <FormattedCurrency
                amountInINR={MOCK_DEVICE_COMPARISON.lost_revenue_mobile_inr}
                className="font-bold"
              />
            </span>
          </div>
        </Card>
      </div>

      {/* Failure Reasons & Category Refunds */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Failure Reasons Breakdown */}
        <Card>
          <div className="flex justify-between items-center mb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">Top Payment Failure Reasons</h3>
              <p className="text-xs text-slate-500">Technical & issuer decline taxonomy</p>
            </div>
          </div>

          <div className="space-y-3">
            {failureReasonsData.map((f) => (
              <div key={f.reason} className="p-3 bg-slate-50 border border-slate-200/80 rounded-xl flex items-center justify-between">
                <div>
                  <h4 className="text-xs font-bold text-slate-900">{f.reason}</h4>
                  <p className="text-[11px] text-slate-500 font-mono mt-0.5">{f.count} failed transactions</p>
                </div>
                <div className="text-right">
                  <span className="text-xs font-bold text-red-600 font-mono">
                    <FormattedCurrency amountInINR={f.lostINR} />
                  </span>
                  <p className="text-[10px] text-slate-400">Lost GTV</p>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* Product Category Refund Heatmap */}
        <Card>
          <div className="flex justify-between items-center mb-4">
            <div>
              <h3 className="text-base font-bold text-slate-900">Product Category Refund Rates</h3>
              <p className="text-xs text-slate-500">Return rates and refunded value across catalog</p>
            </div>
            <Badge variant="warning" size="sm">Fashion Outlier</Badge>
          </div>

          <div className="space-y-3">
            {categoryData.map((cat) => (
              <div key={cat.name} className="p-3 bg-slate-50 border border-slate-200/80 rounded-xl">
                <div className="flex justify-between items-center text-xs mb-1.5">
                  <span className="font-bold text-slate-900">{cat.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-slate-500">
                      Refunded: <FormattedCurrency amountInINR={cat.refundINR} hideDecimals />
                    </span>
                    <span
                      className={`font-mono font-bold ${
                        cat.refundRate > 10 ? 'text-red-600' : 'text-slate-700'
                      }`}
                    >
                      {cat.refundRate}%
                    </span>
                  </div>
                </div>
                <div className="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${Math.min(cat.refundRate * 5, 100)}%`,
                      backgroundColor: cat.color,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
};
