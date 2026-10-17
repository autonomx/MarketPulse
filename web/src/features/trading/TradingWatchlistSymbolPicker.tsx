import { useEffect, useMemo, useRef, useState } from 'react';
import { binanceInstrumentIdFor } from './cryptoInstrumentDefaults';
import { tradingApi } from './tradingApi';
import type { CanonicalInstrument } from './tradingTypes';
import { watchlistLogoIdentity } from './tradingWatchlistPresentation';
import './TradingSymbolSearch.css';
import './TradingWatchlistSymbolPicker.css';

type PickerCategory =
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

const categories: Array<{ id: PickerCategory; label: string }> = [
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

function categoryForInstrument(instrument: CanonicalInstrument): PickerCategory {
  if (instrument.asset_class === 'economic') return 'economy';
  if (instrument.asset_class === 'crypto') return 'crypto';
  if (instrument.instrument_type === 'index') return 'indices';
  if (instrument.instrument_type === 'perpetual' || instrument.asset_class === 'commodity') return 'futures';
  if (instrument.asset_class === 'forex') return 'forex';
  if (instrument.asset_class === 'equity') return 'stocks';
  return 'all';
}

function categoryMatches(instrument: CanonicalInstrument, category: PickerCategory): boolean {
  return category === 'all' || categoryForInstrument(instrument) === category;
}

function locallyMatches(instrument: CanonicalInstrument, query: string): boolean {
  const normalized = query.trim().toUpperCase();
  if (!normalized) return true;
  return [
    instrument.display_symbol,
    instrument.venue_symbol,
    instrument.venue,
    instrument.instrument_id,
    instrument.base_currency,
    instrument.quote_currency,
    instrumentName(instrument),
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

function assetGlyph(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'economic') return '%';
  if (instrument.asset_class === 'crypto') return '₿';
  if (instrument.instrument_type === 'index') return '⌁';
  return instrument.display_symbol.slice(0, 1);
}

function venueGlyph(venue: string): string {
  return venue.slice(0, 1).toUpperCase() || '•';
}

function uniqueInstruments(instruments: readonly CanonicalInstrument[]): CanonicalInstrument[] {
  return [...new Map(instruments.map((instrument) => [instrument.instrument_id, instrument])).values()];
}

function sortResults(instruments: CanonicalInstrument[], query: string): CanonicalInstrument[] {
  const normalized = query.trim().toUpperCase();
  return [...instruments].sort((left, right) => {
    if (!normalized) return left.display_symbol.localeCompare(right.display_symbol);
    const leftSymbol = left.display_symbol.toUpperCase();
    const rightSymbol = right.display_symbol.toUpperCase();
    const leftRank = leftSymbol === normalized ? 0 : leftSymbol.startsWith(normalized) ? 1 : 2;
    const rightRank = rightSymbol === normalized ? 0 : rightSymbol.startsWith(normalized) ? 1 : 2;
    return leftRank - rightRank || leftSymbol.localeCompare(rightSymbol) || left.venue.localeCompare(right.venue);
  });
}

export function TradingWatchlistSymbolPicker({
  open,
  instruments,
  selectedInstrumentIds,
  busy = false,
  onAdd,
  onClose,
}: {
  open: boolean;
  instruments: readonly CanonicalInstrument[];
  selectedInstrumentIds: readonly string[];
  busy?: boolean;
  onAdd: (instrument: CanonicalInstrument) => void | Promise<void>;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<PickerCategory>('all');
  const [remoteResults, setRemoteResults] = useState<CanonicalInstrument[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setCategory('all');
    setRemoteResults([]);
    setLoading(false);
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const clean = query.trim();
    if (clean.length < 2) {
      setRemoteResults([]);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void tradingApi.instruments(clean).then((matches) => {
        if (!cancelled) {
          setRemoteResults(matches);
          setLoading(false);
        }
      }).catch(() => {
        if (!cancelled) {
          setRemoteResults([]);
          setLoading(false);
        }
      });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const selectedIds = useMemo(
    () => new Set(selectedInstrumentIds.map(binanceInstrumentIdFor)),
    [selectedInstrumentIds],
  );
  const results = useMemo(() => {
    const local = instruments.filter((instrument) => (
      categoryMatches(instrument, category) && locallyMatches(instrument, query)
    ));
    const remote = remoteResults.filter((instrument) => categoryMatches(instrument, category));
    return sortResults(uniqueInstruments([...remote, ...local]), query);
  }, [category, instruments, query, remoteResults]);

  if (!open) return null;

  const firstAddable = results.find((instrument) => !selectedIds.has(binanceInstrumentIdFor(instrument.instrument_id)));

  const addSymbol = async (instrument: CanonicalInstrument, closeAfterAdd = false) => {
    await onAdd(instrument);
    if (closeAfterAdd) onClose();
  };

  return (
    <div
      className="trading-symbol-search-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="trading-symbol-search-dialog trading-watchlist-symbol-picker-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trading-watchlist-symbol-picker-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="trading-symbol-search-header">
          <h2 id="trading-watchlist-symbol-picker-title">Add symbol</h2>
          <button type="button" className="trading-symbol-search-close" aria-label="Close add symbol" onClick={onClose}>×</button>
        </header>

        <div className="trading-symbol-search-form">
          <span className="trading-symbol-search-icon" aria-hidden="true" />
          <input
            ref={inputRef}
            aria-label="Search symbols to add"
            placeholder="Symbol, ISIN, or CUSIP"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') onClose();
              if (event.key === 'Enter' && firstAddable && !busy) void addSymbol(firstAddable, event.shiftKey);
            }}
          />
          {query ? (
            <button type="button" className="trading-symbol-search-clear" aria-label="Clear symbol search" onClick={() => setQuery('')}>×</button>
          ) : null}
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
          <div className="trading-watchlist-symbol-picker-heading" aria-hidden="true">
            <span className="symbol-heading">Symbol</span>
            <span>Description</span>
            <span>Exchange</span>
            <span />
          </div>
          {results.length ? (
            <ul className="trading-watchlist-symbol-picker-results" aria-label="Symbols available to add">
              {results.map((instrument) => {
                const normalizedId = binanceInstrumentIdFor(instrument.instrument_id);
                const alreadyAdded = selectedIds.has(normalizedId);
                return (
                  <li key={instrument.instrument_id}>
                    <span className={`trading-watchlist-symbol-picker-avatar ${watchlistLogoIdentity(instrument.display_symbol, instrument.instrument_id).kind}`} aria-hidden="true">{assetGlyph(instrument)}</span>
                    <span className="trading-watchlist-symbol-picker-symbol">
                      <strong>{instrument.display_symbol}</strong>
                      <small>{instrument.venue_symbol}</small>
                    </span>
                    <span className="trading-watchlist-symbol-picker-description">
                      <strong>{instrumentName(instrument)}</strong>
                      <small>{instrumentTypeLabel(instrument)}</small>
                    </span>
                    <span className="trading-watchlist-symbol-picker-exchange">
                      <small>{instrumentTypeLabel(instrument)}</small>
                      <span className="trading-watchlist-symbol-picker-venue">
                        <strong>{instrument.venue}</strong>
                        <span aria-hidden="true">{venueGlyph(instrument.venue)}</span>
                      </span>
                    </span>
                    <button
                      type="button"
                      className="trading-watchlist-symbol-picker-add"
                      aria-label={alreadyAdded
                        ? `${instrument.display_symbol} already in watchlist`
                        : `Add ${instrument.display_symbol} to watchlist`}
                      title={alreadyAdded ? 'Already in watchlist' : 'Add to watchlist'}
                      disabled={alreadyAdded || busy}
                      onClick={(event) => void addSymbol(instrument, event.shiftKey)}
                    >
                      {alreadyAdded ? '✓' : '+'}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="trading-symbol-search-empty">
              <span className="trading-symbol-search-empty-icon" aria-hidden="true">⌕</span>
              <strong>{loading ? 'Searching…' : 'No symbols found'}</strong>
              <p>{loading ? 'Checking the instrument catalog.' : 'Try a ticker, company name, or exchange symbol.'}</p>
            </div>
          )}
        </div>

        <footer className="trading-symbol-search-footer">
          <span>{loading ? 'Searching instrument catalog…' : `${results.length} ${results.length === 1 ? 'result' : 'results'}`}</span>
          <span>Shift + Click or Shift + Enter to add symbol and close dialog</span>
        </footer>
      </section>
    </div>
  );
}
