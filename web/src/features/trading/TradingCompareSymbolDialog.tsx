import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { tradingApi } from './tradingApi';
import type { CanonicalInstrument } from './tradingTypes';
import type { TradingComparisonPlacement } from './tradingComparisons';
import './TradingSymbolSearch.css';
import './TradingCompareSymbolDialog.css';

function instrumentName(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'crypto') {
    return `${instrument.base_currency ?? instrument.display_symbol} / ${instrument.quote_currency ?? 'USD'}`;
  }
  return instrument.display_symbol;
}

function assetGlyph(instrument: CanonicalInstrument): string {
  if (instrument.asset_class === 'crypto') return '₿';
  if (instrument.instrument_type === 'index') return '⌁';
  return instrument.display_symbol.slice(0, 1);
}

const recentStorageKey = 'omnix.trading.compare-recent';

function readRecent(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(recentStorageKey) ?? '[]') as unknown;
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').slice(0, 12) : [];
  } catch {
    return [];
  }
}

function rememberRecent(instrumentId: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(recentStorageKey, JSON.stringify([instrumentId, ...readRecent().filter((id) => id !== instrumentId)].slice(0, 12)));
  } catch {
    // Local storage can be disabled in private browsing; comparison still works.
  }
}

export function TradingCompareSymbolDialog({
  open,
  currentInstrumentId,
  existingInstrumentIds,
  mode = 'compare',
  onAdd,
  onChoose,
  onClose,
}: {
  open: boolean;
  currentInstrumentId: string;
  existingInstrumentIds: readonly string[];
  /** `choose` picks one symbol (an indicator's compare symbol) without a placement. */
  mode?: 'compare' | 'choose';
  onAdd?: (instrument: CanonicalInstrument, placement: TradingComparisonPlacement) => void;
  onChoose?: (instrument: CanonicalInstrument) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<CanonicalInstrument | null>(null);
  const [recent, setRecent] = useState<string[]>(readRecent);
  const inputRef = useRef<HTMLInputElement>(null);
  const instrumentsQuery = useQuery({
    queryKey: ['trading', 'compare-instruments', query.trim()],
    queryFn: () => tradingApi.instruments(query.trim()),
    enabled: open,
    staleTime: 30_000,
  });

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setSelected(null);
    setRecent(readRecent());
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  const results = useMemo(() => {
    const normalized = query.trim().toUpperCase();
    const unique = [...new Map((instrumentsQuery.data ?? []).map((item) => [item.instrument_id, item])).values()]
      .filter((item) => item.instrument_id !== currentInstrumentId)
      .filter((item) => !normalized || [item.display_symbol, item.venue_symbol, item.venue, item.instrument_id]
        .some((value) => value?.toUpperCase().includes(normalized)));
    const recentOrder = new Map(recent.map((id, index) => [id, index]));
    return unique.sort((left, right) => {
      if (!normalized) return (recentOrder.get(left.instrument_id) ?? 999) - (recentOrder.get(right.instrument_id) ?? 999)
        || left.display_symbol.localeCompare(right.display_symbol);
      // Stablecoin crypto pairs generally have the longest continuous history
      // in the catalog. Keep the canonical Binance USDT result ahead of the
      // newer USD pair when a user searches for a base symbol such as BTC.
      const quoteRank = (instrument: CanonicalInstrument) => instrument.asset_class !== 'crypto'
        ? 2
        : instrument.quote_currency === 'USDT' ? 0
          : instrument.quote_currency === 'USD' ? 1
            : 2;
      return quoteRank(left) - quoteRank(right)
        || left.display_symbol.localeCompare(right.display_symbol)
        || left.venue.localeCompare(right.venue);
    });
  }, [currentInstrumentId, instrumentsQuery.data, query, recent]);

  const choose = (instrument: CanonicalInstrument) => {
    rememberRecent(instrument.instrument_id);
    if (mode === 'choose') {
      onChoose?.(instrument);
      onClose();
      return;
    }
    setSelected(instrument);
    setRecent((items) => [instrument.instrument_id, ...items.filter((id) => id !== instrument.instrument_id)].slice(0, 12));
  };

  const add = (placement: TradingComparisonPlacement) => {
    if (!selected) return;
    onAdd?.(selected, placement);
    onClose();
  };

  if (!open) return null;
  // On the page itself: inside the chart panel its stacking context would leave the dialog under the toolbars.
  return createPortal(
    <div className="trading-symbol-search-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="trading-symbol-search-dialog trading-compare-dialog" role="dialog" aria-modal="true" aria-labelledby="trading-compare-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="trading-symbol-search-header">
          <h2 id="trading-compare-title">{mode === 'choose' ? 'Choose symbol' : 'Compare symbols'}</h2>
          <button type="button" className="trading-symbol-search-close" aria-label="Close compare symbols" onClick={onClose}>×</button>
        </header>
        <div className="trading-symbol-search-form">
          <span className="trading-symbol-search-icon" aria-hidden="true" />
          <input
            ref={inputRef}
            aria-label="Search symbols to compare"
            placeholder="Symbol, ISIN, or CUSIP"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setSelected(null); }}
            onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}
          />
          {query ? <button type="button" className="trading-symbol-search-clear" aria-label="Clear compare search" onClick={() => { setQuery(''); setSelected(null); }}>×</button> : null}
        </div>
        {selected ? (
          <div className="trading-compare-selected" aria-live="polite">
            <div><strong>{selected.display_symbol}</strong><span>{instrumentName(selected)} · {selected.venue}</span></div>
            <button type="button" onClick={() => setSelected(null)}>Choose another</button>
          </div>
        ) : null}
        <div className="trading-symbol-search-results-wrap">
          <div className="trading-compare-heading">{query.trim() ? 'Search results' : 'Recent symbols'}</div>
          {results.length ? (
            <ul className="trading-symbol-search-results" role="listbox" aria-label="Compare symbol results">
              {results.map((instrument) => {
                const alreadyAdded = existingInstrumentIds.includes(instrument.instrument_id);
                return (
                  <li key={instrument.instrument_id} role="option" aria-selected={selected?.instrument_id === instrument.instrument_id}>
                    <button type="button" disabled={alreadyAdded} onClick={() => choose(instrument)}>
                      <span className={`trading-symbol-search-avatar ${instrument.asset_class}`} aria-hidden="true">{assetGlyph(instrument)}</span>
                      <span className="trading-symbol-search-symbol"><strong>{instrument.display_symbol}</strong><small>{instrument.venue_symbol}</small></span>
                      <span className="trading-symbol-search-description"><strong>{instrumentName(instrument)}</strong><small>{instrument.instrument_type}</small></span>
                      <span className="trading-symbol-search-exchange"><small>{alreadyAdded ? 'already compared' : instrument.instrument_type}</small><strong>{instrument.venue}</strong></span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="trading-symbol-search-empty"><span className="trading-symbol-search-empty-icon" aria-hidden="true">⌕</span><strong>No symbols found</strong><p>Try a ticker, company name, or exchange.</p></div>
          )}
        </div>
        {mode === 'compare' ? <div role="group" className="trading-compare-placement" aria-label="Comparison placement">
          <span>Place selected symbol</span>
          <div>
            <button type="button" disabled={!selected} onClick={() => add('percent')}>Same % scale</button>
            <button type="button" disabled={!selected} onClick={() => add('price-scale')}>New price scale</button>
            <button type="button" disabled={!selected} onClick={() => add('pane')}>New pane</button>
          </div>
        </div> : null}
        <footer className="trading-symbol-search-footer"><span>{instrumentsQuery.isFetching ? 'Searching instrument catalog…' : `${results.length} ${results.length === 1 ? 'symbol' : 'symbols'}`}</span><span>{mode === 'choose' ? 'Select a symbol' : 'Select a symbol, then choose its scale'}</span></footer>
      </section>
    </div>,
    document.body,
  );
}
