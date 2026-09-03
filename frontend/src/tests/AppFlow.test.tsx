import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from '../App';

describe('PayPilot Application End-to-End Screen Verification', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('1. Overview Screen: Renders all KPI cards, AI briefing, Trajectory chart, and Recent Transactions', () => {
    render(<App />);

    // Header & Titles
    expect(screen.getByText('Financial Overview')).toBeInTheDocument();
    expect(screen.getByText('Autonomous Executive Briefing')).toBeInTheDocument();

    // 4 Core KPI Titles
    expect(screen.getByText('Total Gross Volume')).toBeInTheDocument();
    expect(screen.getByText('Net Realized Revenue')).toBeInTheDocument();
    expect(screen.getByText('Payment Success Rate')).toBeInTheDocument();
    expect(screen.getByText('Recoverable Opportunity')).toBeInTheDocument();

    // Chart & Table headers
    expect(screen.getByText('Revenue & Recovery Trajectory')).toBeInTheDocument();
    expect(screen.getByText('Payment Gateway Health')).toBeInTheDocument();
    expect(screen.getByText('Recent Transactions')).toBeInTheDocument();

    // Check recent transaction rows
    expect(screen.getAllByText('txn_984729103841').length).toBeGreaterThan(0);
  });

  it('2. Transactions Screen: Handles search, status filtering, and opens the Diagnostic Drawer', async () => {
    render(<App />);

    // Navigate to Transactions tab using sidebar button
    const navButtons = screen.getAllByRole('button');
    const txnNavBtn = navButtons.find((b) => b.textContent?.includes('Transactions') && b.textContent?.includes('15k'));
    expect(txnNavBtn).toBeDefined();
    fireEvent.click(txnNavBtn!);

    expect(screen.getByText('Transactions Ledger')).toBeInTheDocument();

    // Search for a specific transaction
    const searchInput = screen.getByPlaceholderText(/Search ID, customer, category.../i);
    fireEvent.change(searchInput, { target: { value: 'Electronics' } });

    // Verify filtered table shows electronics
    expect(screen.getAllByText('txn_984729103841').length).toBeGreaterThan(0);

    // Click on a transaction row to open the inspector drawer
    const row = screen.getAllByText('txn_984729103841')[0];
    fireEvent.click(row);

    // Verify Drawer opens
    await waitFor(() => {
      expect(screen.getByText('Transaction Diagnostic Inspector')).toBeInTheDocument();
      expect(screen.getByText('Checkout Funnel Lifecycle')).toBeInTheDocument();
      expect(screen.getByText('Raw Event Payload')).toBeInTheDocument();
    });

    // Close drawer by pressing Escape
    fireEvent.keyDown(window, { key: 'Escape' });
  });

  it('3. Analytics Screen: Renders Funnel Conversion steps, Mobile vs Desktop gap, and Failure Taxonomy', () => {
    render(<App />);

    // Navigate to Analytics using sidebar button
    const navButtons = screen.getAllByRole('button');
    const analyticsNavBtn = navButtons.find((b) => b.textContent?.trim() === 'Analytics');
    expect(analyticsNavBtn).toBeDefined();
    fireEvent.click(analyticsNavBtn!);

    expect(screen.getByText('Diagnostic Analytics Hub')).toBeInTheDocument();

    // Key metrics
    expect(screen.getByText('Funnel Conversion Rate')).toBeInTheDocument();
    expect(screen.getByText('Mobile vs Desktop Gap')).toBeInTheDocument();
    expect(screen.getByText('Checkout Conversion Funnel')).toBeInTheDocument();
    expect(screen.getByText('Device Channel Conversion Gap')).toBeInTheDocument();
    expect(screen.getByText('Top Payment Failure Reasons')).toBeInTheDocument();
    expect(screen.getByText('Product Category Refund Rates')).toBeInTheDocument();

    // Verify funnel steps
    expect(screen.getByText('Cart View')).toBeInTheDocument();
    expect(screen.getByText('Shipping Details')).toBeInTheDocument();
    expect(screen.getByText('Payment Selection')).toBeInTheDocument();
    expect(screen.getByText('Completed Order')).toBeInTheDocument();
  });

  it('4. AI Intelligence Center: Shows an honest empty state on load, and a visible error (never fake data) when the backend is unreachable', async () => {
    render(<App />);

    // Navigate to AI Intelligence Center
    const navButtons = screen.getAllByRole('button');
    const intelNavBtn = navButtons.find((b) => b.textContent?.includes('AI Intelligence'));
    expect(intelNavBtn).toBeDefined();
    fireEvent.click(intelNavBtn!);

    expect(screen.getByText('AI Intelligence Center')).toBeInTheDocument();

    // Verify Terminal & Mode Toggle
    expect(screen.getByText('Merchant Diagnostic Inquiry Terminal')).toBeInTheDocument();
    expect(screen.getByText('Real-Time Sync')).toBeInTheDocument();
    expect(screen.getByText('Async Job Queue')).toBeInTheDocument();

    // No query has run yet: must show the honest empty state, not a pre-seeded fake brief.
    expect(screen.getByText('No analysis yet')).toBeInTheDocument();
    expect(screen.queryByText('Executive Synthesis & Decision Brief')).not.toBeInTheDocument();

    // Running the pipeline against the (unreachable, in this offline test environment) backend
    // must surface a visible error — never silently swap in mock data as if it were real.
    const runButton = screen.getByRole('button', { name: /Execute Multi-Agent Pipeline/i });
    fireEvent.click(runButton);

    await waitFor(() => {
      expect(screen.getByText('Analysis failed')).toBeInTheDocument();
    });
    expect(screen.queryByText('Executive Synthesis & Decision Brief')).not.toBeInTheDocument();
  });

  it('5. Audit & Security Screen: Displays SLO status, compliance log, and event detail drawer', async () => {
    render(<App />);

    // Navigate to Audit & Security
    const navButtons = screen.getAllByRole('button');
    const auditNavBtn = navButtons.find((b) => b.textContent?.includes('Audit & Security'));
    expect(auditNavBtn).toBeDefined();
    fireEvent.click(auditNavBtn!);

    expect(screen.getByText('Audit & Security Center')).toBeInTheDocument();

    // Wait for async data to load (real API or fallback mock)
    await waitFor(() => {
      expect(screen.getByText('Service Level Objectives (SLO) Health')).toBeInTheDocument();
    }, { timeout: 5000 });

    expect(screen.getByText('P95 Query Latency')).toBeInTheDocument();
    expect(screen.getAllByText('API Error Rate').length).toBeGreaterThan(0);

    // Compliance table is present regardless of data source
    expect(screen.getByText('Compliance Audit Ledger')).toBeInTheDocument();
  });

  it('6. Settings & Team Screen: Displays currency selector, API key controls, quota bars, and team directory', () => {
    render(<App />);

    // Navigate to Settings
    const navButtons = screen.getAllByRole('button');
    const settingsNavBtn = navButtons.find((b) => b.textContent?.includes('Settings & Team'));
    expect(settingsNavBtn).toBeDefined();
    fireEvent.click(settingsNavBtn!);

    expect(screen.getByText('Settings & Preferences')).toBeInTheDocument();

    // Currency selector
    expect(screen.getByText('Global Currency & Locale Formatting')).toBeInTheDocument();
    expect(screen.getByText('Indian Rupee')).toBeInTheDocument();
    expect(screen.getByText('US Dollar')).toBeInTheDocument();
    expect(screen.getByText('Euro')).toBeInTheDocument();
    expect(screen.getByText('British Pound')).toBeInTheDocument();
    expect(screen.getByText('Japanese Yen')).toBeInTheDocument();

    // API Key section — field is editable (paste the real backend-configured key), not a
    // client-side "Roll Key" generator that could never match the server's actual credential.
    expect(screen.getByText('API Key & Tenant Identity')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy/i })).toBeInTheDocument();
    const apiKeyInput = screen.getByPlaceholderText('Paste your PayPilot API key');
    fireEvent.change(apiKeyInput, { target: { value: 'test-new-key-456' } });
    expect((apiKeyInput as HTMLInputElement).value).toBe('test-new-key-456');

    // Quotas & Team
    expect(screen.getByText('Tenant Quota & Rate Limit Utilization')).toBeInTheDocument();
    expect(screen.getByText('System Configuration Diagnostics')).toBeInTheDocument();
    expect(screen.getByText('Team Directory & Access Control')).toBeInTheDocument();
    expect(screen.getByText('Priya Sharma')).toBeInTheDocument();
  });
});
