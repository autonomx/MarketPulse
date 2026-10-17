/**
 * The economic calendar (TVP-10.5): US data releases from FRED, past and scheduled, by day; the market-moving ones
 * marked, with their headline as first published and the value before it. FRED gives dates, not times.
 */
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';
import './TradingEconomicCalendar.css';

type Calendar = components['schemas']['EconomicCalendar'];
type Window = 'this-week' | 'next-week' | 'last-week' | 'month';

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The window's first and last day (weeks start on Monday). */
export function calendarWindow(window: Window, today: Date): { start: string; end: string } {
  const monday = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - ((today.getUTCDay() + 6) % 7)));
  const shift = (days: number) => new Date(monday.getTime() + days * 86_400_000);
  if (window === 'next-week') return { start: isoDay(shift(7)), end: isoDay(shift(13)) };
  if (window === 'last-week') return { start: isoDay(shift(-7)), end: isoDay(shift(-1)) };
  if (window === 'month') return { start: isoDay(shift(-7)), end: isoDay(shift(27)) };
  return { start: isoDay(monday), end: isoDay(shift(6)) };
}

const number = (value: number | null | undefined) => (value === null || value === undefined ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: 2 }));

export function TradingEconomicCalendar({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const [window, setWindow] = useState<Window>('this-week');
  const [importance, setImportance] = useState<'high' | 'all'>('high');
  const range = useMemo(() => calendarWindow(window, new Date()), [window]);
  const query = useQuery({
    queryKey: ['trading', 'economic-calendar', range.start, range.end, importance],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/economic-calendar', { params: { query: { ...range, importance } } }), 'Economic calendar') as Promise<Calendar>,
    staleTime: 30 * 60_000,
  });
  const days = useMemo(() => {
    const grouped = new Map<string, Calendar['events']>();
    for (const event of query.data?.events ?? []) grouped.set(event.date, [...(grouped.get(event.date) ?? []), event]);
    return [...grouped.entries()];
  }, [query.data]);
  const today = isoDay(new Date());
  return (
    <section className="trading-economic-calendar" aria-label="Economic calendar">
      <header>
        <select aria-label="Calendar window" value={window} onChange={(event) => setWindow(event.target.value as Window)}>
          <option value="last-week">Last week</option><option value="this-week">This week</option><option value="next-week">Next week</option><option value="month">Next four weeks</option>
        </select>
        <label><input type="checkbox" checked={importance === 'high'} onChange={(event) => setImportance(event.target.checked ? 'high' : 'all')} />Important only</label>
        <span>US releases · FRED</span>
      </header>
      {query.isLoading ? <p role="status">Loading the calendar…</p> : null}
      {query.isError ? <p role="alert">{query.error instanceof Error ? query.error.message : 'The calendar could not load.'}</p> : null}
      {query.data && !query.data.configured ? (
        <p className="trading-economic-calendar-setup">
          The economic calendar needs a free FRED API key.
          {onOpenSettings ? <button type="button" onClick={onOpenSettings}>Add it in settings</button> : ' Add it under Settings › Trading & Market Data.'}
        </p>
      ) : null}
      {query.data?.configured && days.length === 0 ? <p>No releases in this window.</p> : null}
      {days.map(([day, events]) => (
        <div key={day} className={`trading-economic-calendar-day${day === today ? ' is-today' : ''}`}>
          <h3>{new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric', timeZone: 'UTC' })}</h3>
          <table aria-label={`Releases on ${day}`}>
            <tbody>
              {events.map((event) => (
                <tr key={`${event.release_id}-${day}`} className={event.importance === 'high' ? 'is-high' : undefined}>
                  <td className="trading-economic-calendar-importance" aria-label={event.importance === 'high' ? 'Important' : 'Normal'}>{event.importance === 'high' ? '●●●' : '●'}</td>
                  <td><a href={event.link} target="_blank" rel="noreferrer">{event.name}</a></td>
                  <td>{event.series ? <>Actual <strong>{number(event.actual)}</strong></> : null}</td>
                  <td>{event.series ? <>Previous {number(event.previous)}</> : null}</td>
                  <td className="trading-economic-calendar-unit">{event.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {query.data?.configured ? <footer>{query.data.source}. Values are as first published.</footer> : null}
    </section>
  );
}
