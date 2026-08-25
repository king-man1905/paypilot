import React, { useState } from 'react';
import { CurrencyProvider } from './context/CurrencyContext';
import { AuthProvider } from './context/AuthContext';
import { SLOProvider } from './context/SLOContext';
import { AppShell } from './components/layout/AppShell';
import { NavItemKey } from './components/layout/Sidebar';
import { OverviewPage } from './pages/OverviewPage';
import { TransactionsPage } from './pages/TransactionsPage';
import { AnalyticsPage } from './pages/AnalyticsPage';
import { IntelligencePage } from './pages/IntelligencePage';
import { AuditSecurityPage } from './pages/AuditSecurityPage';
import { SettingsPage } from './pages/SettingsPage';

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavItemKey>('overview');

  const renderActivePage = () => {
    switch (activeTab) {
      case 'overview':
        return (
          <OverviewPage
            onNavigateToTab={(tab) => setActiveTab(tab)}
            onOpenAnalysis={() => setActiveTab('intelligence')}
          />
        );
      case 'transactions':
        return <TransactionsPage />;
      case 'analytics':
        return <AnalyticsPage onNavigateToAI={() => setActiveTab('intelligence')} />;
      case 'intelligence':
        return <IntelligencePage />;
      case 'audit':
        return <AuditSecurityPage />;
      case 'settings':
        return <SettingsPage />;
      default:
        return (
          <OverviewPage
            onNavigateToTab={(tab) => setActiveTab(tab)}
            onOpenAnalysis={() => setActiveTab('intelligence')}
          />
        );
    }
  };

  return (
    <AuthProvider>
      <CurrencyProvider defaultCurrency="INR">
        {/* SLOProvider fetches /admin/slo once — shared by Header & AuditSecurityPage */}
        <SLOProvider>
          <AppShell
            activeTab={activeTab}
            onTabChange={(tab) => setActiveTab(tab)}
            onOpenQuickAnalysis={() => setActiveTab('intelligence')}
          >
            {renderActivePage()}
          </AppShell>
        </SLOProvider>
      </CurrencyProvider>
    </AuthProvider>
  );
};

export default App;
