/**
 * The earnings and dividends calendar (TVP-10.1): the earnings, dividends and splits of a watchlist's stocks (or the
 * open charts') by day. A long list fills over a few requests; upcoming earnings dates are estimates.
 */
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import { api } from './api/gateway';
import { describeEvent, EVENT_KINDS, EVENT_LABEL, EVENT_LETTER, isStockInstrument, type CorporateEventCalendar, type CorporateEventKind } from './corporateEvents';
import { calendarWindow } from './TradingEconomicCalendar';
import { tradingApi } from './tradingApi';
import { upgradeWatchlistPayload, watchlistSymbolIds } from './tradingWatchlistModel';
import './TradingEventsCalendar.css';

type Window = Parameters<typeof calendarWindow>[0];
const MAX_INSTRUMENTS = 200;

const symbolOf = (instrumentId: string) => instrumentId.split(':').at(-1) ?? instrumentId;

type Props = {
  /** The open charts' instruments, the default scope. */
  chartInstrumentIds: readonly string[];
  onShowInstrument?: (instrumentId: string) => void;
};

export function TradingEventsCalendar({ chartInstrumentIds, onShowInstrument }: Props) {
  const [window, setWindow] = useState<Window>('this-week');
  const [scope, setScope] = useState('charts');
  const [kinds, setKinds] = useState<CorporateEventKind[]>([...EVENT_KINDS]);
  const watchlists = useQuery({
    queryKey: ['trading', 'documents', 'watchlists', 'event-calendar'],
    queryFn: async () => (await tradingApi.documents('watchlists')).map((record) => {
      const payload = upgradeWatchlistPayload(record.payload);
      return { id: record.record_id, name: payload.name, instrumentIds: watchlistSymbolIds(payload) };
    }),
    staleTime: 60_000,
  });
  const instrumentIds = useMemo(() => {
    const source = scope === 'charts' ? chartInstrumentIds : watchlists.data?.find((list) => list.id === scope)?.instrumentIds ?? [];
    return [...new Set(source.filter(isStockInstrument))].slice(0, MAX_INSTRUMENTS);
  }, [chartInstrumentIds, scope, watchlists.data]);
  const range = useMemo(() => calendarWindow(window, new Date()), [window]);
  const query = useQuery({
    queryKey: ['trading', 'corporate-events', 'calendar', range.start, range.end, kinds, instrumentIds],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/corporate-events/calendar', {
      params: { query: { ...range, instrument_id: instrumentIds, kind: kinds } },
    }), 'Earnings calendar') as Promise<CorporateEventCalendar>,
    enabled: instrumentIds.length > 0 && kinds.length > 0,
    staleTime: 10 * 60_000,
    // A long list arrives over a few calls: ask again while some of it is pending.
    refetchInterval: (current) => ((current.state.data?.pending.length ?? 0) > 0 ? 3_000 : false),
  });
  const days = useMemo(() => {
    const grouped = new Map<string, CorporateEventCalendar['events']>();
    for (const event of query.data?.events ?? []) grouped.set(event.date, [...(grouped.get(event.date) ?? []), event]);
    return [...grouped.entries()];
  }, [query.data]);
  const toggleKind = (kind: CorporateEventKind, on: boolean) => setKinds((current) => EVENT_KINDS.filter((item) => (item === kind ? on : current.includes(item))));
  const pending = query.data?.pending.length ?? 0;
  return (
    <section className="trading-events-calendar" aria-label="Earnings and dividends calendar">
      <header>
        <select aria-label="Stocks" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="charts">Open charts</option>
          {(watchlists.data ?? []).map((list) => <option key={list.id} value={list.id}>Watchlist: {list.name}</option>)}
        </select>
        <select aria-label="Calendar window" value={window} onChange={(event) => setWindow(event.target.value as Window)}>
          <option value="last-week">Last week</option><option value="this-week">This week</option><option value="next-week">Next week</option><option value="month">Next four weeks</option>
        </select>
        {EVENT_KINDS.map((kind) => (
          <label key={kind}><input type="checkbox" checked={kinds.includes(kind)} onChange={(event) => toggleKind(kind, event.target.checked)} />{EVENT_LABEL[kind]}</label>
        ))}
        <span>US stocks · SEC filings and Alpaca</span>
      </header>
      {instrumentIds.length === 0 ? <p>No US stocks in {scope === 'charts' ? 'the open charts' : 'this watchlist'}.</p> : null}
      {query.isLoading ? <p role="status">Loading the calendar…</p> : null}
      {query.isError ? <p role="alert">{query.error instanceof Error ? query.error.message : 'The calendar could not load.'}</p> : null}
      {pending > 0 ? <p role="status">Loading {pending} more {pending === 1 ? 'stock' : 'stocks'}…</p> : null}
      {query.data && days.length === 0 && pending === 0 ? <p>Nothing in this window.</p> : null}
      {days.map(([day, events]) => (
        <div key={day} className="trading-events-calendar-day">
          <h3>{new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' })}</h3>
          <table aria-label={`Events on ${day}`}>
            <tbody>
              {events.map((event) => (
                <tr key={`${event.instrument_id}-${event.kind}-${event.date}`}>
                  <td><span className={`trading-events-calendar-kind is-${event.kind}${event.estimated ? ' is-estimated' : ''}`} aria-hidden="true">{EVENT_LETTER[event.kind]}</span></td>
                  <td>
                    {onShowInstrument
                      ? <button type="button" onClick={() => onShowInstrument(event.instrument_id)} aria-label={`Show ${symbolOf(event.instrument_id)} on the chart`}>{symbolOf(event.instrument_id)}</button>
                      : symbolOf(event.instrument_id)}
                  </td>
                  <td>{event.link ? <a href={event.link} target="_blank" rel="noreferrer">{describeEvent(event)}</a> : describeEvent(event)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <footer>Earnings dates are the companies’ SEC 8-K filings (item 2.02); dates still to come are estimated from the same quarter a year earlier. Dividends and splits: Alpaca corporate actions, by ex-date.</footer>
    </section>
  );
}
