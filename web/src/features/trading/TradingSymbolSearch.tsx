import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react';
import type { CanonicalInstrument } from './tradingTypes';
import type { TradingFormula } from './tradingFormula';
import { returnKeyboardToChart } from './commands/chartKeyContext';
import './TradingSymbolSearch.css';

export type SymbolSearchCategory =
  | 'all'
  | 'stocks'
  | 'funds'
  | 'futures'
  | 'forex'
  | 'crypto'
  | 'indices'
  | 'bonds'
  | 'economy'
  | 'options';

export type TradingFormulaSearchPreview = {
  formula: TradingFormula;
  unresolvedSymbols: string[];
  loading?: boolean;
};

type CategoryDefinition = {
  id: SymbolSearchCategory;
  label: string;
};

const categories: CategoryDefinition[] = [
  { id: 'all', label: 'All' },
  { id: 'stocks', label: 'Stocks' },
  { id: 'funds', label: 'Funds' },
  { id: 'futures', label: 'Futures' },
  { id: 'forex', label: 'Forex' },
  { id: 'crypto', label: 'Crypto' },
  { id: 'indices', label: 'Indices' },
  { id: 'bonds', label: 'Bonds' },
  { id: 'economy', label: 'Economy' },
  { id: 'options', label: 'Options' },
];

const currencyNames: Record<string, string> = {
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  USD: 'US Dollar',
  USDT: 'Tether',
  USDC: 'USD Coin',
};

const equityNames: Record<string, string> = {
  AAPL: 'Apple Inc.',
  NVDA: 'NVIDIA Corporation',
  TSLA: 'Tesla, Inc.',
  SPY: 'SPDR S&P 500 ETF Trust',
};

const commodityNames: Record<string, string> = {
  COPPER: 'Copper Futures',
  NATGAS: 'Natural Gas Futures',
  UKOIL: 'Brent Crude Oil',
  USOIL: 'WTI Crude Oil',
  XAGUSD: 'Silver Futures',
  XAUUSD: 'Gold Futures',
};

function categoryForInstrument(instrument: CanonicalInstrument): SymbolSearchCategory {
  if (instrument.asset_class === 'economic') return 'economy';
  if (instrument.asset_class === 'crypto') return 'crypto';
  if (instrument.instrument_type === 'index') return 'indices';
  if (instrument.instrument_type === 'perpetual' || instrument.asset_class === 'commodity') return 'futures';
  if (instrument.asset_class === 'forex') return 'forex';
  if (instrument.asset_class === 'equity') return 'stocks';
  return 'all';
}

function categoryMatches(instrument: CanonicalInstrument, category: SymbolSearchCategory): boolean {
  return category === 'all' || categoryForInstrument(instrument) === category;
}

function instrumentMatches(instrument: CanonicalInstrument, query: string): boolean {
  const normalized = query.trim().toUpperCase();
  if (!normalized) return true;
  return [
    instrument.display_symbol,
    instrument.venue_symbol,
    instrument.venue,
    instrument.instrument_id,
    instrument.base_currency,
    instrument.quote_currency,
    instrument.name,
  ].some((value) => value?.toUpperCase().includes(normalized));
}

function instrumentName(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'economic') return instrument.name ?? `${instrument.display_symbol} · FRED`;
  if (instrument.venue === 'CRYPTOCAP') {
    return instrument.display_symbol.endsWith('.D')
      ? `Market Cap ${instrument.display_symbol.slice(0, -2)} Dominance, %`
      : `Crypto Market Cap ${instrument.display_symbol}`;
  }
  if (instrument.asset_class === 'crypto') {
    const base = currencyNames[instrument.base_currency ?? ''] ?? instrument.base_currency ?? instrument.display_symbol;
    const quote = currencyNames[instrument.quote_currency ?? ''] ?? instrument.quote_currency ?? 'USD';
    return `${base} / ${quote}`;
  }
  if (instrument.asset_class === 'commodity') {
    return commodityNames[instrument.display_symbol] ?? `${instrument.display_symbol} · ${instrument.venue}`;
  }
  return equityNames[instrument.display_symbol] ?? `${instrument.display_symbol} · ${instrument.venue}`;
}

function instrumentTypeLabel(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'economic') return 'economic';
  if (instrument.venue === 'CRYPTOCAP') return 'index crypto';
  if (instrument.asset_class === 'crypto') {
    return instrument.instrument_type === 'perpetual' ? 'perpetual crypto' : 'spot crypto';
  }
  if (instrument.instrument_type === 'index') return 'index';
  if (instrument.asset_class === 'forex') return 'forex';
  if (instrument.asset_class === 'commodity') return 'commodity';
  return 'stock';
}

function venueLabel(venue: string): string {
  return venue.toLowerCase() === 'nasdaq' ? 'NASDAQ' : venue;
}

function assetGlyph(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'economic') return '%';
  if (instrument.asset_class === 'crypto') return '₿';
  if (instrument.instrument_type === 'index') return '⌁';
  return instrument.display_symbol.slice(0, 1);
}

function uniqueInstruments(instruments: readonly CanonicalInstrument[]): CanonicalInstrument[] {
  return [...new Map(instruments.map((instrument) => [instrument.instrument_id, instrument])).values()];
}

export function TradingSymbolSearch({
  open,
  query,
  instruments,
  activeInstrumentId,
  loading,
  formulaPreview,
  onQueryChange,
  onSelect,
  onSelectFormula,
  onClose,
  selectQueryOnOpen = true,
}: {
  open: boolean;
  query: string;
  /** Select the query when the search opens, so typing replaces it. False keeps typed text and puts the caret after it. */
  selectQueryOnOpen?: boolean;
  instruments: readonly CanonicalInstrument[];
  activeInstrumentId: string;
  loading?: boolean;
  formulaPreview?: TradingFormulaSearchPreview | null;
  onQueryChange: (query: string) => void;
  onSelect: (instrument: CanonicalInstrument) => void;
  onSelectFormula?: () => void;
  onClose: () => void;
}) {
  const [category, setCategory] = useState<SymbolSearchCategory>('all');
  const inputRef = useRef<HTMLInputElement>(null);

  // Focuses the query when the search opens (not when the selection mode changes); returns whether it took focus at
  // once: typed text keeps arriving, so it does unless the query is to be selected.
  const focusInput = useEffectEvent((atOnce: boolean) => {
    if (atOnce && selectQueryOnOpen) return false;
    const input = inputRef.current;
    input?.focus();
    if (selectQueryOnOpen) input?.select();
    else input?.setSelectionRange(input.value.length, input.value.length);
    return true;
  });
  useEffect(() => {
    if (!open) return;
    setCategory('all');
    focusInput(true);
    const frame = window.requestAnimationFrame(() => focusInput(false));
    return () => {
      window.cancelAnimationFrame(frame);
      // Choosing a symbol (or closing the search) is a chart action: chart keys work again without a click.
      returnKeyboardToChart();
    };
  }, [open]);

  const results = useMemo(() => {
    const normalizedQuery = query.trim().toUpperCase();
    if (formulaPreview) return [];
    return uniqueInstruments(instruments)
      .filter((instrument) => categoryMatches(instrument, category) && instrumentMatches(instrument, query))
      .sort((left, right) => {
        if (!normalizedQuery) return left.display_symbol.localeCompare(right.display_symbol);
        const leftSymbol = left.display_symbol.toUpperCase();
        const rightSymbol = right.display_symbol.toUpperCase();
        const leftExact = leftSymbol === normalizedQuery ? 0 : leftSymbol.startsWith(normalizedQuery) ? 1 : 2;
        const rightExact = rightSymbol === normalizedQuery ? 0 : rightSymbol.startsWith(normalizedQuery) ? 1 : 2;
        return leftExact - rightExact || leftSymbol.localeCompare(rightSymbol) || left.venue.localeCompare(right.venue);
      });
  }, [category, formulaPreview, instruments, query]);

  if (!open) return null;

  return (
    <div
      className="trading-symbol-search-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="trading-symbol-search-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trading-symbol-search-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="trading-symbol-search-header">
          <h2 id="trading-symbol-search-title">Symbol search</h2>
          <button type="button" className="trading-symbol-search-close" aria-label="Close symbol search" onClick={onClose}>×</button>
        </header>

        <div className="trading-symbol-search-form">
          <span className="trading-symbol-search-icon" aria-hidden="true" />
          <input
            ref={inputRef}
            aria-label="Search symbols"
            placeholder="Search stocks, crypto, and more"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') onClose();
              if (event.key === 'Enter') {
                if (formulaPreview && !formulaPreview.loading) onSelectFormula?.();
                else if (results[0]) onSelect(results[0]);
              }
            }}
          />
          {query ? (
            <button type="button" className="trading-symbol-search-clear" aria-label="Clear symbol search" onClick={() => onQueryChange('')}>×</button>
          ) : null}
          <span className="trading-symbol-search-shortcut" aria-hidden="true">⌘ K</span>
        </div>

        <nav className="trading-symbol-search-categories" aria-label="Symbol categories">
          {categories.map((item) => (
            <button
              key={item.id}
              type="button"
              className={category === item.id ? 'active' : undefined}
              aria-pressed={category === item.id}
              onClick={() => setCategory(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="trading-symbol-search-results-wrap">
          <div className="trading-symbol-search-result-heading" aria-hidden="true">
            <span>Symbol</span>
            <span>Description</span>
            <span>Exchange</span>
          </div>
          {formulaPreview ? (
            <div className="trading-symbol-search-formula-result">
              <button
                type="button"
                disabled={Boolean(formulaPreview.loading) || !onSelectFormula}
                onClick={onSelectFormula}
                aria-label={`Create arithmetic chart ${formulaPreview.formula.expression}`}
              >
                <span className="trading-symbol-search-avatar formula" aria-hidden="true">ƒx</span>
                <span className="trading-symbol-search-symbol">
                  <strong>{formulaPreview.formula.expression}</strong>
                  <small>{formulaPreview.formula.symbols.length} symbol{formulaPreview.formula.symbols.length === 1 ? '' : 's'}</small>
                </span>
                <span className="trading-symbol-search-description">
                  <strong>Arithmetic chart</strong>
                  <small>{formulaPreview.loading ? 'Resolving symbols…' : 'Derived from aligned market data'}</small>
                </span>
                <span className="trading-symbol-search-exchange">
                  <small>{formulaPreview.unresolvedSymbols.length ? 'Resolve on chart' : 'Ready'}</small>
                  <strong>{formulaPreview.unresolvedSymbols.length ? formulaPreview.unresolvedSymbols.join(', ') : 'Derived'}</strong>
                </span>
              </button>
              {formulaPreview.unresolvedSymbols.length ? (
                <p>Could not resolve: {formulaPreview.unresolvedSymbols.join(', ')}</p>
              ) : null}
            </div>
          ) : results.length ? (
            <ul className="trading-symbol-search-results" role="listbox" aria-label="Symbol search results">
              {results.map((instrument) => (
                <li key={instrument.instrument_id} role="option" aria-selected={instrument.instrument_id === activeInstrumentId}>
                  <button type="button" onClick={() => onSelect(instrument)}>
                    <span className={`trading-symbol-search-avatar ${instrument.asset_class}`} aria-hidden="true">{assetGlyph(instrument)}</span>
                    <span className="trading-symbol-search-symbol">
                      <strong>{instrument.display_symbol}</strong>
                      <small>{instrument.venue_symbol}</small>
                    </span>
                    <span className="trading-symbol-search-description">
                      <strong>{instrumentName(instrument)}</strong>
                      <small>{instrumentTypeLabel(instrument)}</small>
                    </span>
                    <span className="trading-symbol-search-exchange">
                      <small>{instrumentTypeLabel(instrument)}</small>
                      <strong>{venueLabel(instrument.venue)}</strong>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="trading-symbol-search-empty">
              <span className="trading-symbol-search-empty-icon" aria-hidden="true">⌕</span>
              <strong>No symbols found</strong>
              <p>Try a ticker, company name, or exchange symbol.</p>
            </div>
          )}
        </div>

        <footer className="trading-symbol-search-footer">
          <span>{loading ? 'Searching instrument catalog…' : formulaPreview ? '1 result' : `${results.length} ${results.length === 1 ? 'result' : 'results'}`}</span>
          <span>Search by symbol, name, or exchange</span>
        </footer>
      </section>
    </div>
  );
}

export function symbolSearchInstrumentName(instrument: CanonicalInstrument): string {
  return instrumentName(instrument);
}
