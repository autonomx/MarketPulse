import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import { fixture } from '../../../test/fixture';
import {
  autoAnchoredVwap, chopZoneColors, drawingIndicatorOutputs, keyLevels, moonEvents, seasonality, tradingSessionShading, zigzag,
} from './drawingIndicators';
import { UTC_SESSION } from './tradingSessions';
import { calculateTradingViewBuiltInOutputs, tradingViewBuiltInDefinition } from './tradingViewBuiltIns';

const MINUTE = 60_000;
const DAY = 86_400_000;

function bar(time: number, close: number, options: { high?: number; low?: number; open?: number; volume?: number; step?: number } = {}): MarketBar {
  return fixture({
    instrument_id: 'fixture',
    interval: '5m',
    start_time: new Date(time).toISOString(),
    end_time: new Date(time + (options.step ?? 5 * MINUTE)).toISOString(),
    open: String(options.open ?? close - 0.15),
    high: String(options.high ?? close + 0.4),
    low: String(options.low ?? close - 0.4),
    close: String(close),
    volume: String(options.volume ?? 1_000),
    is_final: true,
    adjustment_mode: 'raw',
    session: 'regular',
    provider: 'fixture',
    ingestion_revision: 1,
    received_at: new Date(time).toISOString(),
  });
}

function barsFromPrices(prices: readonly number[], start = Date.UTC(2026, 0, 2, 14), step = 5 * MINUTE): MarketBar[] {
  return prices.map((price, index) => bar(start + index * step, price, { step }));
}

// Swings: highs at bars 4, 14, 21 and lows at 8, 18 (high = close + 0.4, low = close - 0.4).
const SWING_PRICES = [10, 11, 12, 13, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 15, 14, 13, 12, 13, 14, 15, 14, 13];
const swingBars = barsFromPrices(SWING_PRICES);
const series = (bars: readonly MarketBar[], key: 'high' | 'low' | 'close' | 'volume') => bars.map((item) => Number(item[key]));

function outputs(name: string, bars: readonly MarketBar[], period: number, params: Record<string, number | string> = {}) {
  const result = drawingIndicatorOutputs(name, 'tv-test', period, params, bars, UTC_SESSION);
  expect(result).not.toBeNull();
  return result!;
}

describe('zigzag swings', () => {
  it('finds alternating pivot highs and lows', () => {
    const swings = zigzag(series(swingBars, 'high'), series(swingBars, 'low'), series(swingBars, 'close'), 4, 0);
    expect(swings.map((swing) => [swing.index, swing.high])).toEqual([[4, true], [8, false], [14, true], [18, false], [21, true]]);
    expect(swings[2].price).toBeCloseTo(16.4);
    expect(swings[3].price).toBeCloseTo(11.6);
  });

  it('drops swings smaller than the deviation x ATR', () => {
    const swings = zigzag(series(swingBars, 'high'), series(swingBars, 'low'), series(swingBars, 'close'), 4, 100);
    // Before ATR(10) is ready (bar 9) every swing counts; after it, none moves 100 x ATR.
    expect(swings.map((swing) => swing.index)).toEqual([4, 8]);
  });
});

describe('auto fib, pitchfork and trendlines', () => {
  it('retraces the last swing: 0 at the last swing, 1 at the one before, from the earlier swing on', () => {
    const levels = outputs('Auto Fib Retracement', swingBars, 4, { deviation: 0 });
    expect(levels).toHaveLength(7);
    const at = (level: number) => levels.find((item) => item.key === `tv-test:level-${level}`)!;
    expect(at(0).points[0].value).toBeCloseTo(15.4);
    expect(at(1).points[0].value).toBeCloseTo(11.6);
    expect(at(0.5).points[0].value).toBeCloseTo(13.5);
    expect(at(0.5).points[0].time).toBe(swingBars[18].start_time);
    expect(at(0.5).points).toHaveLength(swingBars.length - 18);
    expect(at(0.5).render).toBe('levels');
  });

  it('extends the A-B move from C', () => {
    const levels = outputs('Auto Fib Extension', swingBars, 4, { deviation: 0 });
    const one = levels.find((item) => item.key === 'tv-test:level-1')!;
    expect(one.points[0].time).toBe(swingBars[21].start_time);
    expect(one.points[0].value).toBeCloseTo(15.4 + (11.6 - 16.4));
  });

  it('draws the pitchfork median from A through the middle of B-C, with tines parallel to it', () => {
    const [median, upper, lower] = outputs('Auto Pitchfork', swingBars, 4, { deviation: 0 });
    const slope = (13.5 - 16.4) / (19.5 - 14);
    expect(median.points[0]).toMatchObject({ time: swingBars[14].start_time });
    expect(median.points[0].value).toBeCloseTo(16.4);
    expect(median.points[1].value - median.points[0].value).toBeCloseTo(slope);
    expect(upper.points[0].time).toBe(swingBars[18].start_time);
    expect(upper.points[0].value).toBeCloseTo(11.6);
    expect(upper.points[1].value - upper.points[0].value).toBeCloseTo(slope);
    expect(lower.points[0].value).toBeCloseTo(15.4);
  });

  it('draws resistance through the last two swing highs and support through the last two lows', () => {
    const [resistance, support] = outputs('Auto Trendlines', swingBars, 2);
    expect(resistance.points[0]).toMatchObject({ time: swingBars[14].start_time });
    expect(resistance.points[7].value).toBeCloseTo(15.4);
    expect(support.points[0]).toMatchObject({ time: swingBars[8].start_time });
    expect(support.points[10].value).toBeCloseTo(11.6);
  });

  it('draws nothing without enough swings', () => {
    const flat = barsFromPrices(Array.from({ length: 30 }, () => 10));
    expect(outputs('Auto Fib Retracement', flat, 10, { deviation: 3 })).toEqual([]);
    expect(outputs('Auto Pitchfork', flat, 10, { deviation: 3 })).toEqual([]);
    expect(outputs('Auto Trendlines', flat, 5)).toEqual([]);
  });
});

describe('key levels and auto anchored VWAP', () => {
  it('groups swing prices, the most touched first', () => {
    const cycle = [10, 11, 12, 13, 14, 13, 12, 11];
    const prices = [...cycle, ...cycle, ...cycle, ...cycle, 10, 11, 12, 13];
    const bars = barsFromPrices(prices);
    const levels = keyLevels(series(bars, 'high'), series(bars, 'low'), series(bars, 'close'), 200, 2);
    expect(levels).toHaveLength(2);
    expect([...levels].sort((a, b) => a - b)[0]).toBeCloseTo(9.6);
    expect([...levels].sort((a, b) => a - b)[1]).toBeCloseTo(14.4);
  });

  it('anchors the VWAP at the lookback\'s highest high, lowest low or highest volume', () => {
    const high = [10, 12, 15, 11, 13];
    const low = [8, 9, 7, 9, 10];
    const close = [9, 11, 12, 10, 12];
    const volume = [100, 500, 200, 100, 300];
    const fromHigh = autoAnchoredVwap(high, low, close, volume, 5, 'highest-high');
    expect(fromHigh.slice(0, 2)).toEqual([null, null]);
    expect(fromHigh[2]).toBeCloseTo((15 + 7 + 12) / 3);
    const typical = [34 / 3, 10, 35 / 3];
    expect(fromHigh[4]).toBeCloseTo((typical[0] * 200 + typical[1] * 100 + typical[2] * 300) / 600);
    expect(autoAnchoredVwap(high, low, close, volume, 5, 'highest-volume')[0]).toBeNull();
    expect(autoAnchoredVwap(high, low, close, volume, 5, 'highest-volume')[1]).toBeCloseTo(32 / 3);
    expect(autoAnchoredVwap(high, low, close, volume, 2, 'lowest-low')[3]).toBeCloseTo(10);
  });
});

describe('bar and background colours', () => {
  it('colours Bollinger Bars outside the bands and draws dashed bands', () => {
    const bars = barsFromPrices([...Array.from({ length: 20 }, (_, i) => 100 + (i % 2) * 0.5), 110, 100, 90]);
    const [colors, upper, lower] = outputs('Bollinger Bars', bars, 20, { deviations: 2 });
    expect(colors.kind).toBe('bar-colors');
    expect(colors.points).toEqual([
      { time: bars[20].start_time, value: 1, color: '#26a69a' },
      { time: bars[22].start_time, value: 1, color: '#ef5350' },
    ]);
    expect(upper.lineStyle).toBe('dashed');
    expect(lower.points).toHaveLength(4);
  });

  it('puts Chop Zone in the steepest colour on a steep trend, either way', () => {
    const up = barsFromPrices(Array.from({ length: 60 }, (_, i) => 100 + i));
    const down = barsFromPrices(Array.from({ length: 60 }, (_, i) => 160 - i));
    expect(chopZoneColors(series(up, 'high'), series(up, 'low'), series(up, 'close'), 30).at(-1)).toBe('#26c6da');
    expect(chopZoneColors(series(down, 'high'), series(down, 'low'), series(down, 'close'), 30).at(-1)).toBe('#d50000');
    const [zone] = outputs('Chop Zone', up, 30);
    expect(zone).toMatchObject({ kind: 'histogram', pane: 1 });
    expect(zone.points.at(-1)).toMatchObject({ value: 1, color: '#26c6da' });
  });

  it('shades Tokyo, London and New York in their own time zones, labelling each session\'s first bar', () => {
    const day = Date.UTC(2026, 0, 5);
    const times = Array.from({ length: 48 }, (_, i) => day + i * 30 * MINUTE);
    const shading = tradingSessionShading(times);
    const at = (hour: number, minute = 0) => shading[(hour * 60 + minute) / 30];
    expect(at(0)).toMatchObject({ name: 'Tokyo', first: true });
    expect(at(1)).toMatchObject({ name: 'Tokyo', first: false });
    expect(at(6)).toBeNull();
    expect(at(8)).toMatchObject({ name: 'London', first: true });
    expect(at(14, 30)).toMatchObject({ name: 'New York', first: true });
    expect(at(21)).toBeNull();
    // Regular-hours bars: each day's session is labelled after the overnight gap.
    const regular = [0, 1].flatMap((dayIndex) => [0, 1, 2].map((i) => Date.UTC(2026, 0, 5 + dayIndex, 14, 30) + i * 30 * MINUTE));
    expect(tradingSessionShading(regular).map((item) => item?.first)).toEqual([true, false, false, true, false, false]);
    expect(tradingSessionShading(Array.from({ length: 5 }, (_, i) => day + i * DAY)).every((item) => item === null)).toBe(true);
    const [background] = outputs('Trading Sessions', times.map((time) => bar(time, 10, { step: 30 * MINUTE })), 1);
    expect(background.kind).toBe('background');
    expect(background.points.filter((point) => point.label).map((point) => point.label)).toEqual(['Tokyo', 'London', 'New York']);
  });

  it('tints each higher-timeframe period and draws its high, low and open', () => {
    const start = Date.UTC(2026, 0, 2, 22);
    const bars = [100, 102, 101, 99, 98, 97, 96, 95].map((price, i) => bar(start + i * 60 * MINUTE, price, { open: price, step: 60 * MINUTE }));
    const [candles, high, low, open] = outputs('Multi-Time Period Charts indicator', bars, 1, { period: 'D' });
    expect(candles.kind).toBe('background');
    expect(candles.points).toHaveLength(8);
    expect(candles.points[0].color).toBe(candles.points[1].color);
    expect(candles.points[0].color).not.toBe(candles.points[2].color);
    expect(high.points.map((point) => point.value)).toEqual([102.4, 102.4, 101.4, 101.4, 101.4, 101.4, 101.4, 101.4]);
    expect(low.points[0].value).toBeCloseTo(99.6);
    expect(open.points[2].value).toBe(101);
    expect(open.lineStyle).toBe('dotted');
  });

  it('gives Visible Average Price a viewport-average output without points', () => {
    expect(outputs('Visible Average Price', swingBars, 1)).toEqual([expect.objectContaining({ kind: 'viewport-average', points: [] })]);
  });
});

describe('moon phases and seasonality', () => {
  it('finds the new and full moons within minutes of the true phase', () => {
    const events = moonEvents(Date.UTC(2024, 0, 1), Date.UTC(2024, 1, 1));
    const newMoon = events.find((event) => !event.full)!;
    const fullMoon = events.find((event) => event.full)!;
    expect(Math.abs(newMoon.time - Date.UTC(2024, 0, 11, 11, 57))).toBeLessThan(10 * MINUTE);
    expect(Math.abs(fullMoon.time - Date.UTC(2024, 0, 25, 17, 54))).toBeLessThan(10 * MINUTE);
    const december = moonEvents(Date.UTC(2024, 11, 1), Date.UTC(2025, 1, 1));
    expect(Math.abs(december.find((event) => event.full)!.time - Date.UTC(2024, 11, 15, 9, 2))).toBeLessThan(10 * MINUTE);
    expect(Math.abs(december.find((event) => !event.full && event.time > Date.UTC(2025, 0, 15))!.time - Date.UTC(2025, 0, 29, 12, 36))).toBeLessThan(10 * MINUTE);
    expect(events).toHaveLength(2);
  });

  it('marks the moons on the bars they fall in', () => {
    const bars = Array.from({ length: 31 }, (_, i) => bar(Date.UTC(2024, 0, 1) + i * DAY, 100 + i, { step: DAY }));
    const [full, fresh] = outputs('Moon Phases', bars, 1);
    expect(full.render).toBe('markers');
    expect(full.points).toHaveLength(1);
    expect(fresh.points).toHaveLength(1);
    expect(full.points[0].time).toBe(new Date(Date.UTC(2024, 0, 25)).toISOString());
    expect(full.points[0].value).toBe(Number(bars.find((item) => item.start_time === full.points[0].time)!.high));
  });

  it('plots each year\'s change since its first bar at the current year\'s dates, on daily bars', () => {
    const start = Date.UTC(2024, 0, 1);
    const bars = Array.from({ length: 366 + 70 }, (_, i) => bar(start + i * DAY, 100 + i, { step: DAY }));
    const years = seasonality(bars, 1);
    expect(years.map((item) => item.year)).toEqual([2025, 2024]);
    const index2025 = 366 + 10;
    expect(years[0].values[366]).toBe(0);
    expect(years[0].values[index2025]).toBeCloseTo((476 / 466 - 1) * 100);
    expect(years[1].values[index2025]).toBeCloseTo(10);
    expect(years[1].values[0]).toBeNull();
    expect(seasonality(swingBars, 1)).toEqual([]);
    // Aligned by month and day: 1 March 2025 reads 2024's 1 March, not its 29 February.
    const march2025 = bars.findIndex((item) => item.start_time.startsWith('2025-03-01'));
    expect(years[1].values[march2025]).toBeCloseTo((Number(bars.find((item) => item.start_time.startsWith('2024-03-01'))!.close) / 100 - 1) * 100);
    // A year whose history starts mid-year is left out.
    expect(seasonality(bars.slice(40), 1).map((item) => item.year)).toEqual([2025]);
    const lines = outputs('Seasonality', bars, 1);
    expect(lines.map((item) => [item.title, item.pane])).toEqual([['2025', 1], ['2024', 1]]);
  });

  it('draws earlier years on to the end of the year past the last bar, on trading days', () => {
    const start = Date.UTC(2024, 0, 1);
    // Weekday bars only, through Friday 2025-12-19.
    const days = Array.from({ length: 719 }, (_, i) => start + i * DAY).filter((time) => ![0, 6].includes(new Date(time).getUTCDay()));
    const bars = days.map((time, i) => bar(time, 100 + i, { step: DAY }));
    expect(bars.at(-1)!.start_time).toBe('2025-12-19T00:00:00.000Z');
    const [current, previous] = seasonality(bars, 1);
    expect(current.ahead).toEqual([]);
    expect(previous.ahead.map((point) => point.time.slice(0, 10))).toEqual(['2025-12-22', '2025-12-23', '2025-12-24', '2025-12-25', '2025-12-26', '2025-12-29', '2025-12-30', '2025-12-31']);
    const dec31 = bars.find((item) => item.start_time.startsWith('2024-12-31'))!;
    expect(previous.ahead.at(-1)!.value).toBeCloseTo((Number(dec31.close) / 100 - 1) * 100);
    const [, line2024] = outputs('Seasonality', bars, 1);
    expect(line2024.points.at(-1)).toEqual(previous.ahead.at(-1));
  });
});

describe('dispatch', () => {
  it('returns nothing for empty bars and null for names it doesn\'t own', () => {
    expect(drawingIndicatorOutputs('Moon Phases', 'tv-test', 1, {}, [], UTC_SESSION)).toEqual([]);
    expect(drawingIndicatorOutputs('Multi-Time Period Charts indicator', 'tv-test', 1, { period: 'D' }, [], UTC_SESSION)).toEqual([]);
    expect(drawingIndicatorOutputs('Volume', 'tv-test', 1, {}, swingBars, UTC_SESSION)).toBeNull();
    expect(drawingIndicatorOutputs('Volume', 'tv-test', 1, {}, [], UTC_SESSION)).toBeNull();
  });

  it('is available in the built-in catalogue with its default inputs', () => {
    const names = ['auto-fib-extension', 'auto-fib-retracement', 'auto-key-levels', 'auto-pitchfork', 'auto-trendlines', 'bollinger-bars', 'chop-zone',
      'moon-phases', 'multi-time-period-charts-indicator', 'seasonality', 'trading-sessions', 'visible-average-price', 'vwap-auto-anchored'];
    const bars = barsFromPrices(Array.from({ length: 120 }, (_, i) => 100 + 5 * Math.sin(i / 4) + i * 0.05));
    for (const name of names) {
      const id = `tv-${name}`;
      const definition = tradingViewBuiltInDefinition(id);
      expect(definition?.available, id).toBe(true);
      expect(() => calculateTradingViewBuiltInOutputs(bars, { id, period: definition!.defaultPeriod })).not.toThrow();
    }
    expect(calculateTradingViewBuiltInOutputs(bars, { id: 'tv-vwap-auto-anchored', period: 50 })[0].points.length).toBeGreaterThan(0);
  });
});
