import React, { useState, useMemo } from 'react';
import {
  Search,
  Download,
  Eye,
  AlertCircle,
  RotateCcw,
  Smartphone,
  Monitor,
  Tablet,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { Badge } from '../components/common/Badge';
import { Drawer } from '../components/common/Drawer';
import { FormattedCurrency } from '../components/common/FormattedCurrency';
import { MOCK_TRANSACTIONS } from '../api/mockData';
import { TransactionRecord } from '../types/api';

export const TransactionsPage: React.FC = () => {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [methodFilter, setMethodFilter] = useState<string>('ALL');
  const [deviceFilter, setDeviceFilter] = useState<string>('ALL');
  const [selectedTxn, setSelectedTxn] = useState<TransactionRecord | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 8;

  // Filtered transactions
  const filteredTransactions = useMemo(() => {
    return MOCK_TRANSACTIONS.filter((txn) => {
      // Search
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchesId = txn.transaction_id.toLowerCase().includes(q);
        const matchesCustomer = txn.customer_id.toLowerCase().includes(q);
        const matchesCategory = txn.product_category.toLowerCase().includes(q);
        if (!matchesId && !matchesCustomer && !matchesCategory) return false;
      }
      // Status
      if (statusFilter !== 'ALL' && txn.payment_status !== statusFilter) return false;
      // Method
      if (methodFilter !== 'ALL' && txn.payment_method !== methodFilter) return false;
      // Device
      if (deviceFilter !== 'ALL' && txn.device_type !== deviceFilter) return false;

      return true;
    });
  }, [searchQuery, statusFilter, methodFilter, deviceFilter]);

  const totalPages = Math.ceil(filteredTransactions.length / itemsPerPage) || 1;
  const paginatedTransactions = filteredTransactions.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage
  );

  const exportCSV = () => {
    const headers = ['Transaction ID', 'Timestamp', 'Customer', 'Category', 'Method', 'Amount (INR)', 'Status', 'Failure Reason', 'Device'];
    const rows = filteredTransactions.map((t) => [
      t.transaction_id,
      t.timestamp,
      t.customer_id,
      t.product_category,
      t.payment_method,
      t.amount,
      t.payment_status,
      t.failure_reason,
      t.device_type,
    ]);

    const csvContent =
      'data:text/csv;charset=utf-8,' +
      [headers.join(','), ...rows.map((e) => e.join(','))].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `paypilot_transactions_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl lg:text-3xl font-extrabold text-slate-900 tracking-tight">
            Transactions Ledger
          </h2>
          <p className="text-xs sm:text-sm font-medium text-slate-500 mt-1">
            Search, filter, and inspect detailed payment events, checkout states, and failure diagnostics.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Button
            variant="secondary"
            size="sm"
            onClick={exportCSV}
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Export CSV
          </Button>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <Card className="p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Search */}
          <div className="relative">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setCurrentPage(1);
              }}
              placeholder="Search ID, customer, category..."
              className="w-full bg-slate-50 border border-slate-200 rounded-lg pl-9 pr-3 py-2 text-xs font-medium text-slate-800 placeholder-slate-400 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </div>

          {/* Status Filter */}
          <div>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-medium text-slate-800 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="ALL">All Payment Statuses</option>
              <option value="SUCCESS">Success</option>
              <option value="FAILED">Failed</option>
              <option value="REFUNDED">Refunded</option>
            </select>
          </div>

          {/* Payment Method Filter */}
          <div>
            <select
              value={methodFilter}
              onChange={(e) => {
                setMethodFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-medium text-slate-800 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="ALL">All Payment Methods</option>
              <option value="UPI">UPI</option>
              <option value="Credit_Card">Credit Card</option>
              <option value="Debit_Card">Debit Card</option>
              <option value="Net_Banking">Net Banking</option>
              <option value="Wallet">Wallet</option>
            </select>
          </div>

          {/* Device Type Filter */}
          <div>
            <select
              value={deviceFilter}
              onChange={(e) => {
                setDeviceFilter(e.target.value);
                setCurrentPage(1);
              }}
              className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-medium text-slate-800 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            >
              <option value="ALL">All Devices</option>
              <option value="Mobile">Mobile Web / App</option>
              <option value="Desktop">Desktop Browser</option>
              <option value="Tablet">Tablet</option>
            </select>
          </div>
        </div>

        {/* Filter Summary & Reset */}
        <div className="flex justify-between items-center text-xs text-slate-500 pt-2 border-t border-slate-100">
          <div>
            Showing <strong className="text-slate-900">{filteredTransactions.length}</strong> matching transactions
          </div>
          {(statusFilter !== 'ALL' || methodFilter !== 'ALL' || deviceFilter !== 'ALL' || searchQuery) && (
            <button
              onClick={() => {
                setStatusFilter('ALL');
                setMethodFilter('ALL');
                setDeviceFilter('ALL');
                setSearchQuery('');
                setCurrentPage(1);
              }}
              className="text-primary hover:underline font-semibold flex items-center gap-1"
            >
              <RotateCcw className="w-3 h-3" />
              Reset Filters
            </button>
          )}
        </div>
      </Card>

      {/* Transactions Table */}
      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="bg-slate-50/80 border-b border-slate-200 text-slate-500 font-bold uppercase tracking-wider">
                <th className="py-3.5 pl-5">Transaction ID</th>
                <th className="py-3.5">Timestamp</th>
                <th className="py-3.5">Customer</th>
                <th className="py-3.5">Category</th>
                <th className="py-3.5">Method</th>
                <th className="py-3.5">Amount</th>
                <th className="py-3.5">Status</th>
                <th className="py-3.5">Failure Reason</th>
                <th className="py-3.5">Device</th>
                <th className="py-3.5 pr-5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 text-slate-700">
              {paginatedTransactions.length === 0 ? (
                <tr>
                  <td colSpan={10} className="py-12 text-center text-slate-400">
                    No transactions found matching the selected filters.
                  </td>
                </tr>
              ) : (
                paginatedTransactions.map((txn) => (
                  <tr
                    key={txn.transaction_id}
                    onClick={() => setSelectedTxn(txn)}
                    className="hover:bg-emerald-50/40 transition-colors cursor-pointer"
                  >
                    <td className="py-3.5 pl-5 font-mono font-bold text-slate-900">
                      {txn.transaction_id}
                    </td>
                    <td className="py-3.5 text-slate-500">{txn.timestamp}</td>
                    <td className="py-3.5 font-mono text-slate-600">{txn.customer_id}</td>
                    <td className="py-3.5">
                      <span className="px-2 py-0.5 rounded bg-slate-100 text-slate-700 font-medium">
                        {txn.product_category}
                      </span>
                    </td>
                    <td className="py-3.5 font-semibold text-slate-800">
                      {txn.payment_method.replace('_', ' ')}
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
                    <td className="py-3.5">
                      {txn.failure_reason !== 'None' ? (
                        <span className="text-red-600 font-mono text-[11px] font-semibold">
                          {txn.failure_reason}
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="py-3.5">
                      <span className="inline-flex items-center gap-1 text-slate-500">
                        {txn.device_type === 'Mobile' ? (
                          <Smartphone className="w-3.5 h-3.5" />
                        ) : txn.device_type === 'Desktop' ? (
                          <Monitor className="w-3.5 h-3.5" />
                        ) : (
                          <Tablet className="w-3.5 h-3.5" />
                        )}
                        {txn.device_type}
                      </span>
                    </td>
                    <td className="py-3.5 pr-5 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedTxn(txn);
                        }}
                        leftIcon={<Eye className="w-3.5 h-3.5" />}
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

        {/* Pagination Bar */}
        <div className="flex items-center justify-between px-5 py-3.5 border-t border-slate-100 bg-slate-50/50 text-xs text-slate-500">
          <div>
            Page <strong className="text-slate-900">{currentPage}</strong> of{' '}
            <strong className="text-slate-900">{totalPages}</strong>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              leftIcon={<ChevronLeft className="w-3.5 h-3.5" />}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              rightIcon={<ChevronRight className="w-3.5 h-3.5" />}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      {/* Transaction Detail Slide-Over Drawer */}
      <Drawer
        isOpen={!!selectedTxn}
        onClose={() => setSelectedTxn(null)}
        title="Transaction Diagnostic Inspector"
        subtitle={selectedTxn ? `ID: ${selectedTxn.transaction_id}` : ''}
        width="lg"
      >
        {selectedTxn && (
          <div className="space-y-6">
            {/* Top Summary Banner */}
            <div className="bg-slate-50 rounded-xl p-4 border border-slate-200 flex items-center justify-between">
              <div>
                <p className="text-xs text-slate-400 font-bold uppercase">Total Amount</p>
                <h4 className="text-2xl font-extrabold text-slate-900 mt-0.5">
                  <FormattedCurrency amountInINR={selectedTxn.amount} />
                </h4>
              </div>
              <Badge
                variant={
                  selectedTxn.payment_status === 'SUCCESS'
                    ? 'success'
                    : selectedTxn.payment_status === 'FAILED'
                    ? 'error'
                    : 'warning'
                }
                size="md"
              >
                {selectedTxn.payment_status}
              </Badge>
            </div>

            {/* Core Metadata Grid */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
                Transaction Metadata
              </h4>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Merchant ID</span>
                  <span className="font-mono font-bold text-slate-800">{selectedTxn.merchant_id}</span>
                </div>
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Customer ID</span>
                  <span className="font-mono font-bold text-slate-800">{selectedTxn.customer_id}</span>
                </div>
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Payment Method</span>
                  <span className="font-bold text-slate-800">{selectedTxn.payment_method.replace('_', ' ')}</span>
                </div>
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Product Category</span>
                  <span className="font-bold text-slate-800">{selectedTxn.product_category}</span>
                </div>
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Device & Channel</span>
                  <span className="font-bold text-slate-800">{selectedTxn.device_type}</span>
                </div>
                <div className="p-3 bg-white border border-slate-100 rounded-lg">
                  <span className="text-slate-400 block font-medium">Timestamp</span>
                  <span className="font-mono font-medium text-slate-800">{selectedTxn.timestamp}</span>
                </div>
              </div>
            </div>

            {/* Checkout Funnel Progress */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">
                Checkout Funnel Lifecycle
              </h4>
              <div className="flex items-center justify-between relative px-2">
                {['CART', 'SHIPPING', 'PAYMENT', 'PAYMENT_COMPLETED'].map((step, idx) => {
                  const isCurrent = selectedTxn.checkout_step_reached === step;
                  const isPassed =
                    selectedTxn.payment_status === 'SUCCESS' ||
                    (step === 'CART') ||
                    (step === 'SHIPPING' && selectedTxn.checkout_step_reached !== 'CART') ||
                    (step === 'PAYMENT' && selectedTxn.checkout_step_reached === 'PAYMENT_COMPLETED');

                  return (
                    <div key={step} className="flex flex-col items-center z-10">
                      <div
                        className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${
                          isCurrent && selectedTxn.payment_status === 'FAILED'
                            ? 'bg-red-500 text-white'
                            : isPassed
                            ? 'bg-emerald-500 text-white'
                            : 'bg-slate-200 text-slate-500'
                        }`}
                      >
                        {idx + 1}
                      </div>
                      <span className="text-[10px] font-semibold text-slate-600 mt-1">
                        {step.replace('_', ' ')}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Failure Analysis (if failed) */}
            {selectedTxn.payment_status === 'FAILED' && (
              <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-xs">
                <div className="flex items-center gap-2 text-red-700 font-bold mb-1">
                  <AlertCircle className="w-4 h-4" />
                  <span>Failure Diagnostic Code: {selectedTxn.failure_reason}</span>
                </div>
                <p className="text-red-600 mt-1">
                  This transaction dropped at the <strong>{selectedTxn.checkout_step_reached}</strong> stage. PayPilot AI recommends activating secondary rail failover to recapture similar drop-offs.
                </p>
              </div>
            )}

            {/* Raw JSON Payload */}
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 mb-2">
                Raw Event Payload
              </h4>
              <pre className="bg-slate-900 text-slate-200 p-4 rounded-xl text-[11px] font-mono overflow-x-auto">
                {JSON.stringify(selectedTxn, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
};
