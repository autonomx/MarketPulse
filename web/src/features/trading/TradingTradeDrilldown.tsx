import { useEffect, useState } from 'react';
import { TradingAutomatedReview } from './TradingAutomatedReview';
import {
  tradingPaperAnalyticsApi,
  type AnalyticsNumeric,
  type PaperAnalyticsTrade,
  type PaperTradeJournalEntry,
} from './tradingPaperAnalyticsApi';
import './TradingTradeDrilldown.css';

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

function money(value: AnalyticsNumeric | null | undefined, currency: string): string {
  const parsed = numeric(value);
  if (parsed === null) return '—';
  const absolute = Math.abs(parsed).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${parsed > 0 ? '+' : parsed < 0 ? '-' : ''}${absolute} ${currency}`;
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
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function time(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function duration(entryTime: string, exitTime: string): string {
  const milliseconds = new Date(exitTime).getTime() - new Date(entryTime).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—';
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
}

function Identity({ name, value }: { name: string; value: string | null | undefined }) {
  return (
    <div className="paper-trade-drilldown-identity">
      <small>{name}</small>
      <code title={value ?? ''}>{shortId(value)}</code>
    </div>
  );
}

function SetupFeatures({ features }: { features: Record<string, unknown> }) {
  const rows = Object.entries(features).filter(([, value]) => (
    value !== null && value !== undefined && typeof value !== 'object'
  )).slice(0, 12);
  if (!rows.length) return <p className="paper-trade-drilldown-empty">No scalar setup features were retained for this outcome.</p>;
  return (
    <div className="paper-trade-drilldown-features">
      {rows.map(([name, value]) => (
        <span key={name}><small>{label(name)}</small><strong>{String(value)}</strong></span>
      ))}
    </div>
  );
}

export function TradingTradeDrilldown({
  trade,
  accountId,
  currency,
  onClose,
}: {
  trade: PaperAnalyticsTrade;
  accountId: string;
  currency: string;
  onClose: () => void;
}) {
  const [entry, setEntry] = useState<PaperTradeJournalEntry | null>(null);
  const [status, setStatus] = useState<'shadow' | 'loading' | 'ready' | 'missing' | 'error'>(
    trade.source === 'auto_paper' ? 'loading' : 'shadow',
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  useEffect(() => {
    setEntry(null);
    setError(null);
    if (trade.source !== 'auto_paper') {
      setStatus('shadow');
      return;
    }
    let alive = true;
    setStatus('loading');
    void tradingPaperAnalyticsApi.journal({
      accountId,
      strategyId: trade.strategy_id,
      epochId: trade.epoch_id ?? null,
      startDate: trade.session_date,
      endDate: trade.session_date,
      limit: 200,
    }).then((response) => {
      if (!alive) return;
      const exact = response.entries.find((candidate) => candidate.trade_id === trade.trade_id) ?? null;
      setEntry(exact);
      setStatus(exact ? 'ready' : 'missing');
    }).catch((reason) => {
      if (!alive) return;
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus('error');
    });
    return () => { alive = false; };
  }, [accountId, trade]);

  const positive = Number(trade.r_result) >= 0;

  return (
    <div className="paper-trade-drilldown-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <aside
        className="paper-trade-drilldown"
        role="dialog"
        aria-modal="true"
        aria-busy={status === 'loading'}
        aria-label={`Trade drill-down for ${symbol(trade.instrument_id)}`}
      >
        <header className="paper-trade-drilldown-header">
          <div>
            <span className={trade.source === 'auto_paper' ? 'auto' : 'shadow'}>{trade.source === 'auto_paper' ? 'AUTO PAPER' : 'SHADOW'}</span>
            <strong>{symbol(trade.instrument_id)} trade evidence</strong>
            <small>{trade.session_date} · {time(trade.entry_time)} → {time(trade.exit_time)}</small>
          </div>
          <button type="button" autoFocus aria-label="Close trade drill-down" onClick={onClose}>×</button>
        </header>

        <section className="paper-trade-drilldown-summary">
          <div><small>Result</small><strong className={positive ? 'positive' : 'negative'}>{signed(trade.r_result, 3, 'R')}</strong></div>
          <div><small>P&amp;L</small><strong className={Number(trade.realized_pnl ?? 0) >= 0 ? 'positive' : 'negative'}>{trade.realized_pnl == null ? '—' : money(trade.realized_pnl, currency)}</strong></div>
          <div><small>Quantity</small><strong>{trade.quantity ?? '—'}</strong></div>
          <div><small>Duration</small><strong>{duration(trade.entry_time, trade.exit_time)}</strong></div>
          <div><small>MAE / MFE</small><strong>{signed(trade.mae_r, 2, 'R')} / {signed(trade.mfe_r, 2, 'R')}</strong></div>
          <div><small>Exit</small><strong>{label(trade.exit_reason)}</strong></div>
        </section>

        <section className="paper-trade-drilldown-section">
          <header><strong>Expected → observed → realized</strong><span>Persisted analytics</span></header>
          <div className="paper-trade-drilldown-kpis">
            <span><small>Initial stop</small><strong>{price(trade.initial_stop)}</strong></span>
            <span><small>Initial target</small><strong>{price(trade.initial_target)}</strong></span>
            <span><small>Signal → executable</small><strong>{signed(trade.signal_to_executable_bps, 2, ' bps')}</strong></span>
            <span><small>Fill slippage</small><strong>{signed(trade.fill_slippage_bps, 2, ' bps')}</strong></span>
            <span><small>Total shortfall</small><strong>{signed(trade.implementation_shortfall_bps, 2, ' bps')}</strong></span>
          </div>
        </section>

        {status === 'shadow' ? (
          <>
            <section className="paper-trade-drilldown-notice shadow">
              <strong>Prospective SHADOW outcome</strong>
              <p>This row is frozen replay evidence, not a broker-style order lifecycle. No paper order, fill, protection, or risk-decision identity is implied.</p>
            </section>
            <section className="paper-trade-drilldown-section">
              <header><strong>Setup evidence</strong><span>Frozen outcome features</span></header>
              <SetupFeatures features={trade.setup_features} />
            </section>
          </>
        ) : null}

        {status === 'loading' ? <div className="paper-trade-drilldown-loading" role="status">Resolving canonical lifecycle evidence…</div> : null}
        {status === 'missing' ? (
          <section className="paper-trade-drilldown-notice warning">
            <strong>Canonical detail not found</strong>
            <p>The analytics row exists, but the exact trade ID was not returned by the journal scope. The dashboard will not infer missing lifecycle evidence.</p>
          </section>
        ) : null}
        {status === 'error' ? (
          <section className="paper-trade-drilldown-notice warning" role="alert">
            <strong>Journal detail unavailable</strong>
            <p>{error}</p>
          </section>
        ) : null}

        {entry ? (
          <>
            <section className="paper-trade-drilldown-section">
              <header><strong>Canonical execution</strong><span>{label(entry.review_state)}</span></header>
              <div className="paper-trade-drilldown-kpis">
                <span><small>Entry</small><strong>{price(entry.average_entry_price)}</strong></span>
                <span><small>Exit</small><strong>{price(entry.average_exit_price)}</strong></span>
                <span><small>Initial risk</small><strong>{money(entry.initial_risk_dollars, currency)}</strong></span>
                <span><small>Lifecycle</small><strong>{label(entry.lifecycle_state)}</strong></span>
                <span><small>Outcome</small><strong>{entry.outcome.toUpperCase()}</strong></span>
              </div>
            </section>

            <section className="paper-trade-drilldown-section">
              <header><strong>Automatic observations</strong><span>Factual · deterministic</span></header>
              {entry.automatic_observations.length ? (
                <ul className="paper-trade-drilldown-observations">
                  {entry.automatic_observations.map((observation, index) => <li key={`${index}-${observation}`}>{observation}</li>)}
                </ul>
              ) : <p className="paper-trade-drilldown-empty">No deterministic observations were derivable from the persisted trade.</p>}
            </section>

            <div className="paper-trade-drilldown-review">
              <TradingAutomatedReview entry={entry} />
            </div>

            <section className="paper-trade-drilldown-section">
              <header><strong>Canonical lifecycle</strong><span>trade-lifecycle-v1</span></header>
              <div className="paper-trade-drilldown-identities">
                <Identity name="Trade" value={entry.trade_id} />
                <Identity name="Session" value={entry.session_id} />
                <Identity name="Setup" value={entry.setup_id} />
                <Identity name="Intent" value={entry.trade_intent_id} />
                <Identity name="Risk" value={entry.risk_decision_id} />
                <Identity name="Protection" value={entry.protection_id} />
                <Identity name="Entry order" value={entry.entry_order_id} />
                <Identity name="Exit order" value={entry.exit_order_id} />
              </div>
              <p className="paper-trade-drilldown-fills">
                Entry fills {entry.entry_fill_ids.length}: {entry.entry_fill_ids.map(shortId).join(', ') || '—'}<br />
                Exit fills {entry.exit_fill_ids.length}: {entry.exit_fill_ids.map(shortId).join(', ') || '—'}
              </p>
            </section>

            <section className="paper-trade-drilldown-section">
              <header><strong>Lifecycle evidence</strong><span>{entry.events.length} events</span></header>
              <div className="paper-trade-drilldown-events">
                {entry.events.length ? entry.events.map((event) => (
                  <div key={event.event_id}>
                    <time>{time(event.observed_at)}</time>
                    <span><strong>{label(event.event_type)}</strong><small>{label(event.state)}{event.reason_code ? ` · ${label(event.reason_code)}` : ''}</small></span>
                  </div>
                )) : <p className="paper-trade-drilldown-empty">No correlated lifecycle events were retained for this trade.</p>}
              </div>
            </section>
          </>
        ) : null}

        <footer>Read-only evidence. This view cannot place, modify, cancel, or authorize an order.</footer>
      </aside>
    </div>
  );
}
