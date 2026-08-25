import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CurrencyProvider, useCurrency } from '../context/CurrencyContext';
import { FormattedCurrency } from '../components/common/FormattedCurrency';

const TestCurrencyConsumer: React.FC = () => {
  const { currency, setCurrency, convertFromINR, formatCurrency, config } = useCurrency();
  return (
    <div>
      <span data-testid="current-currency">{currency}</span>
      <span data-testid="currency-symbol">{config.symbol}</span>
      <span data-testid="converted-val">{convertFromINR(8650).toFixed(2)}</span>
      <span data-testid="formatted-val">{formatCurrency(8650)}</span>
      <div data-testid="formatted-component">
        <FormattedCurrency amountInINR={8650} />
      </div>
      <button onClick={() => setCurrency('USD')} data-testid="btn-usd">USD</button>
      <button onClick={() => setCurrency('EUR')} data-testid="btn-eur">EUR</button>
      <button onClick={() => setCurrency('GBP')} data-testid="btn-gbp">GBP</button>
      <button onClick={() => setCurrency('JPY')} data-testid="btn-jpy">JPY</button>
      <button onClick={() => setCurrency('INR')} data-testid="btn-inr">INR</button>
    </div>
  );
};

describe('Global Multi-Currency Engine', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('initializes with default INR currency and correct exchange calculation', () => {
    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    expect(screen.getByTestId('current-currency').textContent).toBe('INR');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('₹');
    expect(screen.getByTestId('converted-val').textContent).toBe('8650.00');
    expect(screen.getByTestId('formatted-val').textContent).toContain('8,650');
  });

  it('switches to USD ($) dynamically with correct exchange conversion (1 USD = 86.5 INR)', () => {
    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    fireEvent.click(screen.getByTestId('btn-usd'));

    expect(screen.getByTestId('current-currency').textContent).toBe('USD');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('$');
    // 8650 INR / 86.5 = 100.00 USD
    expect(screen.getByTestId('converted-val').textContent).toBe('100.00');
    expect(screen.getByTestId('formatted-val').textContent).toContain('100.00');
    expect(localStorage.getItem('paypilot_preferred_currency')).toBe('USD');
  });

  it('switches to EUR (€) dynamically with correct exchange conversion (1 EUR = 92.4 INR)', () => {
    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    fireEvent.click(screen.getByTestId('btn-eur'));

    expect(screen.getByTestId('current-currency').textContent).toBe('EUR');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('€');
    // 8650 INR / 92.4 ≈ 93.61 EUR
    expect(Number(screen.getByTestId('converted-val').textContent)).toBeCloseTo(93.61, 1);
  });

  it('switches to GBP (£) dynamically with correct exchange conversion (1 GBP = 108.2 INR)', () => {
    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    fireEvent.click(screen.getByTestId('btn-gbp'));

    expect(screen.getByTestId('current-currency').textContent).toBe('GBP');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('£');
    // 8650 INR / 108.2 ≈ 79.94 GBP
    expect(Number(screen.getByTestId('converted-val').textContent)).toBeCloseTo(79.94, 1);
  });

  it('switches to JPY (¥) dynamically with integer formatting (1 JPY = 0.58 INR)', () => {
    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    fireEvent.click(screen.getByTestId('btn-jpy'));

    expect(screen.getByTestId('current-currency').textContent).toBe('JPY');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('¥');
    // 8650 INR / 0.58 ≈ 14913.79 JPY
    expect(Number(screen.getByTestId('converted-val').textContent)).toBeCloseTo(14913.79, 1);
  });

  it('persists selected currency across page reload via localStorage', () => {
    localStorage.setItem('paypilot_preferred_currency', 'EUR');

    render(
      <CurrencyProvider defaultCurrency="INR">
        <TestCurrencyConsumer />
      </CurrencyProvider>
    );

    expect(screen.getByTestId('current-currency').textContent).toBe('EUR');
    expect(screen.getByTestId('currency-symbol').textContent).toBe('€');
  });
});
