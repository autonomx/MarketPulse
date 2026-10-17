/**
 * The advanced view (TVP-5.4): a list's symbols beside one symbol's overview, performance, earnings, dividends,
 * financials and news. Opened from the tools or a watchlist's menu.
 */
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import { dividendTotals, keyStats, performance, PERFORMANCE_PERIODS, reportedEps, type DailyBar } from './advancedView';
import { describeEvent, isStockInstrument, useCompanyEvents, type CorporateEvent } from './corporateEvents';
import { compact, TradingFinancials } from './TradingFinancials';
import { TradingNewsPanel } from './TradingNewsPanel';
import { tradingApi } from './tradingApi';
import { upgradeWatchlistPayload, watchlistSymbolIds } from './tradingWatchlistModel';
import './TradingAdvancedView.css';

type Financials = components['schemas']['Financials'];
type Tab = 'overview' | 'performance' | 'earnings' | 'dividends' | 'financials' | 'news';
type StatementRow = { end: string; values: Record<string, number | null> };

const STOCK_TABS: ReadonlyArray<[Tab, string]> = [['overview', 'Overview'], ['performance', 'Performance'], ['earnings', 'Earnings'], ['dividends', 'Dividends'], ['financials', 'Financials'], ['news', 'News']];
const OTHER_TABS: ReadonlyArray<[Tab, string]> = [['overview', 'Overview'], ['performance', 'Performance'], ['news', 'News']];

const symbolOf = (instrumentId: string) => instrumentId.split(':').at(-1) ?? instrumentId;
const price = (value: number | null | undefined) => (value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }));
const percent = (value: number | null | undefined) => (value === null || value === undefined || !Number.isFinite(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`);
const ratio = (value: number | null | undefined, digits = 2) => (value === null || value === undefined || !Number.isFinite(value) ? '—' : value.toFixed(digits));

function useFinancials(instrumentId: string) {
  return useQuery({
    queryKey: ['trading', 'financials', instrumentId],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/fundamentals', { params: { query: { instrument_id: instrumentId } } }), 'Financials') as Promise<Financials>,
    enabled: isStockInstrument(instrumentId),
    staleTime: 60 * 60_000,
    retry: false,
  });
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="trading-advanced-stat"><dt>{label}</dt><dd>{value}</dd></div>;
}

function Overview({ bars, financials, events }: { bars: DailyBar[]; financials: Financials | undefined; events: CorporateEvent[] }) {
  const stats = keyStats(bars);
  const today = new Date();
  const { trailing } = dividendTotals(events, today);
  const todayText = today.toISOString().slice(0, 10);
  const nextEarnings = events.find((event) => event.kind === 'earnings' && event.date >= todayText);
  const lastDividend = [...events].reverse().find((event) => event.kind === 'dividend' && event.date <= todayText);
  const ratios = financials?.ratios ?? {};
  if (!stats) return <p>No daily bars for this symbol.</p>;
  return (
    <div className="trading-advanced-overview">
      <p className="trading-advanced-price">
        <strong>{price(stats.close)}</strong>
        <span className={(stats.change ?? 0) >= 0 ? 'is-up' : 'is-down'}>{stats.change === null ? '' : `${stats.change >= 0 ? '+' : ''}${price(stats.change)} (${percent(stats.changePct)})`}</span>
      </p>
      {financials ? <p className="trading-advanced-company">{financials.name}{financials.sector ? ` · ${financials.sector}` : ''}{financials.industry ? ` · ${financials.industry}` : ''}</p> : null}
      <dl aria-label="Key statistics">
        <Stat label="Day range" value={`${price(stats.dayLow)} – ${price(stats.dayHigh)}`} />
        <Stat label="52-week range" value={`${price(stats.yearLow)} – ${price(stats.yearHigh)}`} />
        <Stat label="Volume" value={compact(stats.volume)} />
        <Stat label="Average volume (30 days)" value={compact(stats.averageVolume)} />
        {financials ? (
          <>
            <Stat label="Market cap" value={compact(ratios.market_cap)} />
            <Stat label="P/E (TTM)" value={ratio(ratios.pe_ratio)} />
            <Stat label="EPS (TTM)" value={ratio(ratios.eps_ttm)} />
            <Stat label="Revenue (TTM)" value={compact(ratios.revenue_ttm)} />
            <Stat label="Shares outstanding" value={compact(financials.shares_outstanding)} />
          </>
        ) : null}
        {trailing > 0 ? <Stat label="Dividend yield (TTM)" value={`${((trailing / stats.close) * 100).toFixed(2)}%`} /> : null}
        {nextEarnings ? <Stat label="Next earnings" value={describeEvent(nextEarnings)} /> : null}
        {lastDividend ? <Stat label="Last dividend" value={describeEvent(lastDividend)} /> : null}
      </dl>
    </div>
  );
}

function Performance({ bars }: { bars: DailyBar[] }) {
  const values = performance(bars);
  return (
    <ul className="trading-advanced-performance" aria-label="Performance">
      {PERFORMANCE_PERIODS.map(({ id }) => (
        <li key={id} className={values[id] === null ? '' : values[id]! >= 0 ? 'is-up' : 'is-down'}>
          <span>{id}</span><strong>{percent(values[id])}</strong>
        </li>
      ))}
    </ul>
  );
}

function Earnings({ events, financials }: { events: CorporateEvent[]; financials: Financials | undefined }) {
  const quarters = (financials?.quarterly?.income ?? []) as unknown as StatementRow[];
  const earnings = events.filter((event) => event.kind === 'earnings').reverse().slice(0, 16);
  const rows = earnings.map((event) => ({ event, reported: event.estimated ? null : reportedEps(event, quarters) }));
  const bars = rows.filter((row) => row.reported).slice(0, 8).reverse();
  const max = Math.max(...bars.map((row) => Math.abs(row.reported!.eps)), 0.01);
  if (earnings.length === 0) return <p>No earnings filings found for this company.</p>;
  return (
    <div className="trading-advanced-earnings">
      {bars.length ? (
        <div className="trading-advanced-eps" role="img" aria-label={`Diluted EPS: ${bars.map((row) => `${row.event.fiscal_period ?? row.reported!.periodEnd} ${row.reported!.eps}`).join(', ')}`}>
          {bars.map((row) => (
            <div key={row.event.date} title={`${row.event.fiscal_period ?? row.reported!.periodEnd}: ${row.reported!.eps}`}>
              <i className={row.reported!.eps < 0 ? 'is-loss' : ''} style={{ height: `${(Math.abs(row.reported!.eps) / max) * 100}%` }} />
              <small>{row.event.fiscal_period?.split(' ')[0] ?? row.reported!.periodEnd.slice(0, 7)}</small>
            </div>
          ))}
        </div>
      ) : null}
      <table aria-label="Earnings reports">
        <thead><tr><th>Report</th><th>EPS (diluted)</th></tr></thead>
        <tbody>
          {rows.map(({ event, reported }) => (
            <tr key={event.date} className={event.estimated ? 'is-estimated' : undefined}>
              <td>{event.link ? <a href={event.link} target="_blank" rel="noreferrer">{describeEvent(event)}</a> : describeEvent(event)}</td>
              <td>{reported ? ratio(reported.eps) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <small>Dates from SEC 8-K item 2.02 filings; EPS from the quarter's 10-Q or 10-K. A date still to come is estimated from the same quarter a year earlier.</small>
    </div>
  );
}

function Dividends({ events, close }: { events: CorporateEvent[]; close: number | null }) {
  const today = new Date();
  const { years, trailing } = dividendTotals(events, today);
  const dividends = events.filter((event) => event.kind === 'dividend').reverse().slice(0, 24);
  const splits = events.filter((event) => event.kind === 'split').reverse();
  if (dividends.length === 0 && splits.length === 0) return <p>No dividends or splits in the last ten years.</p>;
  return (
    <div className="trading-advanced-dividends">
      {trailing > 0 && close ? <p>Trailing twelve months: <strong>${trailing.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')}</strong> per share, a yield of <strong>{((trailing / close) * 100).toFixed(2)}%</strong>.</p> : null}
      {years.length ? (
        <table aria-label="Dividends per year">
          <thead><tr><th>Year</th><th>Total</th><th>Payments</th></tr></thead>
          <tbody>{years.map((year) => <tr key={year.year}><td>{year.year}</td><td>${year.total.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')}</td><td>{year.count}</td></tr>)}</tbody>
        </table>
      ) : null}
      <ul aria-label="Dividends and splits">
        {[...dividends, ...splits].sort((left, right) => right.date.localeCompare(left.date)).map((event) => <li key={`${event.kind}-${event.date}`}>{describeEvent(event)}</li>)}
      </ul>
    </div>
  );
}

function SymbolDetails({ instrumentId }: { instrumentId: string }) {
  const stock = isStockInstrument(instrumentId);
  const [tab, setTab] = useState<Tab>('overview');
  const history = useQuery({
    queryKey: ['trading', 'advanced-view', 'bars', instrumentId],
    queryFn: () => tradingApi.bars(instrumentId, '1d', 5_000, null),
    staleTime: 10 * 60_000,
  });
  const financials = useFinancials(instrumentId);
  const events = useCompanyEvents(instrumentId);
  const bars = (history.data?.bars ?? []) as DailyBar[];
  const eventList = events.data?.events ?? [];
  const tabs = stock ? STOCK_TABS : OTHER_TABS;
  const shown = tabs.some(([value]) => value === tab) ? tab : 'overview';
  return (
    <section className="trading-advanced-details" aria-label={`${symbolOf(instrumentId)} details`}>
      <header><h3>{symbolOf(instrumentId)}</h3><span>{instrumentId}</span></header>
      <nav role="tablist" aria-label="Symbol details">
        {tabs.map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={shown === value} onClick={() => setTab(value)}>{label}</button>)}
      </nav>
      <div role="tabpanel">
        {history.isLoading && (shown === 'overview' || shown === 'performance') ? <p role="status">Loading daily bars…</p> : null}
        {shown === 'overview' && !history.isLoading ? <Overview bars={bars} financials={financials.data} events={eventList} /> : null}
        {shown === 'performance' && !history.isLoading ? <Performance bars={bars} /> : null}
        {shown === 'earnings' ? (events.isLoading ? <p role="status">Loading SEC filings…</p> : <Earnings events={eventList} financials={financials.data} />) : null}
        {shown === 'dividends' ? (events.isLoading ? <p role="status">Loading corporate actions…</p> : <Dividends events={eventList} close={keyStats(bars)?.close ?? null} />) : null}
        {shown === 'financials' ? <TradingFinancials instrumentId={instrumentId} /> : null}
        {shown === 'news' ? <TradingNewsPanel key={instrumentId} instrumentId={instrumentId} /> : null}
      </div>
    </section>
  );
}

type Props = {
  /** The open charts' instruments, the list shown before a watchlist is picked. */
  chartInstrumentIds: readonly string[];
  /** The watchlist to show first (from a watchlist's menu). */
  initialWatchlistId?: string | null;
  onShowInstrument?: (instrumentId: string) => void;
};

export function TradingAdvancedView({ chartInstrumentIds, initialWatchlistId = null, onShowInstrument }: Props) {
  const [scope, setScope] = useState(initialWatchlistId ?? 'charts');
  const watchlists = useQuery({
    queryKey: ['trading', 'documents', 'watchlists', 'advanced-view'],
    queryFn: async () => (await tradingApi.documents('watchlists')).map((record) => {
      const payload = upgradeWatchlistPayload(record.payload);
      return { id: record.record_id, name: payload.name, instrumentIds: watchlistSymbolIds(payload) };
    }),
    staleTime: 60_000,
  });
  const instrumentIds = useMemo(
    () => [...new Set(scope === 'charts' ? chartInstrumentIds : watchlists.data?.find((list) => list.id === scope)?.instrumentIds ?? [])],
    [chartInstrumentIds, scope, watchlists.data],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const current = selected && instrumentIds.includes(selected) ? selected : instrumentIds[0] ?? null;
  return (
    <section className="trading-advanced-view" aria-label="Advanced view">
      <aside>
        <select aria-label="Symbols from" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="charts">Open charts</option>
          {(watchlists.data ?? []).map((list) => <option key={list.id} value={list.id}>Watchlist: {list.name}</option>)}
        </select>
        <ul aria-label="Symbols">
          {instrumentIds.map((instrumentId) => (
            <li key={instrumentId}>
              <button type="button" aria-pressed={instrumentId === current} onClick={() => setSelected(instrumentId)}>{symbolOf(instrumentId)}</button>
              {onShowInstrument ? <button type="button" className="trading-advanced-chart" onClick={() => onShowInstrument(instrumentId)} aria-label={`Show ${symbolOf(instrumentId)} on the chart`} title="Show on the chart">↗</button> : null}
            </li>
          ))}
        </ul>
        {instrumentIds.length === 0 ? <p>This list is empty.</p> : null}
      </aside>
      {current ? <SymbolDetails key={current} instrumentId={current} /> : null}
    </section>
  );
}
