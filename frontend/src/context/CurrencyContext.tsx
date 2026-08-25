import React, { createContext, useContext, useState } from 'react';
import { CurrencyCode, CurrencyConfig } from '../types/api';

export const CURRENCY_CONFIGS: Record<CurrencyCode, CurrencyConfig> = {
  INR: {
    code: 'INR',
    symbol: '₹',
    name: 'Indian Rupee',
    exchangeRateToINR: 1.0,
    locale: 'en-IN',
  },
  USD: {
    code: 'USD',
    symbol: '$',
    name: 'US Dollar',
    exchangeRateToINR: 86.5,
    locale: 'en-US',
  },
  EUR: {
    code: 'EUR',
    symbol: '€',
    name: 'Euro',
    exchangeRateToINR: 92.4,
    locale: 'de-DE',
  },
  GBP: {
    code: 'GBP',
    symbol: '£',
    name: 'British Pound',
    exchangeRateToINR: 108.2,
    locale: 'en-GB',
  },
  JPY: {
    code: 'JPY',
    symbol: '¥',
    name: 'Japanese Yen',
    exchangeRateToINR: 0.58,
    locale: 'ja-JP',
  },
};

interface CurrencyContextType {
  currency: CurrencyCode;
  config: CurrencyConfig;
  setCurrency: (code: CurrencyCode) => void;
  formatCurrency: (amountInINR: number, options?: { compact?: boolean; hideDecimals?: boolean }) => string;
  convertFromINR: (amountInINR: number) => number;
  /** Always true — rates are hardcoded reference constants, not live FX data */
  isStaticRate: true;
}

const CurrencyContext = createContext<CurrencyContextType | undefined>(undefined);

const STORAGE_KEY = 'paypilot_preferred_currency';

export const CurrencyProvider: React.FC<{ children: React.ReactNode; defaultCurrency?: CurrencyCode }> = ({
  children,
  defaultCurrency = 'INR',
}) => {
  const [currency, setCurrencyState] = useState<CurrencyCode>(() => {
    const saved = localStorage.getItem(STORAGE_KEY) as CurrencyCode;
    return saved && CURRENCY_CONFIGS[saved] ? saved : defaultCurrency;
  });

  const config = CURRENCY_CONFIGS[currency];

  const setCurrency = (code: CurrencyCode) => {
    if (CURRENCY_CONFIGS[code]) {
      setCurrencyState(code);
      localStorage.setItem(STORAGE_KEY, code);
    }
  };

  const convertFromINR = (amountInINR: number): number => {
    if (!amountInINR || isNaN(amountInINR)) return 0;
    return amountInINR / config.exchangeRateToINR;
  };

  const formatCurrency = (
    amountInINR: number,
    options?: { compact?: boolean; hideDecimals?: boolean }
  ): string => {
    if (amountInINR === undefined || amountInINR === null || isNaN(amountInINR)) {
      amountInINR = 0;
    }
    const converted = convertFromINR(amountInINR);

    try {
      const isJPY = currency === 'JPY';
      const maximumFractionDigits = options?.hideDecimals || isJPY ? 0 : (options?.compact ? 1 : 2);
      const minimumFractionDigits = options?.hideDecimals || isJPY ? 0 : (options?.compact ? 0 : 2);

      const formatter = new Intl.NumberFormat(config.locale, {
        style: 'currency',
        currency: config.code,
        notation: options?.compact ? 'compact' : 'standard',
        maximumFractionDigits,
        minimumFractionDigits,
      });

      return formatter.format(converted);
    } catch {
      return `${config.symbol}${converted.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
    }
  };

  return (
    <CurrencyContext.Provider
      value={{
        currency,
        config,
        setCurrency,
        formatCurrency,
        convertFromINR,
        isStaticRate: true,
      }}
    >
      {children}
    </CurrencyContext.Provider>
  );
};

export const useCurrency = (): CurrencyContextType => {
  const context = useContext(CurrencyContext);
  if (!context) {
    throw new Error('useCurrency must be used within a CurrencyProvider');
  }
  return context;
};
