import { describe, expect, it } from 'vitest';
import type { MarketBar } from './tradingTypes';
import { intervalBarStats, percentChangeFromBars, percentChangeFromLookback } from './tradingWatchlistChange';

function bar(
  close: string,
  startTime = '2026-07-01T00:00:00Z',
  open = close,
): MarketBar {
  return {
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
    interval: '1d',
    start_time: startTime,
    end_time: '2026-08-01T00:00:00Z',
    open,
    high: close,
    low: close,
    close,
    volume: '0',
    is_final: true,
    adjustment_mode: 'raw',
    session: '24x7',
    provider: 'binance',
    provider_event_id: null,
    provider_sequence: null,
    ingestion_revision: 1,
    received_at: '2026-08-15T00:00:00Z',
  };
}

describe('percentChangeFromBars', () => {
  it('calculates change from the current interval open to the current price', () => {
    expect(percentChangeFromBars('115', [bar('100'), bar('110', '2026-08-01T00:00:00Z', '100')])).toBeCloseTo(15);
  });

  it('uses the latest candle close when a live quote is unavailable', () => {
    expect(percentChangeFromBars(null, [bar('100'), bar('110', '2026-08-01T00:00:00Z', '100')])).toBeCloseTo(10);
  });

  it('returns null when the current interval has no usable open', () => {
    expect(percentChangeFromBars('115', [bar('110', '2026-08-01T00:00:00Z', '0')])).toBeNull();
  });

  it('derives a missing interval from smaller candles', () => {
    expect(percentChangeFromLookback(
      '115',
      [bar('100', '2026-07-01T00:00:00Z'), bar('110', '2026-08-01T00:00:00Z', '100')],
      '1mo',
    )).toBeCloseTo(15);
  });
});

describe('intervalBarStats', () => {
  const withRange = (start: string, high: string, low: string, volume: string, session = 'regular'): MarketBar => ({
    ...bar('100', start),
    high,
    low,
    volume,
    session,
  });

  it('reads the latest native bar and compares its volume with the bars before it', () => {
    const stats = intervalBarStats([
      withRange('2026-07-01T00:00:00Z', '101', '99', '100'),
      withRange('2026-07-02T00:00:00Z', '102', '98', '300'),
      withRange('2026-07-03T00:00:00Z', '105', '97', '400'),
    ]);
    expect(stats).toEqual({ high: 105, low: 97, volume: 400, relativeVolume: 2, extendedPrice: null });
  });

  it('aggregates a missing interval from smaller bars without a relative volume', () => {
    const stats = intervalBarStats([
      withRange('2026-07-01T00:00:00Z', '120', '80', '999'),
      withRange('2026-07-01T23:00:00Z', '103', '99', '10'),
      withRange('2026-07-02T00:00:00Z', '104', '101', '20'),
    ], '1h');
    expect(stats).toEqual({ high: 104, low: 99, volume: 30, relativeVolume: null, extendedPrice: null });
  });

  it('reports an extended-hours price only for a pre- or post-market bar', () => {
    expect(intervalBarStats([withRange('2026-07-01T00:00:00Z', '1', '1', '1', 'extended_post')]).extendedPrice).toBe('100');
    expect(intervalBarStats([]).high).toBeNull();
  });
});
