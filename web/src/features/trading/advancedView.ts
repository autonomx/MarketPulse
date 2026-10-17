/**
 * The advanced view's figures (TVP-5.4), from a symbol's daily bars, financial statements and corporate events:
 * key statistics, performance over standard periods, reported EPS per earnings date and dividend totals.
 */
import { useEffect, useRef } from 'react';
import { emitOmnixEvent, onOmnixEvent } from '../../events/bus';
import type { CorporateEvent } from './corporateEvents';

declare module '../../events/bus' {
  interface OmnixEventMap {
    /** Opens the advanced view on a watchlist (by record id). */
    'omnix:trading-advanced-view': { watchlistId: string | null };
  }
}

export function requestAdvancedView(watchlistId: string | null): void {
  emitOmnixEvent('omnix:trading-advanced-view', { watchlistId });
}

export function useAdvancedViewRequests(open: (watchlistId: string | null) => void): void {
  const handler = useRef(open);
  useEffect(() => {
    handler.current = open;
  });
  useEffect(() => onOmnixEvent('omnix:trading-advanced-view', ({ watchlistId }) => handler.current(watchlistId)), []);
}

export type DailyBar = { start_time: string; high: string | number; low: string | number; close: string | number; volume: string | number };

const DAY = 86_400_000;

export type KeyStats = {
  close: number;
  change: number | null;
  changePct: number | null;
  dayLow: number;
  dayHigh: number;
  yearLow: number;
  yearHigh: number;
  volume: number;
  averageVolume: number | null;
};

/** The last bar's figures, the 52-week range and the 30-bar average volume; null without bars. */
export function keyStats(bars: readonly DailyBar[]): KeyStats | null {
  const last = bars.at(-1);
  if (!last) return null;
  const close = Number(last.close);
  const previous = bars.length > 1 ? Number(bars[bars.length - 2].close) : null;
  const yearStart = Date.parse(last.start_time) - 365 * DAY;
  const year = bars.filter((bar) => Date.parse(bar.start_time) > yearStart);
  const recent = bars.slice(-31, -1);
  return {
    close,
    change: previous === null ? null : close - previous,
    changePct: previous ? (close / previous - 1) * 100 : null,
    dayLow: Number(last.low),
    dayHigh: Number(last.high),
    yearLow: Math.min(...year.map((bar) => Number(bar.low))),
    yearHigh: Math.max(...year.map((bar) => Number(bar.high))),
    volume: Number(last.volume),
    averageVolume: recent.length ? recent.reduce((sum, bar) => sum + Number(bar.volume), 0) / recent.length : null,
  };
}

export const PERFORMANCE_PERIODS = [
  { id: '1W', days: 7 }, { id: '1M', months: 1 }, { id: '3M', months: 3 }, { id: '6M', months: 6 },
  { id: 'YTD' }, { id: '1Y', months: 12 }, { id: '5Y', months: 60 }, { id: 'All' },
] as const;

export type PerformancePeriod = (typeof PERFORMANCE_PERIODS)[number]['id'];

/** Change in percent from the close at or before each period's start to the last close; null without that history. */
export function performance(bars: readonly DailyBar[]): Record<PerformancePeriod, number | null> {
  const result = Object.fromEntries(PERFORMANCE_PERIODS.map((period) => [period.id, null])) as Record<PerformancePeriod, number | null>;
  const last = bars.at(-1);
  if (!last || bars.length < 2) return result;
  const end = new Date(last.start_time);
  const close = Number(last.close);
  const closeAtOrBefore = (time: number): number | null => {
    let found: number | null = null;
    for (const bar of bars) {
      if (Date.parse(bar.start_time) > time) break;
      found = Number(bar.close);
    }
    return found;
  };
  for (const period of PERFORMANCE_PERIODS) {
    let start: number | null;
    if (period.id === 'All') start = Date.parse(bars[0].start_time);
    else if (period.id === 'YTD') start = Date.UTC(end.getUTCFullYear(), 0, 1) - 1; // the last close of the year before
    else if ('days' in period) start = end.getTime() - period.days * DAY;
    else start = Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - period.months, end.getUTCDate(), end.getUTCHours());
    const base = start < Date.parse(bars[0].start_time) ? null : closeAtOrBefore(start);
    result[period.id] = base ? (close / base - 1) * 100 : null;
  }
  return result;
}

type StatementRow = { end: string; values: Record<string, number | null | undefined> };

/** The diluted (else basic) EPS of the quarter an earnings report covers: the last quarter ended within 120 days before it. */
export function reportedEps(event: CorporateEvent, quarters: readonly StatementRow[]): { periodEnd: string; eps: number } | null {
  const reportDay = Date.parse(`${event.date}T00:00:00Z`);
  let match: StatementRow | null = null;
  for (const row of quarters) {
    const end = Date.parse(`${row.end}T00:00:00Z`);
    if (end < reportDay && reportDay - end <= 120 * DAY && (!match || row.end > match.end)) match = row;
  }
  const eps = match?.values.eps_diluted ?? match?.values.eps_basic;
  return match && typeof eps === 'number' ? { periodEnd: match.end, eps } : null;
}

/** Dividends per year (by ex-date), newest first, and the trailing twelve months' total. */
export function dividendTotals(events: readonly CorporateEvent[], today: Date): { years: Array<{ year: number; total: number; count: number }>; trailing: number } {
  const dividends = events.filter((event) => event.kind === 'dividend' && typeof event.amount === 'number');
  const years = new Map<number, { total: number; count: number }>();
  let trailing = 0;
  const yearAgo = today.getTime() - 365 * DAY;
  for (const event of dividends) {
    const time = Date.parse(`${event.date}T00:00:00Z`);
    if (time > today.getTime()) continue;
    const year = Number(event.date.slice(0, 4));
    const entry = years.get(year) ?? { total: 0, count: 0 };
    entry.total += event.amount ?? 0;
    entry.count += 1;
    years.set(year, entry);
    if (time > yearAgo) trailing += event.amount ?? 0;
  }
  return { years: [...years.entries()].map(([year, entry]) => ({ year, ...entry })).sort((a, b) => b.year - a.year), trailing };
}
