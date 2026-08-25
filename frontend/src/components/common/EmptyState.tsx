import React from 'react';
import { Inbox, AlertTriangle } from 'lucide-react';
import { Button } from './Button';

interface EmptyStateProps {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title = 'No Data Available',
  description = 'There are no records matching your current filter criteria.',
  icon,
  actionLabel,
  onAction,
}) => {
  return (
    <div className="flex flex-col items-center justify-center p-10 text-center bg-slate-50/50 rounded-xl border border-dashed border-slate-300">
      <div className="p-3 bg-white rounded-full shadow-sm text-slate-400 mb-3">
        {icon || <Inbox className="w-8 h-8 text-slate-400" />}
      </div>
      <h4 className="text-base font-bold text-slate-800 mb-1">{title}</h4>
      <p className="text-sm text-slate-500 max-w-sm mb-4">{description}</p>
      {actionLabel && onAction && (
        <Button variant="secondary" size="sm" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
};

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = 'Something went wrong',
  message = 'An unexpected error occurred while communicating with PayPilot services.',
  onRetry,
}) => {
  return (
    <div className="flex flex-col items-center justify-center p-8 text-center bg-red-50/50 rounded-xl border border-red-200">
      <div className="p-3 bg-red-100 rounded-full text-red-600 mb-3">
        <AlertTriangle className="w-6 h-6" />
      </div>
      <h4 className="text-base font-bold text-red-900 mb-1">{title}</h4>
      <p className="text-sm text-red-700 max-w-md mb-4">{message}</p>
      {onRetry && (
        <Button variant="danger" size="sm" onClick={onRetry}>
          Try Again
        </Button>
      )}
    </div>
  );
};
