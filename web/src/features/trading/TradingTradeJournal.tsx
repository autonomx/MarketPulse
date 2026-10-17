import { useEffect, useMemo, useState } from 'react';
import { TradingAutomatedReview } from './TradingAutomatedReview';
import {
  tradingPaperAnalyticsApi,
  type AnalyticsNumeric,
  type PaperTradeJournalEntry,
} from './tradingPaperAnalyticsApi';
import './TradingTradeJournal.css';

function symbol(instrumentId: string): string {
  return instrumentId.split(':').at(-1)?.replace('-', '/') ?? instrumentId;
}

function numeric(value: AnalyticsNumeric | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function signed(value: AnalyticsNumeric | null | undefined, digits = 2, suffix = ''): string {
  const parsed = numeric(value);
  if (parsed === null) return '—';
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}${suffix}`;
}

function price(value: AnalyticsNumeric | null | undefined): string {
  const parsed = numeric(value);
  return parsed === null ? '—' : parsed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function label(value: string | null | undefined): string {
  return value ? value.toLowerCase().replaceAll('_', ' ') : '—';
}

function shortId(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length > 22 ? `${value.slice(0, 11)}…${value.slice(-7)}` : value;
}

function dateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function outcomeTone(outcome: PaperTradeJournalEntry['outcome']): string {
  return outcome === 'win' ? 'win' : outcome === 'loss' ? 'loss' : 'flat';
}

function Identity({ label: identityLabel, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="trade-journal-identity">
      <small>{identityLabel}</small>
      <code title={value ?? ''}>{shortId(value)}</code>
    </div>
  );
}

export function TradingTradeJournal({
  accountId,
  instrumentId,
}: {
  accountId: string | null | undefined;
  instrumentId?: string | null;
}) {
  const [entries, setEntries] = useState<PaperTradeJournalEntry[]>([]);
  const [selectedTradeId, setSelectedTradeId] = useState('');
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accountId) {
      setEntries([]);
      setSelectedTradeId('');
      setStatus('idle');
      setError(null);
      return;
    }
    let alive = true;
    setStatus('loading');
    setError(null);
    void tradingPaperAnalyticsApi.journal({ accountId, limit: 100 }).then((response) => {
      if (!alive) return;
      setEntries(response.entries);
      setSelectedTradeId((current) => (
        current && response.entries.some((entry) => entry.trade_id === current)
          ? current
          : response.entries[0]?.trade_id ?? ''
      ));
      setStatus('ready');
    }).catch((reason) => {
      if (!alive) return;
      setEntries([]);
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus('error');
    });
    return () => { alive = false; };
  }, [accountId, refreshToken]);

  const visibleEntries = useMemo(() => (
    selectedOnly && instrumentId
      ? entries.filter((entry) => entry.instrument_id === instrumentId)
      : entries
  ), [entries, instrumentId, selectedOnly]);

  const selected = visibleEntries.find((entry) => entry.trade_id === selectedTradeId)
    ?? visibleEntries[0]
    ?? null;
  const wins = entries.filter((entry) => entry.outcome === 'win').length;
  const losses = entries.filter((entry) => entry.outcome === 'loss').length;
  const pending = entries.filter((entry) => entry.review_state === 'pending').length;

  if (!accountId) {
    return (
      <section className="trading-trade-journal empty" aria-label="Automatic trade journal">
        <strong>Automatic Journal</strong>
        <span>Select a paper account in the Trade tab to review completed canonical trades.</span>
      </section>
    );
  }

  return (
    <section className="trading-trade-journal" aria-label="Automatic trade journal" data-status={status} aria-busy={status === 'loading'}>
      <header className="trade-journal-header">
        <div>
          <strong>Automatic Journal</strong>
          <small>Regenerated from canonical paper trades and immutable lifecycle evidence</small>
        </div>
        <button type="button" onClick={() => setRefreshToken((value) => value + 1)} disabled={status === 'loading'}>
          {status === 'loading' ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      {error ? <div className="trade-journal-error" role="alert">Journal unavailable: {error}</div> : null}

      <div className="trade-journal-summary">
        <div><small>Trades</small><strong>{entries.length}</strong></div>
        <div><small>W / L</small><strong>{wins} / {losses}</strong></div>
        <div><small>Pending review</small><strong>{pending}</strong></div>
      </div>

      {instrumentId ? (
        <label className="trade-journal-scope">
          <input
            type="checkbox"
            checked={selectedOnly}
            onChange={(event) => setSelectedOnly(event.target.checked)}
          />
          <span>Only {symbol(instrumentId)}</span>
        </label>
      ) : null}

      <div role="group" className="trade-journal-list" aria-label="Journal trades">
        {visibleEntries.length ? visibleEntries.map((entry) => (
          <button
            key={entry.trade_id}
            type="button"
            className={entry.trade_id === selected?.trade_id ? 'selected' : undefined}
            aria-pressed={entry.trade_id === selected?.trade_id}
            onClick={() => setSelectedTradeId(entry.trade_id)}
          >
            <span className={`trade-journal-outcome ${outcomeTone(entry.outcome)}`}>{entry.outcome.toUpperCase()}</span>
            <span className="trade-journal-list-main">
              <strong>{symbol(entry.instrument_id)}</strong>
              <small>{dateTime(entry.exit_time)} · {duration(entry.holding_seconds)}</small>
            </span>
            <span className="trade-journal-result">
              <strong>{signed(entry.r_result, 2, 'R')}</strong>
              <small>{signed(entry.realized_pnl, 2)}</small>
            </span>
          </button>
        )) : <div className="trade-journal-empty-row">No completed canonical paper trades in this scope.</div>}
      </div>

      {selected ? (
        <article className="trade-journal-detail" aria-label={`Journal detail for ${symbol(selected.instrument_id)}`}>
          <header>
            <div>
              <strong>{symbol(selected.instrument_id)} · {selected.outcome.toUpperCase()}</strong>
              <small>{selected.session_date} · {label(selected.exit_reason)} · {duration(selected.holding_seconds)}</small>
            </div>
            <span className={`trade-journal-review review-${selected.review_state}`}>{label(selected.review_state)}</span>
          </header>

          <div className="trade-journal-metrics">
            <div><small>Entry</small><strong>{price(selected.average_entry_price)}</strong></div>
            <div><small>Exit</small><strong>{price(selected.average_exit_price)}</strong></div>
            <div><small>Result</small><strong>{signed(selected.r_result, 3, 'R')}</strong></div>
            <div><small>P&amp;L</small><strong>{signed(selected.realized_pnl, 2)}</strong></div>
            <div><small>MAE / MFE</small><strong>{signed(selected.mae_r, 2, 'R')} / {signed(selected.mfe_r, 2, 'R')}</strong></div>
            <div><small>Shortfall</small><strong>{signed(selected.implementation_shortfall_bps, 2, ' bps')}</strong></div>
          </div>

          <section className="trade-journal-observations">
            <header><strong>Automatic observations</strong><span>Factual · deterministic</span></header>
            {selected.automatic_observations.length ? (
              <ul>{selected.automatic_observations.map((observation, index) => <li key={`${index}-${observation}`}>{observation}</li>)}</ul>
            ) : <p>No automatic observations were derivable from the persisted record.</p>}
          </section>

          <TradingAutomatedReview entry={selected} />

          <section className="trade-journal-plan">
            <header><strong>Initial plan &amp; execution</strong></header>
            <div>
              <span><small>Stop</small><strong>{price(selected.initial_stop)}</strong></span>
              <span><small>Target</small><strong>{price(selected.initial_target)}</strong></span>
              <span><small>Initial risk</small><strong>{signed(selected.initial_risk_dollars, 2)}</strong></span>
              <span><small>Signal→exec</small><strong>{signed(selected.signal_to_executable_bps, 2, ' bps')}</strong></span>
              <span><small>Fill slippage</small><strong>{signed(selected.fill_slippage_bps, 2, ' bps')}</strong></span>
              <span><small>Quantity</small><strong>{price(selected.quantity)}</strong></span>
            </div>
          </section>

          <section className="trade-journal-identities">
            <header><strong>Canonical lifecycle</strong><span>trade-lifecycle-v1</span></header>
            <div>
              <Identity label="Trade" value={selected.trade_id} />
              <Identity label="Session" value={selected.session_id} />
              <Identity label="Setup" value={selected.setup_id} />
              <Identity label="Intent" value={selected.trade_intent_id} />
              <Identity label="Risk" value={selected.risk_decision_id} />
              <Identity label="Protection" value={selected.protection_id} />
              <Identity label="Entry order" value={selected.entry_order_id} />
              <Identity label="Exit order" value={selected.exit_order_id} />
            </div>
            <p>
              Entry fills {selected.entry_fill_ids.length}: {selected.entry_fill_ids.map(shortId).join(', ') || '—'} · Exit fills {selected.exit_fill_ids.length}: {selected.exit_fill_ids.map(shortId).join(', ') || '—'}
            </p>
          </section>

          <section className="trade-journal-events">
            <header><strong>Lifecycle evidence</strong><span>{selected.events.length} events</span></header>
            {selected.events.length ? selected.events.map((event) => (
              <div key={event.event_id}>
                <time>{dateTime(event.observed_at)}</time>
                <span><strong>{label(event.event_type)}</strong><small>{label(event.state)}{event.reason_code ? ` · ${label(event.reason_code)}` : ''}</small></span>
              </div>
            )) : <p>No correlated lifecycle events were retained for this trade.</p>}
          </section>
        </article>
      ) : null}

      <footer>
        Read-only projection. Journal content is regenerated from PostgreSQL authority; it cannot place, modify, or authorize an order.
      </footer>
    </section>
  );
}
