import { describe, expect, it } from 'vitest';
import fixture from './fixtures/advancedIndicators.json';
import {
  CORE_INDICATOR_FORMULA_VERSION,
  anchoredVolumeWeightedAveragePrice,
  averageTrueRange,
  bollingerBands,
  movingAverageConvergenceDivergence,
} from './coreIndicators';
import type { MarketBar } from '../tradingTypes';
import { fixture as partial } from '../../../test/fixture';

function close(actual: number[], expected: number[]) {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((value, index) => expect(value).toBeCloseTo(expected[index], 9));
}

function bars(): MarketBar[] {
  return fixture.closes.map((close, index) => (partial({
    instrument_id: 'fixture', interval: '1d',
    start_time: new Date(Date.UTC(2026, 0, index + 1)).toISOString(),
    end_time: new Date(Date.UTC(2026, 0, index + 2)).toISOString(),
    open: String(close), high: String(fixture.highs[index]), low: String(fixture.lows[index]), close: String(close), volume: String(fixture.volumes[index]),
    is_final: true, adjustment_mode: 'raw', session: 'regular', provider: 'fixture', ingestion_revision: 1, received_at: new Date().toISOString(),
  })));
}

describe('advanced indicator parity', () => {
  it('uses the shared formula version', () => expect(CORE_INDICATOR_FORMULA_VERSION).toBe(fixture.formulaVersion));

  it('matches Bollinger population deviation', () => {
    const result = bollingerBands(fixture.closes, fixture.bollinger.period, fixture.bollinger.deviations);
    close(result.map((item) => item.middle), fixture.bollinger.middle);
    close(result.map((item) => item.upper), fixture.bollinger.upper);
    close(result.map((item) => item.lower), fixture.bollinger.lower);
  });

  it('matches Wilder ATR', () => close(
    averageTrueRange(fixture.highs, fixture.lows, fixture.closes, fixture.atr.period),
    fixture.atr.values,
  ));

  it('matches aligned MACD, signal, and histogram values', () => {
    const result = movingAverageConvergenceDivergence(fixture.closes, fixture.macd.fast, fixture.macd.slow, fixture.macd.signalPeriod);
    close(result.map((item) => item.macd), fixture.macd.line);
    close(result.map((item) => item.signal), fixture.macd.signal);
    close(result.map((item) => item.histogram), fixture.macd.histogram);
  });

  it('matches anchored VWAP and supports a later anchor', () => {
    close(anchoredVolumeWeightedAveragePrice(bars()), fixture.vwap);
    expect(anchoredVolumeWeightedAveragePrice(bars(), bars()[5].start_time)).toHaveLength(fixture.closes.length - 5);
  });
});
