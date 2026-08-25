import React from 'react';

export const LoadingSkeleton: React.FC<{ rows?: number; className?: string }> = ({
  rows = 4,
  className = '',
}) => {
  return (
    <div className={`animate-pulse space-y-3 ${className}`}>
      <div className="h-6 bg-slate-200 rounded-md w-1/3 mb-4" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 bg-slate-100 rounded-md w-full" />
      ))}
    </div>
  );
};
