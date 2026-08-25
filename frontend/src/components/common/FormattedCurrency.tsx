import React from 'react';
import { useCurrency } from '../../context/CurrencyContext';

interface FormattedCurrencyProps {
  amountInINR: number;
  compact?: boolean;
  hideDecimals?: boolean;
  className?: string;
  prefix?: string;
  suffix?: string;
}

export const FormattedCurrency: React.FC<FormattedCurrencyProps> = ({
  amountInINR,
  compact = false,
  hideDecimals = false,
  className = '',
  prefix = '',
  suffix = '',
}) => {
  const { formatCurrency } = useCurrency();
  const formatted = formatCurrency(amountInINR, { compact, hideDecimals });

  return (
    <span className={`font-mono font-medium tracking-tight ${className}`}>
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
};
