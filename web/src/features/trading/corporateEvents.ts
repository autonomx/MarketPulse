/**
 * Earnings, dividends and splits (TVP-10.1): a stock's events from the server (SEC 8-K item 2.02 filings and Alpaca
 * corporate actions), where they sit on a chart's bars, and how they read.
 */
import { useQuery } from '@tanstack/react-query';
import { unwrapLabelled } from '../../api/http';
import type { components } from './api/generated';
import { api } from './api/gateway';

export type CorporateEvent = components['schemas']['CorporateEvent'];
export type CompanyEvents = components['schemas']['CompanyEvents'];
export type CorporateEventCalendar = components['schemas']['CorporateEventCalendar'];
export type CorporateEventKind = CorporateEvent['kind'];

export const EVENT_KINDS: readonly CorporateEventKind[] = ['earnings', 'dividend', 'split'];
export const EVENT_LETTER: Record<CorporateEventKind, string> = { earnings: 'E', dividend: 'D', split: 'S' };
export const EVENT_LABEL: Record<CorporateEventKind, string> = { earnings: 'Earnings', dividend: 'Dividend', split: 'Split' };

export function isStockInstrument(instrumentId: string): boolean {
  return instrumentId.toLowerCase().startsWith('equity:');
}

export function useCompanyEvents(instrumentId: string, enabled = true) {
  return useQuery({
    queryKey: ['trading', 'corporate-events', instrumentId],
    queryFn: () => unwrapLabelled(api.GET('/api/trading/corporate-events', { params: { query: { instrument_id: instrumentId } } }), 'Corporate events') as Promise<CompanyEvents>,
    enabled: enabled && isStockInstrument(instrumentId),
    staleTime: 60 * 60_000,
  });
}

/** The bars a chart shows, as the adapter's `drawingBars()` gives them. */
export type EventBars = {
  length: number;
  at: (position: number) => { time: string } | undefined;
  indexAtOrBefore: (time: string) => number;
};

/** Where events sit: on a bar (`index`), or past the last bar (`time`, for upcoming events). */
export type EventAnchor = { key: string; kind: CorporateEventKind; events: CorporateEvent[]; index: number | null; time: string };

/** About New York midnight on `day` (UTC 04:00; an hour early in winter, which only matters between bars). */
function newYorkDayStart(day: string): number {
  return Date.parse(`${day}T04:00:00Z`);
}

/**
 * Each kind's events per bar:
 * - daily and longer bars: the bar holding the day;
 * - intraday bars: the day's first bar (none when the bars skip that day);
 * - after the last bar: the day itself, unless `upcoming` is off (replay, where the future is hidden).
 */
export function eventAnchors(
  events: readonly CorporateEvent[],
  bars: EventBars,
  { intraday, stepMs, upcoming }: { intraday: boolean; stepMs: number | null; upcoming: boolean },
): EventAnchor[] {
  if (bars.length === 0) return [];
  const first = Date.parse(bars.at(0)?.time ?? '');
  const last = Date.parse(bars.at(bars.length - 1)?.time ?? '');
  const anchors = new Map<string, EventAnchor>();
  for (const event of events) {
    const start = newYorkDayStart(event.date);
    if (!Number.isFinite(start) || start + 86_400_000 <= first) continue;
    let index: number | null = null;
    let time: string;
    if (start >= last + (stepMs ?? 86_400_000)) {
      if (!upcoming) continue;
      time = new Date(start + (intraday ? 0 : last % 86_400_000 - 4 * 3_600_000)).toISOString();
    } else if (intraday) {
      index = bars.indexAtOrBefore(new Date(start - 1).toISOString()) + 1;
      const bar = bars.at(index);
      if (!bar || Date.parse(bar.time) >= start + 86_400_000) continue;
      time = bar.time;
    } else {
      index = bars.indexAtOrBefore(`${event.date}T23:59:59.999Z`);
      const bar = bars.at(index);
      if (!bar) continue;
      time = bar.time;
    }
    const key = `${event.kind}:${index ?? time}`;
    const anchor = anchors.get(key);
    if (anchor) anchor.events.push(event);
    else anchors.set(key, { key, kind: event.kind, events: [event], index, time });
  }
  return [...anchors.values()];
}

const formatDay = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const TIMING: Record<string, string> = { before_open: 'before the open', during_market: 'during the session', after_close: 'after the close' };

function trimNumber(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** One line describing an event, e.g. "Earnings Q3 FY2026 · Jul 30, 2026, after the close". */
export function describeEvent(event: CorporateEvent): string {
  if (event.kind === 'earnings') {
    const period = event.fiscal_period ? ` ${event.fiscal_period}` : '';
    const timing = event.timing ? `, ${TIMING[event.timing]}` : '';
    return `${event.estimated ? 'Estimated earnings' : 'Earnings'}${period} · ${formatDay(event.date)}${timing}`;
  }
  if (event.kind === 'dividend') {
    const amount = event.amount === null || event.amount === undefined ? '' : ` $${trimNumber(event.amount)}`;
    const pay = event.payable_date ? `, paid ${formatDay(event.payable_date)}` : '';
    return `${event.special ? 'Special dividend' : 'Dividend'}${amount} · ex-date ${formatDay(event.date)}${pay}`;
  }
  return `Split ${trimNumber(event.split_to ?? 1)}:${trimNumber(event.split_from ?? 1)} · ${formatDay(event.date)}`;
}
