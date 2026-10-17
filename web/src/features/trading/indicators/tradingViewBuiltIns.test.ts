import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import {
  TRADINGVIEW_BUILTIN_DEFINITIONS,
  calculateTradingViewBuiltInOutputs,
  isTradingViewBuiltInId,
  tradingViewBuiltInUsesCompareSeries,
} from './tradingViewBuiltIns';
import { fixture } from '../../../test/fixture';

function fixtureBars(count = 720): MarketBar[] {
  return fixture(Array.from({ length: count }, (_, index) => {
    const trend = 100 + index * 0.035;
    const wave = Math.sin(index / 9) * 4 + Math.sin(index / 29) * 7;
    const close = trend + wave;
    const open = close - Math.sin(index / 3) * 0.8;
    const high = Math.max(open, close) + 1.5 + (index % 5) * 0.08;
    const low = Math.min(open, close) - 1.4 - (index % 4) * 0.07;
    return {
      instrument_id: 'equity:NASDAQ:NVDA',
      interval: '1d',
      start_time: new Date(Date.UTC(2024, 0, index + 1)).toISOString(),
      end_time: new Date(Date.UTC(2024, 0, index + 2)).toISOString(),
      open: String(open),
      high: String(high),
      low: String(low),
      close: String(close),
      volume: String(1_000_000 + (index % 31) * 42_000 + index * 500),
      is_final: true,
      adjustment_mode: 'raw',
      session: 'regular',
      provider: 'fixture',
      ingestion_revision: 1,
      received_at: new Date().toISOString(),
    };
  }));
}

function hourlyBars(count: number, close: (index: number) => number, volume: (index: number) => number = () => 1_000): MarketBar[] {
  return fixture(Array.from({ length: count }, (_, index) => {
    const value = close(index);
    return {
      instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
      interval: '1h',
      start_time: new Date(Date.UTC(2026, 0, 5, index)).toISOString(),
      end_time: new Date(Date.UTC(2026, 0, 5, index + 1)).toISOString(),
      open: String(value),
      high: String(value + 1),
      low: String(value - 1),
      close: String(value),
      volume: String(volume(index)),
      is_final: true,
      adjustment_mode: 'raw',
      session: '24x7',
      provider: 'fixture',
      ingestion_revision: 1,
      received_at: '2026-01-05T00:00:00Z',
    };
  }));
}

describe('TradingView built-in indicator catalog', () => {
  it('mirrors the complete unique TradingView built-in support-folder catalog', () => {
    expect(TRADINGVIEW_BUILTIN_DEFINITIONS).toHaveLength(209);
    expect(new Set(TRADINGVIEW_BUILTIN_DEFINITIONS.map((definition) => definition.name)).size).toBe(209);
    expect(TRADINGVIEW_BUILTIN_DEFINITIONS.some((definition) => definition.name === '1 year active supply %')).toBe(true);
    expect(TRADINGVIEW_BUILTIN_DEFINITIONS.some((definition) => definition.name === 'Zig Zag')).toBe(true);
  });

  it('marks feed-dependent studies unavailable instead of fabricating their data', () => {
    const openInterest = TRADINGVIEW_BUILTIN_DEFINITIONS.find((definition) => definition.name === 'Open Interest');
    const analystForecast = TRADINGVIEW_BUILTIN_DEFINITIONS.find((definition) => definition.name === 'Analyst price forecast');
    expect(openInterest).toMatchObject({ available: false });
    expect(analystForecast).toMatchObject({ available: false });
    expect(openInterest?.requirement).toMatch(/data|feed|series/i);
  });

  it('calculates finite output for the OHLCV-backed catalog', () => {
    const bars = fixtureBars();
    const available = TRADINGVIEW_BUILTIN_DEFINITIONS.filter((definition) => definition.available && isTradingViewBuiltInId(definition.id));
    expect(available.length).toBeGreaterThan(70);
    for (const definition of available) {
      const outputs = calculateTradingViewBuiltInOutputs(bars, {
        id: definition.id,
        period: definition.defaultPeriod,
      });
      expect(outputs, definition.name).not.toHaveLength(0);
      expect(outputs.flatMap((output) => output.points).every((point) => Number.isFinite(point.value)), definition.name).toBe(true);
    }
  });

  it('makes the TVP-6.1 OHLCV indicators available', () => {
    const names = [
      'Rob Booker - ADX Breakout', 'Rob Booker - Knoxville Divergence', 'Rob Booker Intraday Pivot Points',
      'Rob Booker Missed Pivot Points', 'Rob Booker Reversal', 'Rob Booker Ziv Ghost Pivots',
      'Relative Volume at Time', '24-hour Volume', 'Correlation Coefficient (CC)',
    ];
    for (const name of names) {
      expect(TRADINGVIEW_BUILTIN_DEFINITIONS.find((definition) => definition.name === name), name).toMatchObject({ available: true });
    }
    expect(tradingViewBuiltInUsesCompareSeries('tv-correlation-coefficient-cc')).toBe(true);
    expect(tradingViewBuiltInUsesCompareSeries('tv-rob-booker-reversal')).toBe(false);
  });

  it('correlates with the compare series aligned by start time', () => {
    const bars = hourlyBars(60, (index) => 100 + Math.sin(index / 4) * 5);
    const id = 'tv-correlation-coefficient-cc';
    // The same closes, shuffled and with every 7th bar missing: sorted, aligned and carried forward.
    const compare = [...bars].reverse().filter((_, index) => index % 7 !== 3);
    const [aligned] = calculateTradingViewBuiltInOutputs(bars, { id, period: 20, compareSymbol: 'equity:NASDAQ:QQQ' }, { compareBars: bars.slice().reverse() });
    expect(aligned.points).toHaveLength(41);
    expect(aligned.points.every((point) => Math.abs(point.value - 1) < 1e-12)).toBe(true);
    const [gapped] = calculateTradingViewBuiltInOutputs(bars, { id, period: 20, compareSymbol: 'equity:NASDAQ:QQQ' }, { compareBars: compare });
    expect(gapped.points.length).toBeGreaterThan(0);
    expect(gapped.points.every((point) => point.value <= 1 && point.value >= -1)).toBe(true);
    expect(calculateTradingViewBuiltInOutputs(bars, { id, period: 20 }, { compareBars: bars })[0].points).toEqual([]);
    expect(calculateTradingViewBuiltInOutputs(bars, { id, period: 20, compareSymbol: 'equity:NASDAQ:QQQ' })[0].points).toEqual([]);
  });

  it('sums the last 24 hours of volume in quote currency once a whole day is loaded', () => {
    const bars = hourlyBars(30, () => 100, (index) => index + 1);
    const [volume] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-24-hour-volume', period: 1 });
    expect(volume.points[0]).toEqual({ time: bars[23].start_time, value: 300 * 100 });
    expect(volume.points.at(-1)).toEqual({ time: bars[29].start_time, value: (7 + 30) * 24 / 2 * 100 });
    // The price source converts base volume: high is close + 1.
    const [high] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-24-hour-volume', period: 1, params: { source: 'high' } });
    expect(high.points[0].value).toBe(300 * 101);
    // Bars longer than a day have no 24-hour volume until intrabar data exists.
    const weekly = bars.map((bar, index) => ({ ...bar, start_time: new Date(Date.UTC(2026, 0, 5 + 7 * index)).toISOString() }));
    expect(calculateTradingViewBuiltInOutputs(weekly, { id: 'tv-24-hour-volume', period: 1 })[0].points).toEqual([]);
  });

  it('plots pivots of the previous pivot period in hours, and the developing ghost pivots', () => {
    const bars = hourlyBars(48, (index) => 100 + index);
    const [hourly] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-rob-booker-intraday-pivot-points', period: 1 });
    // Each hourly bar gets the pivots of the bar before it: high 101, low 99, close 100.
    expect(hourly.points[0]).toEqual({ time: bars[1].start_time, value: (101 + 99 + 100) / 3 });
    expect(hourly.render).toBe('levels');
    const [eightHours] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-rob-booker-intraday-pivot-points', period: 8 });
    // 8-hour periods from 00:00 UTC: the first period's high 108, low 99, close 107.
    expect(eightHours.points[0]).toEqual({ time: bars[8].start_time, value: (108 + 99 + 107) / 3 });
    const ghostInputs = { id: 'tv-rob-booker-ziv-ghost-pivots', period: 1, params: { pivotPeriod: '480' } };
    const [ghost] = calculateTradingViewBuiltInOutputs(bars, ghostInputs);
    expect(ghost.points[1]).toEqual({ time: bars[1].start_time, value: (102 + 99 + 101) / 3 });
    expect(ghost.points[8]).toEqual({ time: bars[8].start_time, value: (109 + 107 + 108) / 3 });
  });

  it('signals Reversal when the MACD line crosses zero with slow %K beyond its level', () => {
    // Falling then rising: the MACD line crosses zero from below while %K is still low.
    const bars = hourlyBars(120, (index) => (index < 60 ? 200 - index : 140 + (index - 60) * 0.5));
    const outputs = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-rob-booker-reversal', period: 14, params: { lower: 101 } });
    const bullish = outputs.find((output) => output.key.endsWith(':bullish'))!;
    expect(bullish.points).toHaveLength(1);
    expect(bullish.render).toBe('markers');
    expect(calculateTradingViewBuiltInOutputs(bars, { id: 'tv-rob-booker-reversal', period: 14 })[0].points).toEqual([]);
  });

  it('draws the ADX Breakout box from the bars before the current one', () => {
    const bars = hourlyBars(60, (index) => 100 + (index % 2));
    const outputs = calculateTradingViewBuiltInOutputs([...bars, ...hourlyBars(61, () => 110).slice(60)], { id: 'tv-rob-booker-adx-breakout', period: 20 });
    const [upper, , up] = outputs;
    // A flat market keeps ADX low; the last bar's close (110) is above the box of the 20 bars before it, so it is a breakout.
    expect(upper.points.at(-1)?.value).toBe(102);
    expect(up.points.at(-1)).toEqual({ time: new Date(Date.UTC(2026, 0, 5, 60)).toISOString(), value: 110 });
  });

  it('compares cumulative volume with the same UTC time in earlier sessions', () => {
    const bars = hourlyBars(24 * 4, () => 100, (index) => (index >= 72 ? 200 : 100));
    const [ratio] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-relative-volume-at-time', period: 3 });
    expect(ratio.points).toHaveLength(24);
    expect(ratio.points.every((point) => point.value === 2)).toBe(true);
    const [regular] = calculateTradingViewBuiltInOutputs(bars, { id: 'tv-relative-volume-at-time', period: 3, params: { mode: 'regular' } });
    expect(regular.points.every((point) => point.value === 2)).toBe(true);
  });

  it('keeps Trend Strength Index in its documented -1 to +1 range', () => {
    const definition = TRADINGVIEW_BUILTIN_DEFINITIONS.find((item) => item.name === 'Trend Strength Index');
    expect(definition).toBeDefined();
    const points = calculateTradingViewBuiltInOutputs(fixtureBars(160), {
      id: definition!.id,
      period: definition!.defaultPeriod,
    }).flatMap((output) => output.points);
    expect(points.length).toBeGreaterThan(0);
    expect(points.every((point) => point.value >= -1 && point.value <= 1)).toBe(true);
  });
});
