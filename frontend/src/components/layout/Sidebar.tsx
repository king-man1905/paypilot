import React from 'react';
import {
  LayoutDashboard,
  Receipt,
  LineChart,
  Settings,
  ShieldCheck,
  Bot,
  Sparkles,
  ArrowRight,
  Send,
} from 'lucide-react';

export type NavItemKey =
  | 'overview'
  | 'transactions'
  | 'analytics'
  | 'intelligence'
  | 'audit'
  | 'settings';

interface SidebarProps {
  activeTab: NavItemKey;
  onTabChange: (tab: NavItemKey) => void;
  onOpenAnalysisModal?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  onTabChange,
  onOpenAnalysisModal,
}) => {
  const navItems = [
    {
      key: 'overview' as NavItemKey,
      label: 'Overview',
      icon: LayoutDashboard,
      badge: null,
    },
    {
      key: 'transactions' as NavItemKey,
      label: 'Transactions',
      icon: Receipt,
      badge: '15k',
    },
    {
      key: 'analytics' as NavItemKey,
      label: 'Analytics',
      icon: LineChart,
      badge: null,
    },
    {
      key: 'intelligence' as NavItemKey,
      label: 'AI Intelligence',
      icon: Bot,
      badge: 'AI Engine',
      badgeClass: 'bg-emerald-100 text-emerald-800 border-emerald-300 font-bold',
    },
    {
      key: 'audit' as NavItemKey,
      label: 'Audit & Security',
      icon: ShieldCheck,
      badge: null,
    },
    {
      key: 'settings' as NavItemKey,
      label: 'Settings & Team',
      icon: Settings,
      badge: null,
    },
  ];

  return (
    <aside className="w-72 fixed left-0 top-0 h-screen bg-white border-r border-slate-200/90 flex flex-col p-6 overflow-y-auto hidden lg:flex z-40 select-none">
      {/* Brand Header */}
      <div className="mb-8 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-primary text-white flex items-center justify-center shadow-md shadow-primary/20">
            <Bot className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-xl font-extrabold text-slate-900 tracking-tight flex items-center gap-1.5">
              PayPilot
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary-light text-primary font-bold uppercase tracking-wider">
                v1.24
              </span>
            </h1>
            <p className="text-xs font-semibold text-slate-400">Autonomous Revenue Recovery</p>
          </div>
        </div>
      </div>

      {/* Navigation List */}
      <nav className="flex-1 space-y-1.5">
        <div className="text-[11px] font-bold text-slate-400 uppercase tracking-wider px-3 mb-2">
          Management Hub
        </div>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.key;
          return (
            <button
              key={item.key}
              onClick={() => onTabChange(item.key)}
              className={`w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl font-medium text-sm transition-all duration-150 ${
                isActive
                  ? 'bg-primary/10 text-primary font-semibold shadow-xs'
                  : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center gap-3">
                <Icon
                  className={`w-5 h-5 transition-colors ${
                    isActive ? 'text-primary' : 'text-slate-400'
                  }`}
                />
                <span>{item.label}</span>
              </div>
              {item.badge && (
                <span
                  className={`text-[10px] px-2 py-0.5 rounded-full border ${
                    item.badgeClass || 'bg-slate-100 text-slate-600 border-slate-200'
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* AI Quick Analysis Card */}
      <div className="mt-auto pt-6">
        <div className="bg-gradient-to-br from-emerald-50 to-slate-50 rounded-2xl p-4 border border-emerald-200/70 shadow-xs mb-4">
          <div className="flex items-center gap-2 mb-2">
            <div className="p-1.5 bg-emerald-500/10 text-emerald-700 rounded-lg">
              <Sparkles className="w-4 h-4 text-emerald-600" />
            </div>
            <span className="text-xs font-bold text-slate-800">Agentic Diagnosis</span>
          </div>
          <p className="text-xs text-slate-600 mb-3 leading-relaxed">
            Diagnose payment friction, revenue leaks, and checkout drop-offs in real time.
          </p>
          <button
            onClick={() => onTabChange('intelligence')}
            className="w-full bg-white hover:bg-emerald-50 border border-emerald-300/80 text-emerald-700 text-xs font-bold py-2 px-3 rounded-lg flex items-center justify-center gap-1.5 transition-colors shadow-xs"
          >
            <span>Launch Analysis Studio</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Global Action CTA Button */}
        <button
          onClick={() => {
            if (onOpenAnalysisModal) {
              onOpenAnalysisModal();
            } else {
              onTabChange('intelligence');
            }
          }}
          className="w-full bg-primary text-white rounded-xl py-3 px-4 text-sm font-semibold flex items-center justify-center gap-2 hover:bg-primary-hover transition-all duration-150 shadow-sm active:scale-[0.98]"
        >
          <Send className="w-4 h-4" />
          <span>Quick Diagnostic Run</span>
        </button>
      </div>
    </aside>
  );
};
