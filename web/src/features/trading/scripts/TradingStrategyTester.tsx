/**
 * The strategy tester (TVP-11.5): a strategy() script's backtest on the chart's bars, or a deep backtest over all the
 * history the provider serves. Overview (equity, buy and hold, drawdown), performance summary (all, long, short),
 * list of trades and properties. A research backtest only: the script trades a simulated account and never places
 * an order, live or paper.
 */
import { useMemo, useState, useSyncExternalStore } from 'react';
import type { CoreIndicatorInstance } from '../indicators/coreIndicators';
import {
  isScriptIndicatorId, loadScriptSource, scriptIdOf, scriptIndicatorName, scriptInputValues, scriptRunStatus, scriptRunStatusVersion, subscribeScriptRunStatus,
} from './scriptIndicators';
import { scriptsApi, type StrategyReport, type StrategySummary } from './scriptsApi';
import './TradingStrategyTester.css';
import { PRODUCT_NAME } from '../../../shared/productIdentity';

type Tab = 'overview' | 'summary' | 'trades' | 'properties';
type Shown = { report: StrategyReport; times: string[]; source: 'chart' | 'deep' };

const money = (value: number | null | undefined) => (value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 2 }));
const percent = (value: number | null | undefined) => (value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value.toFixed(2)}%`);
const signed = (value: number) => (value > 0 ? 'is-up' : value < 0 ? 'is-down' : '');
const when = (time: number | null) => (time === null ? '—' : new Date(time).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }));

/** Rows of the performance summary: label, key, and how it reads. */
const SUMMARY_ROWS: Array<[string, string, 'money' | 'percent' | 'count' | 'ratio']> = [
  ['Net profit', 'net_profit', 'money'], ['Gross profit', 'gross_profit', 'money'], ['Gross loss', 'gross_loss', 'money'],
  ['Profit factor', 'profit_factor', 'ratio'], ['Open P&L', 'open_pl', 'money'], ['Total closed trades', 'total_closed_trades', 'count'],
  ['Winning trades', 'winning_trades', 'count'], ['Losing trades', 'losing_trades', 'count'], ['Percent profitable', 'percent_profitable', 'percent'],
  ['Avg trade', 'avg_trade', 'money'], ['Avg trade %', 'avg_trade_percent', 'percent'], ['Avg winning trade', 'avg_winning_trade', 'money'],
  ['Avg losing trade', 'avg_losing_trade', 'money'], ['Ratio avg win / avg loss', 'ratio_avg_win_loss', 'ratio'],
  ['Largest winning trade', 'largest_winning_trade', 'money'], ['Largest losing trade', 'largest_losing_trade', 'money'],
  ['Avg # bars in trades', 'avg_bars_in_trades', 'ratio'], ['Avg # bars in winning trades', 'avg_bars_in_winning_trades', 'ratio'],
  ['Avg # bars in losing trades', 'avg_bars_in_losing_trades', 'ratio'],
];

function cell(summary: StrategySummary, key: string, kind: 'money' | 'percent' | 'count' | 'ratio'): string {
  const value = summary[key];
  if (value === null || value === undefined) return '—';
  return kind === 'money' ? money(value) : kind === 'percent' ? percent(value) : kind === 'count' ? String(value) : value.toFixed(2);
}

/** A line through values on a fixed 600×120 box (the chart scales it). */
function path(values: readonly number[], min: number, max: number): string {
  if (values.length === 0) return '';
  const span = max - min || 1;
  return values.map((value, index) => `${index === 0 ? 'M' : 'L'}${((index / Math.max(1, values.length - 1)) * 600).toFixed(1)},${(120 - ((value - min) / span) * 120).toFixed(1)}`).join(' ');
}

function Overview({ report }: { report: StrategyReport }) {
  const all = report.summary.all;
  const values = [...report.equity, ...report.buy_hold];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const deepest = Math.min(0, ...report.drawdown);
  return (
    <div className="trading-strategy-overview">
      <dl className="trading-strategy-headline">
        <div><dt>Net profit</dt><dd className={signed(all.net_profit ?? 0)}>{money(all.net_profit)} <small>{percent(all.net_profit_percent)}</small></dd></div>
        <div><dt>Total closed trades</dt><dd>{all.total_closed_trades ?? 0}</dd></div>
        <div><dt>Percent profitable</dt><dd>{percent(all.percent_profitable)}</dd></div>
        <div><dt>Profit factor</dt><dd>{all.profit_factor === null || all.profit_factor === undefined ? '—' : all.profit_factor.toFixed(3)}</dd></div>
        <div><dt>Max drawdown</dt><dd className="is-down">{money(all.max_drawdown)} <small>{percent(all.max_drawdown_percent)}</small></dd></div>
        <div><dt>Avg trade</dt><dd>{money(all.avg_trade)}</dd></div>
        <div><dt>Buy &amp; hold return</dt><dd className={signed(all.buy_hold_return ?? 0)}>{money(all.buy_hold_return)} <small>{percent(all.buy_hold_return_percent)}</small></dd></div>
        <div><dt>Sharpe / Sortino</dt><dd>{all.sharpe_ratio === null || all.sharpe_ratio === undefined ? '—' : all.sharpe_ratio.toFixed(3)} / {all.sortino_ratio === null || all.sortino_ratio === undefined ? '—' : all.sortino_ratio.toFixed(3)}</dd></div>
      </dl>
      <svg className="trading-strategy-chart" viewBox="0 0 600 120" preserveAspectRatio="none" role="img" aria-label="Equity and buy and hold">
        <path className="is-buy-hold" d={path(report.buy_hold, min, max)} />
        <path className="is-equity" d={path(report.equity, min, max)} />
      </svg>
      <svg className="trading-strategy-chart is-drawdown" viewBox="0 0 600 120" preserveAspectRatio="none" role="img" aria-label="Drawdown">
        <path d={`${path(report.drawdown, deepest, 0)} L600,0 L0,0 Z`} />
      </svg>
    </div>
  );
}

function Trades({ report }: { report: StrategyReport }) {
  const rows = [...report.open_trades.map((trade) => ({ ...trade, open: true })), ...[...report.trades].reverse().map((trade) => ({ ...trade, open: false }))];
  return (
    <div className="trading-strategy-table-scroll">
      {report.trades_total > report.trades.length ? <p className="trading-strategy-note">The newest {report.trades.length} of {report.trades_total} trades.</p> : null}
      <table aria-label="List of trades">
        <thead><tr><th>#</th><th>Type</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Size</th><th>Profit</th><th>Cum. profit</th><th>Run-up</th><th>Drawdown</th></tr></thead>
        <tbody>
          {rows.map((trade) => (
            <tr key={`${trade.open ? 'open' : 'closed'}-${trade.number}`}>
              <td>{trade.number}</td>
              <td>{trade.direction === 'long' ? 'Long' : 'Short'}{trade.open ? ' (open)' : ''}</td>
              <td>{trade.entry_id}{trade.exit_id ? ` → ${trade.exit_id}` : ''}</td>
              <td>{when(trade.entry_time)}<br /><small>{money(trade.entry_price)}</small></td>
              <td>{when(trade.exit_time)}<br /><small>{money(trade.exit_price)}</small></td>
              <td>{Number(trade.qty.toFixed(4))}</td>
              <td className={signed(trade.profit)}>{money(trade.profit)}<br /><small>{percent(trade.profit_percent)}</small></td>
              <td>{trade.open ? '—' : money(trade.cum_profit)}</td>
              <td>{money(trade.runup)}</td>
              <td>{money(trade.drawdown)}</td>
            </tr>
          ))}
          {rows.length === 0 ? <tr><td colSpan={10}>No trades on these bars.</td></tr> : null}
        </tbody>
      </table>
    </div>
  );
}

export function TradingStrategyTester({
  indicators,
  instrumentId,
  bindingId,
  interval,
}: {
  indicators: readonly CoreIndicatorInstance[];
  instrumentId: string;
  bindingId: string | null;
  interval: string;
}) {
  // Re-render when a chart run reports (statuses are kept outside React).
  const version = useSyncExternalStore(subscribeScriptRunStatus, scriptRunStatusVersion);
  const strategies = useMemo(() => indicators
    .filter((indicator) => indicator.enabled && isScriptIndicatorId(String(indicator.id)))
    .map((indicator) => ({ indicator, status: scriptRunStatus(scriptIdOf(String(indicator.id)) ?? '') }))
    .filter((item) => item.status?.strategy && version >= 0), [indicators, version]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [deep, setDeep] = useState<{ key: string; shown: Shown } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const selected = strategies.find((item) => String(item.indicator.id) === chosen) ?? strategies[0];
  if (!selected) {
    return (
      <div className="trading-strategy-tester trading-strategy-empty">
        <strong>No strategy on this chart</strong>
        <span>Write a script with strategy() in the {PRODUCT_NAME} Scripts editor and add it to the chart; its backtest shows here. Strategies trade a simulated account only.</span>
      </div>
    );
  }
  const id = String(selected.indicator.id);
  const deepKey = JSON.stringify([id, instrumentId, interval, bindingId, selected.indicator.params]);
  const shown: Shown = deep && deep.key === deepKey ? deep.shown : { report: selected.status!.strategy!, times: selected.status!.times ?? [], source: 'chart' };
  const runDeep = async () => {
    const scriptId = scriptIdOf(id);
    if (!scriptId) return;
    setBusy(true);
    setProblem(null);
    try {
      const script = await loadScriptSource(scriptId, Number(selected.indicator.params?.revision ?? 0));
      const response = await scriptsApi.backtest({ source: script.source, instrumentId, bindingId, interval, inputs: scriptInputValues(selected.indicator) });
      if (!response.result?.strategy) {
        setProblem(response.error ? `${response.error.line ? `Line ${response.error.line}: ` : ''}${response.error.message}` : 'The backtest did not run.');
        return;
      }
      setDeep({ key: deepKey, shown: { report: response.result.strategy, times: response.times, source: 'deep' } });
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  const first = shown.times[0];
  const last = shown.times.at(-1);
  return (
    <div className="trading-strategy-tester">
      <header className="trading-strategy-header">
        {strategies.length > 1 ? (
          <select aria-label="Strategy" value={id} onChange={(event) => setChosen(event.target.value)}>
            {strategies.map((item) => <option key={String(item.indicator.id)} value={String(item.indicator.id)}>{scriptIndicatorName(item.indicator)}</option>)}
          </select>
        ) : <strong>{scriptIndicatorName(selected.indicator)}</strong>}
        <span className="trading-strategy-range" role="status">
          {shown.source === 'deep' ? 'Deep backtest' : 'Chart bars'}: {shown.times.length.toLocaleString()} bars{first && last ? ` · ${new Date(first).toLocaleDateString()} – ${new Date(last).toLocaleDateString()}` : ''}
        </span>
        <button type="button" onClick={() => void runDeep()} disabled={busy}>{busy ? 'Backtesting…' : 'Deep backtest'}</button>
        {shown.source === 'deep' ? <button type="button" onClick={() => setDeep(null)}>Chart bars</button> : null}
      </header>
      {problem ? <p className="trading-strategy-note" role="alert">{problem}</p> : null}
      <nav role="tablist" aria-label="Strategy tester sections" className="trading-strategy-tabs">
        {([['overview', 'Overview'], ['summary', 'Performance summary'], ['trades', 'List of trades'], ['properties', 'Properties']] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>
        ))}
      </nav>
      <div role="tabpanel" className="trading-strategy-panel">
        {tab === 'overview' ? <Overview report={shown.report} /> : null}
        {tab === 'summary' ? (
          <div className="trading-strategy-table-scroll">
            <table aria-label="Performance summary">
              <thead><tr><th /><th>All</th><th>Long</th><th>Short</th></tr></thead>
              <tbody>
                {SUMMARY_ROWS.map(([label, key, kind]) => (
                  <tr key={key}><th scope="row">{label}</th>{(['all', 'long', 'short'] as const).map((side) => <td key={side}>{cell(shown.report.summary[side], key, kind)}</td>)}</tr>
                ))}
                <tr><th scope="row">Max drawdown</th><td>{money(shown.report.summary.all.max_drawdown)}</td><td /><td /></tr>
                <tr><th scope="row">Commission paid</th><td>{money(shown.report.summary.all.commission_paid)}</td><td /><td /></tr>
                <tr><th scope="row">Max contracts held</th><td>{cell(shown.report.summary.all, 'max_contracts_held', 'ratio')}</td><td /><td /></tr>
              </tbody>
            </table>
          </div>
        ) : null}
        {tab === 'trades' ? <Trades report={shown.report} /> : null}
        {tab === 'properties' ? (
          <div className="trading-strategy-table-scroll">
            <table aria-label="Strategy properties">
              <tbody>
                {Object.entries(shown.report.settings).map(([key, value]) => <tr key={key}><th scope="row">{key.replace(/_/g, ' ')}</th><td>{String(value)}</td></tr>)}
                {Object.entries(scriptInputValues(selected.indicator)).map(([key, value]) => <tr key={`input-${key}`}><th scope="row">Input: {key}</th><td>{String(value)}</td></tr>)}
                <tr><th scope="row">Symbol</th><td>{instrumentId} · {interval}</td></tr>
              </tbody>
            </table>
            <p className="trading-strategy-note">A research backtest on a simulated account; strategy scripts never place orders. Prices in ticks use a 0.01 tick.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
