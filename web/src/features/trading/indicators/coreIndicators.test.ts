import { describe, expect, it } from 'vitest';
import fixture from './fixtures/coreIndicators.json';
import {
  CORE_INDICATOR_FORMULA_VERSION,
  exponentialMovingAverage,
  indicatorPaneScale,
  indicatorOutputs,
  relativeStrengthIndex,
  simpleMovingAverage,
} from './coreIndicators';
import type { MarketBar } from '../tradingTypes';
import { fixture as partial } from '../../../test/fixture';

function close(actual: number[], expected: number[]) {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 10));
}

describe('versioned core indicators', () => {
  it('matches the shared SMA fixture', () => {
    expect(CORE_INDICATOR_FORMULA_VERSION).toBe(fixture.formulaVersion);
    close(simpleMovingAverage(fixture.closes, fixture.sma.period), fixture.sma.values);
  });

  it('matches the shared EMA fixture', () => {
    close(exponentialMovingAverage(fixture.closes, fixture.ema.period), fixture.ema.values);
  });

  it('matches Wilder RSI and documents warmup behavior', () => {
    close(relativeStrengthIndex(fixture.closes, fixture.rsi.period), fixture.rsi.values);
    expect(relativeStrengthIndex([1, 2, 3], 3)).toEqual([]);
  });

  it('fails closed for invalid periods', () => {
    expect(() => simpleMovingAverage([1], 0)).toThrow(/positive integer/);
    expect(() => exponentialMovingAverage([1], 1.5)).toThrow(/positive integer/);
  });

  it('calculates the linked community indicators with finite, aligned points', () => {
    const bars = partial<MarketBar[]>(Array.from({ length: 260 }, (_, index) => {
      const close = 100 + Math.sin(index / 5) * 8 + index * 0.05;
      return {
        instrument_id: 'fixture', interval: '1d',
        start_time: new Date(Date.UTC(2025, 0, index + 1)).toISOString(),
        end_time: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
        open: String(close - 0.5), high: String(close + 2), low: String(close - 2), close: String(close), volume: String(1_000 + index),
        is_final: true, adjustment_mode: 'raw', session: 'regular', provider: 'fixture', ingestion_revision: 1, received_at: new Date().toISOString(),
      };
    }));
    const ids = [
      'bull-market-band', 'death-cross', 'ema-stack', 'fair-value-gap', 'golden-cross', 'ideal-bb',
      'log-macd', 'macd-dema', 'rsi-divergence', 'stochastic-rsi', 'swing-liquidity', 'volume-profile',
    ] as const;
    for (const id of ids) {
      const outputs = indicatorOutputs(bars, { id, period: id === 'volume-profile' ? 100 : 14, enabled: true });
      expect(outputs.every((output) => output.points.every((point) => Number.isFinite(point.value)))).toBe(true);
    }
  });

  it('exposes volume-at-price bins for the Volume Profile renderer', () => {
    const bars = partial<MarketBar[]>(Array.from({ length: 80 }, (_, index) => ({
      instrument_id: 'fixture', interval: '1d',
      start_time: new Date(Date.UTC(2025, 0, index + 1)).toISOString(),
      end_time: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
      open: String(100 + index / 4), high: String(104 + index / 4), low: String(96 + index / 4), close: String(100 + index / 4), volume: String(1_000 + index * 10),
      is_final: true, adjustment_mode: 'raw', session: 'regular', provider: 'fixture', ingestion_revision: 1, received_at: new Date().toISOString(),
    })));
    const outputs = indicatorOutputs(bars, { id: 'volume-profile', period: 80, enabled: true });
    const profile = outputs[0]?.volumeProfile;
    expect(profile).toBeDefined();
    expect(profile?.bins.length).toBeGreaterThan(0);
    expect(profile?.maxVolume).toBeGreaterThan(0);
    expect(profile?.bins.some((bin) => bin.volume > 0)).toBe(true);
  });

  it('hides indicator Y-axis labels by default and allows explicit opt-in', () => {
    const bars = partial<MarketBar[]>(Array.from({ length: 30 }, (_, index) => ({
      instrument_id: 'fixture', interval: '1d',
      start_time: new Date(Date.UTC(2025, 0, index + 1)).toISOString(),
      end_time: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
      open: '100', high: '102', low: '98', close: String(100 + index), volume: '1000',
      is_final: true, adjustment_mode: 'raw', session: 'regular', provider: 'fixture', ingestion_revision: 1, received_at: new Date().toISOString(),
    })));
    expect(indicatorOutputs(bars, { id: 'sma', period: 5, enabled: true })[0].labelsOnPriceScale).toBe(false);
    expect(indicatorOutputs(bars, { id: 'sma', period: 5, enabled: true, style: { labelsOnPriceScale: true } })[0].labelsOnPriceScale).toBe(true);
  });

  it('defines bounded oscillator scales and 0-100 Stoch RSI values', () => {
    expect(indicatorPaneScale('rsi')).toMatchObject({ min: 0, max: 100, band: { from: 30, to: 70 } });
    expect(indicatorPaneScale('stochastic-rsi')).toMatchObject({ min: 0, max: 100, band: { from: 20, to: 80 } });
    expect(indicatorPaneScale('macd')).toBeNull();
    const bars = partial<MarketBar[]>(Array.from({ length: 120 }, (_, index) => ({
      instrument_id: 'fixture', interval: '1d',
      start_time: new Date(Date.UTC(2025, 0, index + 1)).toISOString(),
      end_time: new Date(Date.UTC(2025, 0, index + 2)).toISOString(),
      open: '100', high: String(102 + (index % 3)), low: String(98 - (index % 2)), close: String(100 + Math.sin(index / 4) * 4), volume: '1000',
      is_final: true, adjustment_mode: 'raw', session: 'regular', provider: 'fixture', ingestion_revision: 1, received_at: new Date().toISOString(),
    })));
    const points = indicatorOutputs(bars, { id: 'stochastic-rsi', period: 14, fastPeriod: 3, signalPeriod: 3, enabled: true })
      .flatMap((output) => output.points);
    expect(points.length).toBeGreaterThan(0);
    expect(points.every((point) => point.value >= 0 && point.value <= 100)).toBe(true);
  });
});
