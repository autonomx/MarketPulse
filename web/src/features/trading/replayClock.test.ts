import { describe, expect, it } from 'vitest';
import {
  DEFAULT_REPLAY_SPEED,
  REPLAY_MIN_TICK_MS,
  REPLAY_SPEEDS,
  barCloseTime,
  formatReplaySpeed,
  isReplaySpeed,
  nextReplayClock,
  parseReplaySpeed,
  previousReplayClock,
  replayBarAtClock,
  replayClockForBar,
  replayTickPlan,
  replayVisibleBars,
  replayVisibleCount,
} from './replayClock';
import type { MarketBar } from './tradingTypes';
import { fixture } from '../../test/fixture';

const MINUTE = 60_000;
const BASE = Date.parse('2026-08-05T12:00:00Z');

function bar(startMinute: number, minutes: number, interval: string): MarketBar {
  return fixture({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
    interval,
    start_time: new Date(BASE + startMinute * MINUTE).toISOString(),
    end_time: new Date(BASE + (startMinute + minutes) * MINUTE).toISOString(),
    open: '1',
    high: '1',
    low: '1',
    close: String(startMinute),
    volume: '1',
    is_final: true,
    adjustment_mode: 'raw',
    session: '24x7',
    provider: 'binance',
    ingestion_revision: 1,
    received_at: new Date(BASE + (startMinute + minutes) * MINUTE).toISOString(),
  });
}

/** Twelve 5-minute bars: 12:00–13:00. */
const fiveMinute = Array.from({ length: 12 }, (_, index) => bar(index * 5, 5, '5m'));
/** One-hour bars: 11:00–12:00, 12:00–13:00. */
const hourly = [bar(-60, 60, '1h'), bar(0, 60, '1h')];
/** One-minute bars with a gap between 12:03 and 12:10 (no trading). */
const gapped = [bar(0, 1, '1m'), bar(1, 1, '1m'), bar(2, 1, '1m'), bar(10, 1, '1m'), bar(11, 1, '1m')];
const at = (minute: number) => BASE + minute * MINUTE;

describe('replay clock to bars', () => {
  it('shows only bars that have closed by the clock, on every interval', () => {
    const clock = at(35);
    expect(replayVisibleBars(fiveMinute, clock).map((item) => item.close)).toEqual(['0', '5', '10', '15', '20', '25', '30']);
    // The 12:00–13:00 hourly bar is still open at 12:35, so it stays hidden.
    expect(replayVisibleBars(hourly, clock).map((item) => item.close)).toEqual(['-60']);
    expect(replayBarAtClock(hourly, clock)?.close).toBe('-60');
    expect(replayBarAtClock(fiveMinute, clock)?.close).toBe('30');
  });

  it('includes a bar the moment it closes', () => {
    expect(replayVisibleCount(fiveMinute, at(5))).toBe(1);
    expect(replayVisibleCount(fiveMinute, at(5) - 1)).toBe(0);
    expect(replayVisibleCount(hourly, at(60))).toBe(2);
  });

  it('holds the last bar before a gap until the next bar closes', () => {
    expect(replayBarAtClock(gapped, at(7))?.close).toBe('2');
    expect(replayVisibleCount(gapped, at(10.5))).toBe(3);
    expect(replayVisibleCount(gapped, at(11))).toBe(4);
  });

  it('shows nothing before the first bar closes', () => {
    expect(replayVisibleBars(fiveMinute, at(-30))).toEqual([]);
    expect(replayBarAtClock(fiveMinute, at(4))).toBeNull();
    expect(replayVisibleCount([], at(0))).toBe(0);
  });

  it('shows every bar after the last bar closes', () => {
    expect(replayVisibleBars(fiveMinute, at(500))).toHaveLength(fiveMinute.length);
    expect(replayBarAtClock(fiveMinute, at(500))?.close).toBe('55');
  });

  it('uses the bar close as the clock for a chosen start bar', () => {
    expect(replayClockForBar(fiveMinute[2])).toBe(at(15));
  });

  it('derives an unreadable close from the start and interval, never from the start alone', () => {
    const noEnd = { ...fiveMinute[2], end_time: 'not a time' };
    expect(barCloseTime(noEnd)).toBe(at(15));
    expect(replayVisibleCount([fiveMinute[0], fiveMinute[1], noEnd], at(10))).toBe(2);
    expect(replayVisibleCount([fiveMinute[0], fiveMinute[1], noEnd], at(15))).toBe(3);
  });

  it('never steps onto a last bar whose close cannot be known', () => {
    const unknown = { ...fiveMinute[11], end_time: '', interval: 'tick' };
    const series = [...fiveMinute.slice(0, 11), unknown];
    expect(nextReplayClock(series, at(55))).toBeNull();
    expect(nextReplayClock(series, at(50), 5)).toBe(at(55));
    expect(replayVisibleCount(series, at(55))).toBe(11);
  });

  it('keeps searching correctly past an unknowable bar in the middle', () => {
    const unknown = { ...fiveMinute[5], end_time: '', interval: 'tick' };
    const series = [...fiveMinute.slice(0, 5), unknown, ...fiveMinute.slice(6)];
    // The unknowable 12:25 bar counts as closed when the 12:30 bar starts.
    expect(replayVisibleCount(series, at(25))).toBe(5);
    expect(replayVisibleCount(series, at(30))).toBe(6);
    expect(replayVisibleCount(series, at(45))).toBe(9);
    expect(nextReplayClock(series, at(25))).toBe(at(30));
    expect(nextReplayClock(series, at(30))).toBe(at(35));
    expect(previousReplayClock(series, at(35), at(0))).toBe(at(30));
    expect(replayBarAtClock(series, at(32))).toBe(unknown);
  });

  it('never reveals a bar whose close cannot be known', () => {
    const unknown = { ...fiveMinute[11], end_time: '', interval: 'tick' };
    expect(barCloseTime(unknown)).toBe(Number.POSITIVE_INFINITY);
    expect(replayClockForBar(unknown)).toBeNull();
    expect(replayVisibleCount([...fiveMinute.slice(0, 11), unknown], at(10_000))).toBe(11);
  });
});

describe('replay steps', () => {
  it('steps forward to the next close of the stepping chart', () => {
    expect(nextReplayClock(fiveMinute, at(15))).toBe(at(20));
    // A clock set from a finer chart moves to the coarser chart's next close.
    expect(nextReplayClock(fiveMinute, at(17))).toBe(at(20));
    expect(nextReplayClock(hourly, at(17))).toBe(at(60));
  });

  it('steps several bars at once and stops on the last bar', () => {
    expect(nextReplayClock(fiveMinute, at(15), 3)).toBe(at(30));
    expect(nextReplayClock(fiveMinute, at(50), 10)).toBe(at(60));
    expect(nextReplayClock(fiveMinute, at(60))).toBeNull();
  });

  it('steps across gaps to the next bar that exists', () => {
    expect(nextReplayClock(gapped, at(3))).toBe(at(11));
    expect(previousReplayClock(gapped, at(11), at(1))).toBe(at(3));
  });

  it('steps back one bar but never before the replay start', () => {
    expect(previousReplayClock(fiveMinute, at(30), at(10))).toBe(at(25));
    expect(previousReplayClock(fiveMinute, at(17), at(10))).toBe(at(15));
    expect(previousReplayClock(fiveMinute, at(12), at(11))).toBe(at(11));
    expect(previousReplayClock(fiveMinute, at(10), at(10))).toBeNull();
    expect(previousReplayClock(hourly, at(60), at(30))).toBe(at(30));
  });
});

describe('replay speeds', () => {
  it('offers nine fixed speeds with 1× as the default', () => {
    expect(REPLAY_SPEEDS).toEqual([0.1, 0.3, 0.5, 1, 3, 5, 10, 30, 100]);
    expect(DEFAULT_REPLAY_SPEED).toBe(1);
    expect(REPLAY_SPEEDS.map(formatReplaySpeed)).toContain('0.1×');
    expect(isReplaySpeed(3)).toBe(true);
    expect(isReplaySpeed(2)).toBe(false);
  });

  it('maps any value to the nearest supported speed', () => {
    expect(parseReplaySpeed('10')).toBe(10);
    expect(parseReplaySpeed(2)).toBe(3);
    expect(parseReplaySpeed(8)).toBe(10);
    expect(parseReplaySpeed(0.25)).toBe(0.3);
    expect(parseReplaySpeed(1_000)).toBe(100);
    expect(parseReplaySpeed('fast')).toBe(DEFAULT_REPLAY_SPEED);
    expect(parseReplaySpeed(0)).toBe(DEFAULT_REPLAY_SPEED);
  });

  it('plays the speed in bars per second without ticking faster than the minimum tick', () => {
    expect(replayTickPlan(0.1)).toEqual({ intervalMs: 10_000, barsPerTick: 1 });
    expect(replayTickPlan(1)).toEqual({ intervalMs: 1_000, barsPerTick: 1 });
    expect(replayTickPlan(5)).toEqual({ intervalMs: 200, barsPerTick: 1 });
    expect(replayTickPlan(10)).toEqual({ intervalMs: REPLAY_MIN_TICK_MS, barsPerTick: 1 });
    expect(replayTickPlan(30)).toEqual({ intervalMs: REPLAY_MIN_TICK_MS, barsPerTick: 3 });
    expect(replayTickPlan(100)).toEqual({ intervalMs: REPLAY_MIN_TICK_MS, barsPerTick: 10 });
    for (const speed of REPLAY_SPEEDS) {
      const plan = replayTickPlan(speed);
      expect(plan.barsPerTick * 1_000 / plan.intervalMs).toBeCloseTo(speed, 0);
    }
  });
});
