/* eslint-disable react-hooks/rules-of-hooks -- baseline WP-9.x */
import { useEffect, useMemo, useState } from 'react';
import { tradingPaperApi } from './tradingPaperApi';
import type { PaperAccountSnapshot, PaperOrder } from './paperTypes';
import { tradingStrategyApi } from './tradingStrategyApi';
import type { TradingStrategyConfig } from './tradingStrategyTypes';
import { TradingCommandCenter } from './TradingCommandCenter';
import { TradingTradeDrilldown } from './TradingTradeDrilldown';
import {
  tradingPaperAnalyticsApi,
  type AnalyticsNumeric,
  type PaperAnalyticsMode,
  type PaperAnalyticsOverview,
  type PaperAnalyticsTrade,
  type PaperSimulationEpoch,
} from './tradingPaperAnalyticsApi';
import './TradingPaperDashboard.css';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';
import { chartPalette } from './chartPalette';

type DashboardTab = 'overview' | 'diagnostics' | 'execution';
type Point = { x: number; y: number; label?: string };
type DailyPerformanceRow = { date: string; value: number };
type ManualPaperPerformance = {
  tradeCount: number;
  wins: number;
  losses: number;
  winRate: number | null;
  profitFactor: number | null;
  totalPnl: number;
  bestPnl: number | null;
  worstPnl: number | null;
  daily: DailyPerformanceRow[];
};

function numeric(value: AnalyticsNumeric | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fixed(value: AnalyticsNumeric | null | undefined, digits = 2): string {
  const parsed = numeric(value);
  return parsed === null ? '—' : parsed.toFixed(digits);
}

function signed(value: AnalyticsNumeric | null | undefined, digits = 2, suffix = ''): string {
  const parsed = numeric(value);
  if (parsed === null) return '—';
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}${suffix}`;
}

function pct(value: AnalyticsNumeric | null | undefined, digits = 1): string {
  const parsed = numeric(value);
  return parsed === null ? '—' : `${(parsed * 100).toFixed(digits)}%`;
}

function money(value: AnalyticsNumeric | null | undefined, currency = 'USD', signedValue = false): string {
  const parsed = numeric(value);
  if (parsed === null) return '—';
  const formatted = Math.abs(parsed).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${signedValue && parsed > 0 ? '+' : parsed < 0 ? '-' : ''}${formatted} ${currency}`;
}

function symbol(instrumentId: string): string {
  return instrumentId.split(':').at(-1)?.replace('-', '/') ?? instrumentId;
}

function shortDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function timeLabel(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function compactTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function paperOrderTime(order: PaperOrder): string {
  const value = order.updated_at ?? order.created_at;
  return value ? compactTime(value) : '—';
}

function manualPaperPerformance(
  snapshot: PaperAccountSnapshot | null,
  startDate: string,
  endDate: string,
): ManualPaperPerformance {
  const realized = (snapshot?.recent_ledger ?? []).flatMap((entry) => {
    if (entry.entry_type !== 'realized_pnl') return [];
    const amount = Number(entry.amount);
    if (!Number.isFinite(amount)) return [];
    const parsedDate = entry.created_at ? new Date(entry.created_at) : null;
    const date = parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate.toISOString().slice(0, 10) : null;
    if ((startDate && (!date || date < startDate)) || (endDate && (!date || date > endDate))) return [];
    return [{ amount, date }];
  });
  const wins = realized.filter((item) => item.amount > 0).length;
  const losses = realized.filter((item) => item.amount < 0).length;
  const grossProfit = realized.filter((item) => item.amount > 0).reduce((total, item) => total + item.amount, 0);
  const grossLoss = Math.abs(realized.filter((item) => item.amount < 0).reduce((total, item) => total + item.amount, 0));
  const dailyByDate = new Map<string, number>();
  for (const item of realized) {
    if (item.date) dailyByDate.set(item.date, (dailyByDate.get(item.date) ?? 0) + item.amount);
  }
  return {
    tradeCount: realized.length,
    wins,
    losses,
    winRate: realized.length ? wins / realized.length : null,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    totalPnl: realized.reduce((total, item) => total + item.amount, 0),
    bestPnl: realized.length ? Math.max(...realized.map((item) => item.amount)) : null,
    worstPnl: realized.length ? Math.min(...realized.map((item) => item.amount)) : null,
    daily: [...dailyByDate.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, value]) => ({ date, value })),
  };
}

function durationLabel(milliseconds: number | null): string {
  if (milliseconds === null || !Number.isFinite(milliseconds) || milliseconds < 0) return '—';
  const totalMinutes = Math.round(milliseconds / 60_000);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours}h ${minutes}m`;
}

function sampleSharpe(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = values.reduce((total, value) => total + value, 0) / values.length;
  const variance = values.reduce((total, value) => total + (value - mean) ** 2, 0) / (values.length - 1);
  const deviation = Math.sqrt(variance);
  return deviation > 0 ? (mean / deviation) * Math.sqrt(252) : null;
}

function sampleSortino(values: number[]): number | null {
  if (values.length < 2) return null;
  const mean = values.reduce((total, value) => total + value, 0) / values.length;
  const downside = values.map((value) => Math.min(0, value));
  const downsideDeviation = Math.sqrt(downside.reduce((total, value) => total + value ** 2, 0) / values.length);
  return downsideDeviation > 0 ? (mean / downsideDeviation) * Math.sqrt(252) : null;
}

function linePath(points: Point[], width: number, height: number, padding: number, domain: { min: number; max: number }): string {
  if (!points.length) return '';
  const span = Math.max(0.000001, domain.max - domain.min);
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;
  return points.map((point, index) => {
    const x = padding + (points.length <= 1 ? innerWidth / 2 : index / (points.length - 1) * innerWidth);
    const y = padding + (domain.max - point.y) / span * innerHeight;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

function LineChart({
  points,
  references = [],
  empty = 'No historical points yet.',
  tone = 'primary',
  formatAxis = (value: number) => value.toFixed(2),
  area = true,
}: {
  points: Point[];
  references?: Array<{ value: number; label: string }>;
  empty?: string;
  tone?: 'primary' | 'danger' | 'success';
  formatAxis?: (value: number) => string;
  area?: boolean;
}) {
  const width = 760;
  const height = 220;
  const padding = 30;
  if (!points.length) return <div className="paper-dashboard-empty">{empty}</div>;
  const values = [...points.map((point) => point.y), ...references.map((reference) => reference.value)];
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const margin = Math.max((max - min) * 0.08, 0.0001);
  min -= margin;
  max += margin;
  const domain = { min, max };
  const path = linePath(points, width, height, padding, domain);
  const span = max - min;
  const yFor = (value: number) => padding + (max - value) / span * (height - padding * 2);
  const areaPath = `${path} L${width - padding},${height - padding} L${padding},${height - padding} Z`;
  return (
    <svg className={`paper-dashboard-chart tone-${tone}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Time series chart">
      <line className="grid" x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} />
      <line className="grid faint" x1={padding} x2={width - padding} y1={padding + (height - padding * 2) / 2} y2={padding + (height - padding * 2) / 2} />
      {references.map((reference) => (
        <g key={`${reference.label}-${reference.value}`}>
          <line className="reference" x1={padding} x2={width - padding} y1={yFor(reference.value)} y2={yFor(reference.value)} />
          <text className="reference-label" x={width - padding - 4} y={yFor(reference.value) - 4}>{reference.label}</text>
        </g>
      ))}
      {area ? <path className="chart-area" d={areaPath} /> : null}
      <path className="series" d={path} fill="none" />
      <circle className="last-point" cx={width - padding} cy={yFor(points.at(-1)?.y ?? 0)} r="4" />
      <text className="axis-label" x={padding} y={14}>{formatAxis(max)}</text>
      <text className="axis-label" x={padding} y={height - 6}>{formatAxis(min)}</text>
    </svg>
  );
}

function DailyBars({ rows, unit }: { rows: DailyPerformanceRow[]; unit: 'R' | 'USD' }) {
  if (!rows.length) return <div className="paper-dashboard-empty">No completed paper-trading sessions yet.</div>;
  const max = Math.max(0.0001, ...rows.map((row) => Math.abs(row.value)));
  return (
    <div className="paper-dashboard-daily-bars" role="img" aria-label={`Daily performance in ${unit}`}>
      {rows.slice(-24).map((row) => {
        const magnitude = Math.max(4, Math.abs(row.value) / max * 47);
        return (
          <div key={row.date} className="daily-column" title={`${row.date}: ${row.value > 0 ? '+' : ''}${row.value.toFixed(2)} ${unit}`}>
            <div className="daily-track">
              <span className={row.value >= 0 ? 'daily-bar positive' : 'daily-bar negative'} style={{ height: `${magnitude}%` }} />
            </div>
            <small>{shortDate(row.date)}</small>
          </div>
        );
      })}
    </div>
  );
}

function WinLossDonut({ wins, losses }: { wins: number; losses: number }) {
  const total = wins + losses;
  const winRate = total ? wins / total : 0;
  const percentage = winRate * 100;
  return (
    <div className="paper-win-loss">
      <div className="paper-win-donut" style={{ background: `conic-gradient(${chartPalette.gain} 0 ${percentage}%, ${chartPalette.loss} ${percentage}% 100%)` }} role="img" aria-label={`${wins} winning trades and ${losses} losing trades`}>
        <div><strong>{total}</strong><small>Total</small></div>
      </div>
      <div className="paper-win-legend">
        <span><i className="win" /><b>Win trades</b><small>{(winRate * 100).toFixed(1)}% ({wins})</small></span>
        <span><i className="loss" /><b>Loss trades</b><small>{((1 - winRate) * 100).toFixed(1)}% ({losses})</small></span>
      </div>
    </div>
  );
}

function Distribution({ rows }: { rows: PaperAnalyticsOverview['r_distribution'] }) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <div className="paper-dashboard-histogram" role="img" aria-label="Trade R distribution">
      {rows.map((row) => (
        <div key={row.label} title={`${row.label}: ${row.count}`}>
          <span className="hist-bar" style={{ height: `${Math.max(2, row.count / max * 100)}%` }} />
          <small>{row.label}</small>
          <strong>{row.count}</strong>
        </div>
      ))}
    </div>
  );
}

function MaeMfeScatter({ rows }: { rows: PaperAnalyticsOverview['mae_mfe'] }) {
  const width = 760;
  const height = 250;
  const padding = 34;
  if (!rows.length) return <div className="paper-dashboard-empty">MAE/MFE appears as prospective SHADOW replays and completed AUTO PAPER trades accumulate.</div>;
  const xs = rows.map((row) => Number(row.mae_r));
  const ys = rows.map((row) => Number(row.mfe_r));
  const minX = Math.min(-1, ...xs);
  const maxX = Math.max(0, ...xs);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(1.5, ...ys);
  const x = (value: number) => padding + (value - minX) / Math.max(0.0001, maxX - minX) * (width - padding * 2);
  const y = (value: number) => padding + (maxY - value) / Math.max(0.0001, maxY - minY) * (height - padding * 2);
  return (
    <svg className="paper-dashboard-chart scatter" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="MAE versus MFE scatter plot">
      <line className="reference" x1={x(-1)} x2={x(-1)} y1={padding} y2={height - padding} />
      <line className="reference" x1={padding} x2={width - padding} y1={y(0.75)} y2={y(0.75)} />
      <line className="reference target" x1={padding} x2={width - padding} y1={y(1.5)} y2={y(1.5)} />
      <text className="reference-label" x={x(-1) + 4} y={padding + 12}>−1R</text>
      <text className="reference-label" x={width - padding - 90} y={y(0.75) - 4}>+0.75R protect</text>
      <text className="reference-label" x={width - padding - 80} y={y(1.5) - 4}>+1.5R target</text>
      {rows.map((row) => (
        <circle key={row.trade_id} className={Number(row.r_result) >= 0 ? 'scatter-point winner' : 'scatter-point loser'} cx={x(Number(row.mae_r))} cy={y(Number(row.mfe_r))} r="5">
          <title>{`${symbol(row.instrument_id)} ${row.session_date} · ${signed(row.r_result, 2, 'R')} · MAE ${signed(row.mae_r, 2, 'R')} · MFE ${signed(row.mfe_r, 2, 'R')}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function Qualification({ data }: { data: NonNullable<PaperAnalyticsOverview['qualification']> }) {
  const threshold = data.thresholds;
  const items = [
    { label: 'Matched trades', value: `${data.matched_eligible_trade_count} / ${threshold.minimum_matched_trades}`, pass: data.matched_eligible_trade_count >= threshold.minimum_matched_trades },
    { label: 'Sessions', value: `${data.distinct_sessions} / ${threshold.minimum_distinct_sessions}`, pass: data.distinct_sessions >= threshold.minimum_distinct_sessions },
    { label: 'Symbols', value: `${data.distinct_symbols} / ${threshold.minimum_distinct_symbols}`, pass: data.distinct_symbols >= threshold.minimum_distinct_symbols },
    { label: 'Execution match', value: data.execution_match_rate == null ? 'N/A' : pct(data.execution_match_rate), pass: Number(data.execution_match_rate ?? 0) >= Number(threshold.minimum_execution_match_rate) },
    { label: 'Expectancy', value: data.expectancy_r == null ? 'N/A' : signed(data.expectancy_r, 3, 'R'), pass: Number(data.expectancy_r ?? Number.NEGATIVE_INFINITY) >= Number(threshold.minimum_expectancy_r) },
    { label: '90% LCB', value: data.one_sided_90_lcb_r == null ? 'N/A' : signed(data.one_sided_90_lcb_r, 3, 'R'), pass: Number(data.one_sided_90_lcb_r ?? 0) > 0 },
    { label: 'Max drawdown', value: data.max_drawdown_r == null ? 'N/A' : `${fixed(data.max_drawdown_r, 2)}R`, pass: data.max_drawdown_r != null && Number(data.max_drawdown_r) <= Number(threshold.maximum_drawdown_r) },
    { label: 'Operator review', value: data.reviewed ? 'Recorded' : 'Required', pass: data.reviewed },
  ];
  return (
    <section className="paper-qualification" data-authorized={data.auto_paper_authorized ? 'true' : 'false'}>
      <header><div><strong>V2 prospective qualification</strong><small>Frozen profile · prospective evidence since {data.prospective_start}</small></div><span>{data.auto_paper_authorized ? 'AUTO PAPER AUTHORIZED' : 'NOT QUALIFIED'}</span></header>
      <div className="qualification-grid">{items.map((item) => <div key={item.label} data-pass={item.pass ? 'true' : 'false'}><small>{item.label}</small><strong>{item.value}</strong><span>{item.pass ? '✓' : '✕'}</span></div>)}</div>
      <footer><span>Profile <code>{data.current_profile_fingerprint.slice(0, 12)}</code></span><span>Evidence <code>{data.evidence_fingerprint.slice(0, 12)}</code></span>{data.reason_codes.length ? <span>Blocking: {data.reason_codes.join(', ')}</span> : <span>Quantitative floors pass.</span>}</footer>
    </section>
  );
}

function Metric({ icon, label, value, detail, tone = 'neutral' }: { icon: string; label: string; value: string; detail?: string; tone?: 'neutral' | 'positive' | 'negative' | 'primary' }) {
  return (
    <div className={`paper-overview-metric tone-${tone}`}>
      <span className="metric-icon" aria-hidden="true">{icon}</span>
      <div><small>{label}</small><strong>{value}</strong>{detail ? <span>{detail}</span> : null}</div>
    </div>
  );
}

function Kpi({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return <div className="paper-dashboard-kpi"><small>{label}</small><strong>{value}</strong>{detail ? <span>{detail}</span> : null}</div>;
}

function Insight({ icon, label, value, detail }: { icon: string; label: string; value: string; detail?: string }) {
  return <div className="paper-insight"><span aria-hidden="true">{icon}</span><div><small>{label}</small><strong>{value}</strong>{detail ? <em>{detail}</em> : null}</div></div>;
}

function PaperOrderReferences({ orders }: { orders: PaperOrder[] }) {
  return (
    <article className="paper-panel-card paper-order-references-card">
      <header>
        <div><strong>Paper order references</strong><small>Direct account activity, including manual fills and rejected orders. These are not canonical strategy trade records.</small></div>
        <span>{orders.length} shown</span>
      </header>
      <div className="paper-trades-table"><table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th><th>Fill price</th><th>Order reference</th></tr></thead><tbody>
        {orders.map((order) => <tr key={order.order_id}><td>{paperOrderTime(order)}</td><td><strong>{symbol(order.instrument_id)}</strong></td><td>{order.side.toUpperCase()}</td><td>{Number(order.filled_quantity) > 0 ? order.filled_quantity : order.quantity}</td><td><span className={`paper-order-status status-${order.status}`}>{order.status.toUpperCase()}</span></td><td>{order.average_fill_price ?? '—'}</td><td><code title={order.order_id}>{order.order_id}</code></td></tr>)}
        {!orders.length ? <tr><td colSpan={7}>No paper orders have been recorded for this account.</td></tr> : null}
      </tbody></table></div>
    </article>
  );
}

export function TradingPaperDashboard() {
  const [accounts, setAccounts] = useState<Array<{ account_id: string; name: string; currency: string }>>([]);
  const [strategies, setStrategies] = useState<TradingStrategyConfig[]>([]);
  const [epochs, setEpochs] = useState<PaperSimulationEpoch[]>([]);
  const [accountId, setAccountId] = useState('');
  const [strategyId, setStrategyId] = useState('');
  const [epochId, setEpochId] = useState('');
  const [mode, setMode] = useState<PaperAnalyticsMode>('auto_paper');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [rollingWindow, setRollingWindow] = useState(20);
  const [tab, setTab] = useState<DashboardTab>('overview');
  const [data, setData] = useState<PaperAnalyticsOverview | null>(null);
  const [paperSnapshot, setPaperSnapshot] = useState<PaperAccountSnapshot | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<PaperAnalyticsTrade | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([tradingPaperApi.accounts(), tradingStrategyApi.list()]).then(([nextAccounts, nextStrategies]) => {
      if (!alive) return;
      setAccounts(nextAccounts.map((account) => ({ account_id: account.account_id, name: account.name, currency: account.base_currency })));
      setStrategies(nextStrategies);
      const firstAccount = nextAccounts[0]?.account_id ?? '';
      setAccountId((current) => current || firstAccount);
      if (firstAccount) {
        const firstStrategy = nextStrategies.find((strategy) => strategy.account_id === firstAccount);
        setStrategyId((current) => current || firstStrategy?.strategy_id || '');
      }
    }).catch((reason) => {
      if (!alive) return;
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus('error');
    });
    return () => { alive = false; };
  }, []);

  const accountStrategies = useMemo(() => strategies.filter((strategy) => strategy.account_id === accountId), [accountId, strategies]);

  useEffect(() => {
    if (!accountId) {
      setPaperSnapshot(null);
      return;
    }
    let alive = true;
    const refresh = () => {
      void tradingPaperApi.snapshot(accountId).then((snapshot) => {
        if (alive) setPaperSnapshot(snapshot);
      }).catch(() => {
        if (alive) setPaperSnapshot(null);
      });
    };
    refresh();
    const stopPolling = startPolling(refresh, POLL_INTERVALS_MS.paperAccount);
    return () => {
      alive = false;
      stopPolling();
    };
  }, [accountId]);

  useEffect(() => {
    if (!accountId) { setEpochs([]); setEpochId(''); return; }
    let alive = true;
    void tradingPaperAnalyticsApi.epochs(accountId).then((nextEpochs) => {
      if (!alive) return;
      setEpochs(nextEpochs);
      const current = nextEpochs.find((epoch) => epoch.is_current) ?? nextEpochs[0];
      setEpochId((existing) => nextEpochs.some((epoch) => epoch.epoch_id === existing) ? existing : current?.epoch_id ?? '');
    }).catch((reason) => { if (alive) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { alive = false; };
  }, [accountId]);

  useEffect(() => {
    if (!accountId) return;
    let alive = true;
    setStatus('loading');
    setError(null);
    void tradingPaperAnalyticsApi.overview({ accountId, strategyId: strategyId || null, epochId: epochId || null, mode, startDate: startDate || null, endDate: endDate || null, rollingWindow }).then((overview) => {
      if (!alive) return;
      setData(overview);
      setEpochs(overview.epochs);
      setStatus('ready');
    }).catch((reason) => {
      if (!alive) return;
      setData(null);
      setError(reason instanceof Error ? reason.message : String(reason));
      setStatus('error');
    });
    return () => { alive = false; };
  }, [accountId, strategyId, epochId, mode, startDate, endDate, rollingWindow]);

  useEffect(() => {
    setSelectedTrade(null);
  }, [accountId, strategyId, epochId, mode, startDate, endDate]);

  const selectAccount = (next: string) => {
    setAccountId(next);
    const firstStrategy = strategies.find((strategy) => strategy.account_id === next);
    setStrategyId(firstStrategy?.strategy_id ?? '');
    setEpochId('');
  };

  const cumulativeR = useMemo(() => {
    let total = 0;
    return (data?.daily_r ?? []).map((row, index) => { total += Number(row.r_result); return { x: index, y: total, label: row.session_date }; });
  }, [data?.daily_r]);
  const equityPoints = useMemo(() => (data?.equity ?? []).map((row, index) => ({ x: index, y: Number(row.equity), label: row.observed_at })), [data?.equity]);
  const drawdownPoints = useMemo(() => (data?.drawdown ?? []).map((row, index) => ({ x: index, y: Number(row.drawdown), label: row.observed_at })), [data?.drawdown]);
  const rollingPoints = useMemo(() => (data?.rolling_expectancy ?? []).map((row, index) => ({ x: index, y: Number(row.expectancy_r), label: row.observed_at })), [data?.rolling_expectancy]);
  const lcbPoints = useMemo(() => (data?.rolling_expectancy ?? []).filter((row) => row.one_sided_90_lcb_r != null).map((row, index) => ({ x: index, y: Number(row.one_sided_90_lcb_r), label: row.observed_at })), [data?.rolling_expectancy]);
  const exposurePoints = useMemo(() => (data?.equity ?? []).map((row, index) => ({ x: index, y: Number(row.gross_exposure), label: row.observed_at })), [data?.equity]);
  const riskPoints = useMemo(() => (data?.equity ?? []).map((row, index) => ({ x: index, y: Number(row.risk_at_stop), label: row.observed_at })), [data?.equity]);

  if (!accounts.length && status !== 'loading') {
    return <section className="trading-paper-dashboard"><div className="paper-dashboard-empty"><strong>Paper trading dashboard</strong><span>Create a paper account to begin collecting durable analytics.</span></div></section>;
  }

  let summary = data?.summary;
  const selectedAccount = accounts.find((account) => account.account_id === accountId);
  const currency = selectedAccount?.currency ?? 'USD';
  const selectedEpoch = epochs.find((epoch) => epoch.epoch_id === epochId) ?? epochs.find((epoch) => epoch.is_current) ?? epochs[0];
  const initialCash = numeric(selectedEpoch?.initial_cash);
  const latestEquity = data?.equity.at(-1);
  const accountBalance = numeric(latestEquity?.cash) ?? initialCash;
  const accountEquity = numeric(latestEquity?.equity) ?? accountBalance;
  const realizedPnl = numeric(latestEquity?.realized_pnl);
  const unrealizedPnl = numeric(latestEquity?.unrealized_pnl);
  const equityReturn = initialCash && accountEquity !== null ? (accountEquity - initialCash) / initialCash : null;
  const realizedReturn = initialCash && realizedPnl !== null ? realizedPnl / initialCash : null;
  const unrealizedReturn = initialCash && unrealizedPnl !== null ? unrealizedPnl / initialCash : null;
  const dailyValues = (data?.daily_r ?? []).map((row) => Number(row.r_result)).filter(Number.isFinite);
  const sharpe = sampleSharpe(dailyValues);
  const sortino = sampleSortino(dailyValues);
  const tradeRows = data?.recent_trades ?? [];
  const paperOrderRows = useMemo(
    () => [...(paperSnapshot?.order_history ?? [])]
      .sort((left, right) => new Date(right.updated_at ?? right.created_at ?? 0).getTime() - new Date(left.updated_at ?? left.created_at ?? 0).getTime())
      .slice(0, 8),
    [paperSnapshot?.order_history],
  );
  const manualPerformance = useMemo(
    () => manualPaperPerformance(paperSnapshot, startDate, endDate),
    [endDate, paperSnapshot, startDate],
  );
  const usingManualPerformance = mode === 'auto_paper'
    && tradeRows.length === 0
    && manualPerformance.tradeCount > 0;
  if (usingManualPerformance && summary) {
    summary = {
      ...summary,
      trade_count: manualPerformance.tradeCount,
      wins: manualPerformance.wins,
      losses: manualPerformance.losses,
      // The gateway sends decimals as strings; keep the same shape.
      win_rate: manualPerformance.winRate === null ? null : String(manualPerformance.winRate),
      profit_factor: manualPerformance.profitFactor === null ? null : String(manualPerformance.profitFactor),
      expectancy_r: null,
    };
  }
  const completeTradeSample = Boolean(summary && tradeRows.length === summary.trade_count);
  const tradeRs = tradeRows.map((trade) => Number(trade.r_result)).filter(Number.isFinite);
  const winners = tradeRs.filter((value) => value > 0);
  const losers = tradeRs.filter((value) => value < 0);
  const averageWin = completeTradeSample && winners.length ? winners.reduce((total, value) => total + value, 0) / winners.length : null;
  const averageLoss = completeTradeSample && losers.length ? losers.reduce((total, value) => total + value, 0) / losers.length : null;
  const payoffRatio = averageWin !== null && averageLoss !== null && averageLoss !== 0 ? averageWin / Math.abs(averageLoss) : null;
  const durations = tradeRows.map((trade) => new Date(trade.exit_time).getTime() - new Date(trade.entry_time).getTime()).filter((value) => Number.isFinite(value) && value >= 0);
  const averageDuration = completeTradeSample && durations.length ? durations.reduce((total, value) => total + value, 0) / durations.length : null;
  const bestTrade = tradeRows.length ? tradeRows.reduce((best, trade) => Number(trade.r_result) > Number(best.r_result) ? trade : best) : null;
  const worstTrade = tradeRows.length ? tradeRows.reduce((worst, trade) => Number(trade.r_result) < Number(worst.r_result) ? trade : worst) : null;
  const hasCompleteDollarPnl = Boolean(summary && tradeRows.length === summary.trade_count && tradeRows.every((trade) => trade.realized_pnl != null));
  const dailyPerformance = useMemo<DailyPerformanceRow[]>(() => {
    if (!data) return [];
    if (usingManualPerformance) return manualPerformance.daily;
    if (!hasCompleteDollarPnl) return data.daily_r.map((row) => ({ date: row.session_date, value: Number(row.r_result) }));
    const byDate = new Map<string, number>();
    for (const trade of data.recent_trades) byDate.set(trade.session_date, (byDate.get(trade.session_date) ?? 0) + Number(trade.realized_pnl ?? 0));
    return [...byDate.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, value]) => ({ date, value }));
  }, [data, hasCompleteDollarPnl, manualPerformance.daily, usingManualPerformance]);
  const dailyUnit: 'R' | 'USD' = usingManualPerformance || hasCompleteDollarPnl ? 'USD' : 'R';
  const drawdownUnit = data?.drawdown[0]?.unit ?? 'R';
  const maxDrawdown = drawdownPoints.length ? Math.min(...drawdownPoints.map((point) => point.y)) : null;

  return (
    <section className="trading-paper-dashboard" aria-label="Paper trading analytics dashboard" data-status={status}>
      <header className="paper-dashboard-header">
        <div><strong>Trading Command Center</strong><small>Daily operations, account performance, strategy evidence and execution quality</small></div>
        <span>{status === 'loading' ? 'Refreshing…' : status === 'error' ? 'Unavailable' : 'Durable analytics'}</span>
      </header>

      <div className="paper-dashboard-filterbar">
        <div className="paper-dashboard-view-tabs" role="tablist" aria-label="Paper dashboard section">
          <button type="button" role="tab" aria-selected={tab === 'overview'} onClick={() => setTab('overview')}>Overview</button>
          <button type="button" role="tab" aria-selected={tab === 'diagnostics'} onClick={() => setTab('diagnostics')}>Strategy diagnostics</button>
          <button type="button" role="tab" aria-selected={tab === 'execution'} onClick={() => setTab('execution')}>Execution &amp; risk</button>
        </div>
        <div className="paper-dashboard-filters">
          <label><span>Account</span><select aria-label="Dashboard account" value={accountId} onChange={(event) => selectAccount(event.target.value)}>{accounts.map((account) => <option key={account.account_id} value={account.account_id}>{account.name}</option>)}</select></label>
          <label><span>Strategy</span><select aria-label="Dashboard strategy" value={strategyId} onChange={(event) => setStrategyId(event.target.value)}><option value="">All strategies</option>{accountStrategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.archived_at ? 'Archived · ' : ''}{strategy.strategy_id} · v{strategy.strategy_version}</option>)}</select></label>
          <label><span>Mode</span><select aria-label="Dashboard mode" value={mode} onChange={(event) => setMode(event.target.value as PaperAnalyticsMode)}><option value="shadow">Prospective SHADOW</option><option value="auto_paper">AUTO PAPER</option></select></label>
          <label><span>Epoch</span><select aria-label="Dashboard simulation epoch" value={epochId} onChange={(event) => setEpochId(event.target.value)}><option value="">All epochs</option>{epochs.map((epoch) => <option key={epoch.epoch_id} value={epoch.epoch_id}>{epoch.is_current ? 'Current · ' : ''}Epoch {epoch.ordinal}</option>)}</select></label>
          <label className="date-filter"><span>From</span><input aria-label="Dashboard start date" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
          <label className="date-filter"><span>To</span><input aria-label="Dashboard end date" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
        </div>
      </div>

      {error ? <div className="paper-dashboard-error" role="status">{error}</div> : null}

      {accountId ? <TradingCommandCenter accountId={accountId} strategyId={strategyId || null} currency={currency} /> : null}

      {data ? <>
        <div className="paper-overview-strip">
          <Metric icon="▣" label="Account balance" value={money(accountBalance, currency)} detail={initialCash === null ? undefined : `Initial ${money(initialCash, currency)}`} />
          <Metric icon="↗" label="Equity" value={money(accountEquity, currency)} detail={equityReturn === null ? undefined : `${equityReturn >= 0 ? '+' : ''}${(equityReturn * 100).toFixed(2)}%`} tone={equityReturn !== null && equityReturn < 0 ? 'negative' : 'primary'} />
          <Metric icon="$" label="Realized P&L" value={money(realizedPnl, currency, true)} detail={realizedReturn === null ? undefined : `${realizedReturn >= 0 ? '+' : ''}${(realizedReturn * 100).toFixed(2)}%`} tone={realizedPnl !== null && realizedPnl < 0 ? 'negative' : 'positive'} />
          <Metric icon="＋" label="Unrealized P&L" value={money(unrealizedPnl, currency, true)} detail={unrealizedReturn === null ? undefined : `${unrealizedReturn >= 0 ? '+' : ''}${(unrealizedReturn * 100).toFixed(2)}%`} tone={unrealizedPnl !== null && unrealizedPnl < 0 ? 'negative' : 'positive'} />
          <Metric icon="◎" label="Win rate" value={pct(summary?.win_rate, 2)} detail={`${summary?.wins ?? 0}W / ${summary?.losses ?? 0}L`} tone="primary" />
          <Metric icon="Σ" label="Expectancy" value={signed(summary?.expectancy_r, 3, 'R')} detail="Per trade" tone={Number(summary?.expectancy_r ?? 0) < 0 ? 'negative' : 'primary'} />
          <Metric icon="△" label="Max drawdown" value={maxDrawdown === null ? (summary?.max_drawdown_r == null ? '—' : `-${Math.abs(Number(summary.max_drawdown_r)).toFixed(2)}R`) : `${maxDrawdown.toFixed(2)}${drawdownUnit === 'percent' ? '%' : 'R'}`} tone="negative" />
          <Metric icon="◇" label="Trades" value={String(summary?.trade_count ?? 0)} detail="Filtered sample" />
        </div>

        {tab === 'overview' ? <>
          <div className="paper-overview-charts">
            <article className="paper-chart-card equity-card"><header><div><strong>Equity curve</strong><small>{money(accountEquity, currency)} · {equityReturn === null ? 'No baseline' : `${equityReturn >= 0 ? '+' : ''}${(equityReturn * 100).toFixed(2)}%`}</small></div><span>All time</span></header><LineChart points={equityPoints.length ? equityPoints : cumulativeR} references={equityPoints.length && initialCash !== null ? [{ value: initialCash, label: 'initial' }] : [{ value: 0, label: '0R' }]} formatAxis={equityPoints.length ? (value) => value.toLocaleString(undefined, { maximumFractionDigits: 0 }) : (value) => `${value.toFixed(1)}R`} /></article>
            <article className="paper-chart-card daily-card"><header><div><strong>{dailyUnit === 'USD' ? `Daily P&L (${currency})` : 'Daily result (R)'}</strong><small>{dailyUnit === 'USD' ? 'Realized canonical trade P&L' : 'Strategy-normalized session result'}</small></div><span>All time</span></header><DailyBars rows={dailyPerformance} unit={dailyUnit} /></article>
            <article className="paper-chart-card win-card"><header><div><strong>Win vs loss</strong><small>Completed filtered trades</small></div><span>{pct(summary?.win_rate)}</span></header><WinLossDonut wins={summary?.wins ?? 0} losses={summary?.losses ?? 0} /><footer><div><small>Best trade</small><strong className="positive">{bestTrade ? signed(bestTrade.r_result, 2, 'R') : '—'}</strong></div><div><small>Worst trade</small><strong className="negative">{worstTrade ? signed(worstTrade.r_result, 2, 'R') : '—'}</strong></div></footer></article>
            <article className="paper-chart-card drawdown-card"><header><div><strong>Drawdown</strong><small>Peak-to-trough {drawdownUnit === 'percent' ? 'equity' : 'strategy R'}</small></div><span>All time</span></header><LineChart points={drawdownPoints} references={[{ value: 0, label: 'peak' }]} tone="danger" formatAxis={(value) => `${value.toFixed(2)}${drawdownUnit === 'percent' ? '%' : 'R'}`} /></article>
          </div>

          <PaperOrderReferences orders={paperOrderRows} />

          <div className="paper-overview-lower">
            <article className="paper-panel-card recent-trades-card"><header><div><strong>Recent trades</strong><small>Canonical AUTO PAPER and prospective SHADOW outcomes · drill into retained evidence</small></div><span>{tradeRows.length} shown</span></header><div className="paper-trades-table"><table><thead><tr><th>Time</th><th>Symbol</th><th>Mode</th><th>Qty</th><th>P&L</th><th>R-mult</th><th>Outcome</th><th>Exit</th><th>Detail</th></tr></thead><tbody>{tradeRows.slice(0, 8).map((trade) => <tr key={`${trade.source}-${trade.trade_id}`}><td>{compactTime(trade.entry_time)}</td><td><strong>{symbol(trade.instrument_id)}</strong></td><td>{trade.source === 'shadow_replay' ? 'SHADOW' : 'AUTO'}</td><td>{trade.quantity ?? '—'}</td><td className={Number(trade.realized_pnl ?? 0) < 0 ? 'negative' : 'positive'}>{trade.realized_pnl == null ? '—' : money(trade.realized_pnl, currency, true)}</td><td className={Number(trade.r_result) < 0 ? 'negative' : 'positive'}>{signed(trade.r_result, 2, 'R')}</td><td><span className={Number(trade.r_result) >= 0 ? 'trade-outcome win' : 'trade-outcome loss'}>{Number(trade.r_result) >= 0 ? 'Win' : 'Loss'}</span></td><td>{trade.exit_reason ?? '—'}</td><td><button type="button" className="paper-trade-view-button" aria-label={`View ${symbol(trade.instrument_id)} trade evidence`} onClick={() => setSelectedTrade(trade)}>View</button></td></tr>)}{!tradeRows.length ? <tr><td colSpan={9}>No completed trades in this filter.</td></tr> : null}</tbody></table></div></article>
            <article className="paper-panel-card insights-card"><header><div><strong>Strategy / execution insights</strong><small>Filtered evidence; sample-derived metrics are shown only when the trade sample is complete</small></div></header><div className="paper-insight-grid"><Insight icon="↗" label="Profit factor" value={fixed(summary?.profit_factor, 2)} /><Insight icon="◉" label="Sharpe ratio" value={sharpe === null ? '—' : sharpe.toFixed(2)} detail="Daily R annualized" /><Insight icon="↗" label="Sortino ratio" value={sortino === null ? '—' : sortino.toFixed(2)} detail="Daily R annualized" /><Insight icon="↟" label="Avg win" value={averageWin === null ? '—' : signed(averageWin, 2, 'R')} /><Insight icon="↡" label="Avg loss" value={averageLoss === null ? '—' : signed(averageLoss, 2, 'R')} /><Insight icon="↗" label="Payoff ratio" value={payoffRatio === null ? '—' : payoffRatio.toFixed(2)} /><Insight icon="Σ" label="Avg trade" value={signed(summary?.expectancy_r, 3, 'R')} /><Insight icon="◷" label="Trade duration" value={durationLabel(averageDuration)} /><Insight icon="▲" label="Profitability" value={pct(summary?.win_rate, 2)} /></div></article>
          </div>

          <div className="paper-overview-support">
            <article><header><div><strong>Regime comparison</strong><small>SHADOW and AUTO PAPER stay statistically separate</small></div></header><div className="paper-execution-kpis"><Kpi label="SHADOW" value={signed(data.mode_comparison.shadow.expectancy_r, 3, 'R')} detail={`${data.mode_comparison.shadow.trade_count} trades`} /><Kpi label="AUTO PAPER" value={signed(data.mode_comparison.auto_paper.expectancy_r, 3, 'R')} detail={`${data.mode_comparison.auto_paper.trade_count} trades`} /><Kpi label="Implementation delta" value={data.mode_comparison.expectancy_delta_r == null ? '—' : signed(data.mode_comparison.expectancy_delta_r, 3, 'R')} detail="AUTO − SHADOW" /></div></article>
            <article><header><div><strong>Rolling expectancy</strong><small>{rollingWindow}-trade window · +0.20R V2 threshold</small></div><label><span>Window</span><select value={rollingWindow} onChange={(event) => setRollingWindow(Number(event.target.value))}><option value={20}>20</option><option value={30}>30</option><option value={50}>50</option></select></label></header><LineChart points={rollingPoints} references={[{ value: 0, label: '0R' }, { value: 0.2, label: '+0.20R' }]} area={false} /></article>
          </div>
        </> : null}

        {tab === 'diagnostics' ? <div className="paper-dashboard-grid">
          {data.qualification ? <div className="wide"><Qualification data={data.qualification} /></div> : null}
          <article><header><div><strong>R distribution</strong><small>Outcome shape matters more than win rate alone</small></div></header><Distribution rows={data.r_distribution} /></article>
          <article><header><div><strong>Rolling 90% lower bound</strong><small>Evidence reliability, not only point estimate</small></div></header><LineChart points={lcbPoints} references={[{ value: 0, label: 'must stay > 0R' }]} area={false} /></article>
          <article className="wide"><header><div><strong>MAE vs MFE</strong><small>Validate stop, protection and target geometry</small></div></header><MaeMfeScatter rows={data.mae_mfe} /></article>
          <article className="wide"><header><div><strong>Candidate lifecycle</strong><small>One strategy/profile/session/universe/instrument lifecycle — not raw event counts</small></div></header><div className="paper-funnel">{data.funnel.map((stage, index) => <div key={stage.stage}><span>{stage.stage}</span><strong>{stage.count}</strong><small>{index === 0 ? 'Captured lifecycle' : stage.conversion_from_previous == null ? '—' : `${(Number(stage.conversion_from_previous) * 100).toFixed(0)}% from prior`}</small>{stage.dominant_drop_reason ? <em>{stage.dominant_drop_count} stopped: {stage.dominant_drop_reason}</em> : null}</div>)}</div></article>
          <article className="wide"><header><div><strong>Factor studies</strong><small>Persisted setup-feature outcome buckets</small></div></header>{data.factors.length ? <div className="paper-factor-studies">{data.factors.map((study) => <section key={study.factor}><strong>{study.factor}</strong><table><thead><tr><th>Bucket</th><th>N</th><th>Expectancy</th><th>Win rate</th></tr></thead><tbody>{study.buckets.map((bucket) => <tr key={bucket.label}><td>{bucket.label}</td><td>{bucket.count}</td><td className={Number(bucket.expectancy_r) >= 0 ? 'positive' : 'negative'}>{signed(bucket.expectancy_r, 3, 'R')}</td><td>{pct(bucket.win_rate)}</td></tr>)}</tbody></table></section>)}</div> : <div className="paper-dashboard-empty">Factor buckets populate as completed AUTO PAPER trades retain setup features.</div>}</article>
        </div> : null}

        {tab === 'execution' ? <div className="paper-dashboard-grid">
          <article className="wide"><header><div><strong>Expected → observed → realized</strong><small>Separate signal deterioration, fill slippage and implementation shortfall</small></div></header><div className="paper-execution-kpis"><Kpi label="AUTO PAPER trades" value={String(data.execution.trade_count)} /><Kpi label="Signal → executable" value={data.execution.average_signal_to_executable_bps == null ? '—' : `${fixed(data.execution.average_signal_to_executable_bps, 1)} bps`} /><Kpi label="Execution → fill" value={data.execution.average_fill_slippage_bps == null ? '—' : `${fixed(data.execution.average_fill_slippage_bps, 1)} bps`} /><Kpi label="Total shortfall" value={data.execution.average_implementation_shortfall_bps == null ? '—' : `${fixed(data.execution.average_implementation_shortfall_bps, 1)} bps`} /></div></article>
          <article><header><div><strong>Gross exposure</strong><small>Mark-to-market notional over time</small></div></header><LineChart points={exposurePoints} formatAxis={(value) => value.toLocaleString(undefined, { maximumFractionDigits: 0 })} /></article>
          <article><header><div><strong>Risk at stop</strong><small>Persisted protection risk over time</small></div></header><LineChart points={riskPoints} formatAxis={(value) => value.toLocaleString(undefined, { maximumFractionDigits: 0 })} tone="danger" /></article>
          <article className="wide"><header><div><strong>Simulation history</strong><small>Reset starts a new immutable epoch instead of erasing evidence</small></div></header><div className="paper-epochs">{epochs.map((epoch) => <div key={epoch.epoch_id} data-current={epoch.is_current ? 'true' : 'false'}><strong>Epoch {epoch.ordinal}</strong><span>{epoch.is_current ? 'CURRENT' : epoch.end_reason?.replaceAll('_', ' ') ?? 'closed'}</span><small>Initial {money(epoch.initial_cash, currency)} · {timeLabel(epoch.started_at)}{epoch.ended_at ? ` → ${timeLabel(epoch.ended_at)}` : ''}</small></div>)}</div></article>
        </div> : null}
      </> : <div className="paper-dashboard-empty">{status === 'loading' ? 'Loading durable paper analytics…' : 'No analytics available.'}</div>}

      {selectedTrade && accountId ? (
        <TradingTradeDrilldown trade={selectedTrade} accountId={accountId} currency={currency} onClose={() => setSelectedTrade(null)} />
      ) : null}
    </section>
  );
}
