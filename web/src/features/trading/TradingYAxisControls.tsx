/* eslint-disable react-hooks/exhaustive-deps -- baseline WP-9.x */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { TradingPriceScaleSide } from './chart/chartAdapter';
import './TradingYAxisControls.css';

type CurrencyOption = { code: string; name: string; flag: string };

const currencyOptions: CurrencyOption[] = [
  { code: 'USD', name: 'US Dollar', flag: '🇺🇸' },
  { code: 'CAD', name: 'Canadian Dollar', flag: '🇨🇦' },
  { code: 'EUR', name: 'Euro', flag: '🇪🇺' },
  { code: 'GBP', name: 'British Pound', flag: '🇬🇧' },
  { code: 'JPY', name: 'Japanese Yen', flag: '🇯🇵' },
  { code: 'AUD', name: 'Australian Dollar', flag: '🇦🇺' },
  { code: 'CHF', name: 'Swiss Franc', flag: '🇨🇭' },
  { code: 'CNY', name: 'Chinese Yuan', flag: '🇨🇳' },
  { code: 'HKD', name: 'Hong Kong Dollar', flag: '🇭🇰' },
  { code: 'INR', name: 'Indian Rupee', flag: '🇮🇳' },
  { code: 'BRL', name: 'Brazilian Real', flag: '🇧🇷' },
  { code: 'MXN', name: 'Mexican Peso', flag: '🇲🇽' },
  { code: 'NZD', name: 'New Zealand Dollar', flag: '🇳🇿' },
  { code: 'SGD', name: 'Singapore Dollar', flag: '🇸🇬' },
  { code: 'AED', name: 'United Arab Emirates Dirham', flag: '🇦🇪' },
  { code: 'BTC', name: 'Bitcoin', flag: '₿' },
  { code: 'ETH', name: 'Ethereum', flag: 'Ξ' },
  { code: 'USDT', name: 'Tether', flag: '₮' },
  { code: 'USDC', name: 'USD Coin', flag: '$' },
];

export function TradingYAxisControls({
  side,
  currency,
  logarithmic,
  autoScale,
  visible,
  onCurrencyChange,
  onAutoFit,
  onToggleLogarithmic,
}: {
  side: TradingPriceScaleSide;
  currency: string;
  logarithmic: boolean;
  autoScale: boolean;
  visible: boolean;
  onCurrencyChange: (currency: string) => void;
  onAutoFit: () => void;
  onToggleLogarithmic: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [currencyOpen, setCurrencyOpen] = useState(false);
  const [query, setQuery] = useState('');
  const normalizedCurrency = currency.toUpperCase();
  const selected = currencyOptions.find((option) => option.code === normalizedCurrency)
    ?? { code: normalizedCurrency, name: normalizedCurrency, flag: '¤' };
  const options = useMemo(() => {
    const available = currencyOptions.some((option) => option.code === normalizedCurrency)
      ? currencyOptions
      : [selected, ...currencyOptions];
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return available;
    return available.filter((option) => `${option.code} ${option.name}`.toLowerCase().includes(normalizedQuery));
  }, [normalizedCurrency, query, selected]);

  useEffect(() => {
    if (!currencyOpen) {
      setQuery('');
      return undefined;
    }
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && rootRef.current?.contains(event.target)) return;
      setCurrencyOpen(false);
    };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCurrencyOpen(false);
    };
    window.addEventListener('pointerdown', close);
    window.addEventListener('keydown', keydown);
    return () => {
      window.removeEventListener('pointerdown', close);
      window.removeEventListener('keydown', keydown);
    };
  }, [currencyOpen]);

  return (
    <div
      ref={rootRef}
      className={`trading-y-axis-controls is-${side}`}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div className="trading-y-axis-currency-row">
        <button
          type="button"
          className="trading-y-axis-currency-trigger"
          aria-label="Y-axis currency"
          aria-expanded={currencyOpen}
          onClick={() => setCurrencyOpen((value) => !value)}
        >
          <span>{selected.code}</span>
          <span aria-hidden="true">⌄</span>
        </button>
      </div>
      <div className={`trading-y-axis-actions${visible ? ' is-visible' : ''}`} role="toolbar" aria-label="Y-axis scale controls">
        <button
          type="button"
          className={`trading-y-axis-action${autoScale ? ' is-active' : ''}`}
          aria-label="Auto fit y-axis"
          aria-pressed={autoScale}
          title="Auto fit y-axis"
          onClick={onAutoFit}
        >
          A
        </button>
        <button
          type="button"
          className={`trading-y-axis-action${logarithmic ? ' is-active' : ''}`}
          aria-label="Toggle logarithmic y-axis"
          aria-pressed={logarithmic}
          title="Toggle logarithmic y-axis"
          onClick={onToggleLogarithmic}
        >
          L
        </button>
      </div>
      {currencyOpen ? (
        <div className="trading-y-axis-currency-menu" role="listbox" aria-label="Y-axis currencies">
          <div className="trading-y-axis-currency-heading">CURRENCIES</div>
          <label className="trading-y-axis-currency-search">
            <span aria-hidden="true">⌕</span>
            <input
              autoFocus
              value={query}
              aria-label="Search currencies"
              placeholder="Search"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="trading-y-axis-currency-options">
            {options.map((option) => (
              <button
                key={option.code}
                type="button"
                role="option"
                aria-selected={option.code === normalizedCurrency}
                className={option.code === normalizedCurrency ? 'is-selected' : undefined}
                onClick={() => {
                  onCurrencyChange(option.code);
                  setCurrencyOpen(false);
                }}
              >
                <span className="trading-y-axis-currency-flag" aria-hidden="true">{option.flag}</span>
                <span><strong>{option.code}</strong><small>{option.name}</small></span>
              </button>
            ))}
            {options.length === 0 ? <div className="trading-y-axis-currency-empty">No currencies found</div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
