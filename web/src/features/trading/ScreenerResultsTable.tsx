import { useMemo, useState } from 'react';
import { screenerRuleLabel, screenerValue, sortScreenerResults, type ScreenerSort } from './screenerRules';
import type { TradingScannerResult, TradingScannerRule } from './scannerTypes';
import { requestWatchlistAdd } from './tradingWatchlistEvents';
import type { WatchlistFlagColor } from './tradingWatchlistModel';
import { TradingWatchlistFlagMenu } from './TradingWatchlistRows';
import { useTradingWatchlistFlags } from './useTradingWatchlistFlags';

function valueText(value: number | null): string {
  if (value === null) return '—';
  const digits = Math.abs(value) >= 1_000 ? 0 : Math.abs(value) >= 1 ? 2 : 4;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** A result's colour flag (TVP-5.1): the same flags as the watchlist's, set from the screener. */
function FlagCell({ symbol, flag, onPick }: { symbol: string; flag: WatchlistFlagColor | undefined; onPick: (color: WatchlistFlagColor | null) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <td className="trading-screener-flag-cell">
      <button type="button" aria-label={flag ? `Flag ${symbol} (flagged)` : `Flag ${symbol}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        <span className={`trading-watchlist-flag-swatch ${flag ?? 'none'}`} aria-hidden="true" />
      </button>
      {open ? <TradingWatchlistFlagMenu symbol={symbol} current={flag} onPick={(color) => { onPick(color); setOpen(false); }} onClose={() => setOpen(false)} /> : null}
    </td>
  );
}

/**
 * A screen's results (TVP-9.1): one column per rule, sortable by any column; a row shows its symbol on the active
 * chart (and so its colour link group), can be flagged like a watchlist symbol, and the results can be added to the
 * open watchlist.
 */
export function ScreenerResultsTable({
  results, rules, added, symbolOf, onShow,
}: {
  results: readonly TradingScannerResult[];
  rules: readonly TradingScannerRule[];
  /** Results new since the previous run (TVP-9.2). */
  added: ReadonlySet<string>;
  symbolOf: (instrumentId: string) => string;
  onShow: (instrumentId: string) => void;
}) {
  const [sort, setSort] = useState<ScreenerSort>({ key: 'rank', direction: 'asc' });
  const [addNote, setAddNote] = useState<string | null>(null);
  const { flags, setFlag } = useTradingWatchlistFlags();
  const sorted = useMemo(() => sortScreenerResults(results, rules, sort), [results, rules, sort]);
  const header = (key: string, label: string) => {
    const active = sort.key === key;
    return (
      <th key={key} aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
        <button type="button" onClick={() => setSort({ key, direction: active && sort.direction === 'desc' ? 'asc' : active ? 'desc' : key === 'symbol' || key === 'rank' ? 'asc' : 'desc' })}>
          {label}{active ? (sort.direction === 'asc' ? ' ▲' : ' ▼') : ''}
        </button>
      </th>
    );
  };
  return (
    <>
      <div className="trading-screener-results-actions">
        <button
          type="button"
          disabled={results.length === 0}
          onClick={() => setAddNote(requestWatchlistAdd(sorted.map((result) => result.instrument_id))
            ? `Added to the open watchlist.`
            : 'Open an editable watchlist (Watchlist tab) to add them.')}
        >
          Add {results.length} to the watchlist
        </button>
        {addNote ? <small role="status">{addNote}</small> : null}
      </div>
      <table className="trading-scanner-results">
        <thead>
          <tr>
            <th aria-label="Flag" />
            {header('rank', 'Rank')}
            {header('symbol', 'Symbol')}
            {rules.map((rule) => header(`rule:${rule.rule_id}`, screenerRuleLabel(rule)))}
            {header('score', 'Score')}
          </tr>
        </thead>
        <tbody>
          {sorted.map((result) => (
            <tr key={`${result.run_id}:${result.instrument_id}`} className={added.has(result.instrument_id) ? 'is-new' : undefined} title={added.has(result.instrument_id) ? 'New since the previous run' : undefined}>
              <FlagCell symbol={symbolOf(result.instrument_id)} flag={flags.get(result.instrument_id)} onPick={(color) => void setFlag([result.instrument_id], color)} />
              <td>{result.rank}</td>
              <td><button type="button" className="trading-screener-symbol" onClick={() => onShow(result.instrument_id)} title="Show on the active chart">{symbolOf(result.instrument_id)}</button></td>
              {rules.map((rule) => <td key={rule.rule_id}>{valueText(screenerValue(result, rule))}</td>)}
              <td>{valueText(Number(result.score))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
