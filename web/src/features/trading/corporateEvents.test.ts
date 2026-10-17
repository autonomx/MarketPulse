import { describe, expect, it } from 'vitest';
import { describeEvent, eventAnchors, type CorporateEvent, type EventBars } from './corporateEvents';

function barsAt(times: string[]): EventBars {
  const millis = times.map((time) => Date.parse(time));
  return {
    length: times.length,
    at: (position) => (times[position] ? { time: times[position] } : undefined),
    indexAtOrBefore: (time) => {
      const value = Date.parse(time);
      let found = -1;
      millis.forEach((bar, index) => { if (bar <= value) found = index; });
      return found;
    },
  };
}

const event = (kind: CorporateEvent['kind'], date: string, extra: Partial<CorporateEvent> = {}): CorporateEvent => ({
  kind, date, estimated: false, special: false, ...extra,
} as CorporateEvent);

const DAY = 86_400_000;
const daily = barsAt(['2026-07-28T04:00:00Z', '2026-07-29T04:00:00Z', '2026-07-30T04:00:00Z', '2026-07-31T04:00:00Z', '2026-08-03T04:00:00Z']);

describe('event anchors (TVP-10.1)', () => {
  it('puts an event on its day’s daily bar, one marker per kind and bar, and skips events before the bars', () => {
    const anchors = eventAnchors([
      event('earnings', '2026-07-30'), event('dividend', '2026-07-30'), event('dividend', '2026-07-30', { special: true }),
      event('split', '2026-08-01'), event('earnings', '2026-04-30'),
    ], daily, { intraday: false, stepMs: DAY, upcoming: true });
    expect(anchors.map((anchor) => [anchor.kind, anchor.index, anchor.events.length])).toEqual([
      ['earnings', 2, 1], ['dividend', 2, 2], ['split', 3, 1], // a Saturday split sits on Friday's bar
    ]);
  });

  it('puts upcoming events past the last bar, except in replay', () => {
    const upcoming = [event('earnings', '2026-08-20', { estimated: true })];
    expect(eventAnchors(upcoming, daily, { intraday: false, stepMs: DAY, upcoming: true })).toEqual([
      expect.objectContaining({ index: null, time: '2026-08-20T04:00:00.000Z' }),
    ]);
    expect(eventAnchors(upcoming, daily, { intraday: false, stepMs: DAY, upcoming: false })).toEqual([]);
  });

  it('uses the day’s first intraday bar, and nothing on a day the bars skip', () => {
    const intraday = barsAt(['2026-07-29T19:00:00Z', '2026-07-30T13:30:00Z', '2026-07-30T14:30:00Z', '2026-08-03T13:30:00Z']);
    const anchors = eventAnchors([event('earnings', '2026-07-30'), event('dividend', '2026-07-31')], intraday, { intraday: true, stepMs: 3_600_000, upcoming: true });
    expect(anchors.map((anchor) => [anchor.kind, anchor.index])).toEqual([['earnings', 1]]);
  });

  it('describes each kind', () => {
    expect(describeEvent(event('earnings', '2026-07-30', { fiscal_period: 'Q3 FY2026', timing: 'after_close' }))).toBe('Earnings Q3 FY2026 · Jul 30, 2026, after the close');
    expect(describeEvent(event('earnings', '2026-10-29', { estimated: true }))).toBe('Estimated earnings · Oct 29, 2026');
    expect(describeEvent(event('dividend', '2026-08-11', { amount: 0.26, payable_date: '2026-08-14' }))).toBe('Dividend $0.26 · ex-date Aug 11, 2026, paid Aug 14, 2026');
    expect(describeEvent(event('split', '2020-08-31', { split_from: 1, split_to: 4 }))).toBe('Split 4:1 · Aug 31, 2020');
  });
});
