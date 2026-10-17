import { describe, expect, it } from 'vitest';
import { dividendTotals, keyStats, performance, reportedEps, type DailyBar } from './advancedView';
import type { CorporateEvent } from './corporateEvents';

/** One bar a day from `start`, closing at `closes[i]`. */
function daily(start: string, closes: number[]): DailyBar[] {
  const first = Date.parse(`${start}T04:00:00Z`);
  return closes.map((close, index) => ({
    start_time: new Date(first + index * 86_400_000).toISOString(), high: close + 1, low: close - 1, close, volume: 1_000 + index,
  }));
}

const event = (kind: CorporateEvent['kind'], date: string, extra: Partial<CorporateEvent> = {}) => ({ kind, date, estimated: false, special: false, ...extra }) as CorporateEvent;

describe('advanced view figures (TVP-5.4)', () => {
  it('reads key statistics from the daily bars', () => {
    const bars = daily('2025-01-01', Array.from({ length: 400 }, (_, index) => 100 + index));
    const stats = keyStats(bars)!;
    expect(stats.close).toBe(499);
    expect(stats.change).toBe(1);
    expect(stats.changePct).toBeCloseTo(100 / 498);
    expect(stats.yearLow).toBe(135 - 1); // the bars of the last 365 days
    expect(stats.yearHigh).toBe(500);
    expect(stats.averageVolume).toBe(1_000 + (369 + 398) / 2);
    expect(keyStats([])).toBeNull();
  });

  it('measures performance from the close at each period’s start, and leaves periods without history empty', () => {
    // 2025-12-31 closes at 100, then one point a day: 2026-02-10 (day 41) closes at 141.
    const bars = daily('2025-12-31', Array.from({ length: 42 }, (_, index) => 100 + index));
    const values = performance(bars);
    expect(values['1W']).toBeCloseTo((141 / 134 - 1) * 100);
    expect(values['1M']).toBeCloseTo((141 / 110 - 1) * 100); // from 2026-01-10
    expect(values.YTD).toBeCloseTo(41);
    expect(values.All).toBeCloseTo(41);
    expect(values['3M']).toBeNull();
    expect(values['5Y']).toBeNull();
  });

  it('matches an earnings report to the quarter that ended before it', () => {
    const quarters = [
      { end: '2026-03-28', values: { eps_diluted: 1.5 } },
      { end: '2026-06-27', values: { eps_diluted: 1.62 } },
      { end: '2025-12-27', values: { eps_basic: 2.4 } },
    ];
    expect(reportedEps(event('earnings', '2026-07-30'), quarters)).toEqual({ periodEnd: '2026-06-27', eps: 1.62 });
    expect(reportedEps(event('earnings', '2026-01-29'), quarters)).toEqual({ periodEnd: '2025-12-27', eps: 2.4 });
    expect(reportedEps(event('earnings', '2026-12-30'), quarters)).toBeNull(); // nothing within 120 days
  });

  it('totals dividends by year and over the last twelve months', () => {
    const events = [
      event('dividend', '2025-08-11', { amount: 0.26 }), event('dividend', '2025-11-10', { amount: 0.26 }),
      event('dividend', '2026-02-09', { amount: 0.26 }), event('dividend', '2026-05-11', { amount: 0.27 }),
      event('dividend', '2026-12-01', { amount: 0.3 }), // declared, not yet paid out
      event('earnings', '2026-07-30'),
    ];
    const { years, trailing } = dividendTotals(events, new Date('2026-10-09T12:00:00Z'));
    expect(years.map((year) => [year.year, Number(year.total.toFixed(2)), year.count])).toEqual([[2026, 0.53, 2], [2025, 0.52, 2]]);
    expect(trailing).toBeCloseTo(0.79); // since 2025-10-09
  });
});
