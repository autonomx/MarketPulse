import { describe, expect, it } from 'vitest';
import type { MarketBar } from '../tradingTypes';
import type { CoreIndicatorId, CoreIndicatorInstance } from './coreIndicators';
import { TradingIndicatorScheduler } from './indicatorScheduler';
import { fixture } from '../../../test/fixture';

function bars(count: number): MarketBar[] {
  return fixture(Array.from({ length: count }, (_, index) => ({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT',
    interval: '1m',
    start_time: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString(),
    end_time: new Date(Date.UTC(2026, 0, 1, 0, index + 1)).toISOString(),
    open: String(100 + index),
    high: String(101 + index),
    low: String(99 + index),
    close: String(100 + index),
    volume: '10',
    is_final: true,
    adjustment_mode: 'raw',
    session: '24x7',
    provider: 'binance',
    ingestion_revision: 1,
    received_at: '2026-01-01T00:00:00Z',
  })));
}

describe('TradingIndicatorScheduler', () => {
  it('uses the deterministic fallback when Worker is unavailable', async () => {
    const scheduler = new TradingIndicatorScheduler(null);
    const outputs = await scheduler.calculate(bars(30), [{ id: 'sma', period: 20, enabled: true }]);
    expect(outputs?.[0].key).toBe('sma:20');
    expect(outputs?.[0].points).toHaveLength(11);
    scheduler.destroy();
  });

  it('suppresses stale calculations before they reach a chart', async () => {
    const scheduler = new TradingIndicatorScheduler(null);
    const first = scheduler.calculate(bars(30), [{ id: 'sma', period: 20, enabled: true }]);
    const second = scheduler.calculate(bars(30), [{ id: 'ema', period: 10, enabled: true }]);
    expect(await first).toBeNull();
    expect((await second)?.[0].key).toBe('ema:10');
    scheduler.destroy();
  });

  it('loads the compare symbol of a Correlation Coefficient over the chart time range', async () => {
    const calls: Array<[string, string, { from: number; to: number }]> = [];
    const scheduler = new TradingIndicatorScheduler(null, async (instrumentId, interval, range) => {
      calls.push([instrumentId, interval, range]);
      return bars(30);
    });
    const correlation: CoreIndicatorInstance = { id: 'tv-correlation-coefficient-cc' as CoreIndicatorId, period: 20, enabled: true, compareSymbol: 'equity:NASDAQ:QQQ' };
    const outputs = await scheduler.calculate(bars(30), [correlation, { id: 'sma', period: 20, enabled: true }]);
    expect(calls).toEqual([['equity:NASDAQ:QQQ', '1m', { from: Date.UTC(2026, 0, 1, 0, 0), to: Date.UTC(2026, 0, 1, 0, 29) }]]);
    expect(outputs?.map((output) => [output.key, output.points.length])).toEqual([['tv-correlation-coefficient-cc:cc', 11], ['sma:20', 11]]);
    scheduler.destroy();
  });

  it('passes the session calendar to session-aware built-ins', async () => {
    const scheduler = new TradingIndicatorScheduler(null);
    const twap: CoreIndicatorInstance = { id: 'tv-time-weighted-average-price' as CoreIndicatorId, period: 1, enabled: true };
    // 23:59 UTC on 2025-12-31 and later bars: one New York session, but the 1 January UTC session starts at 00:00.
    const series = bars(3).map((bar, index) => ({ ...bar, start_time: new Date(Date.UTC(2025, 11, 31, 23, 59 + index)).toISOString() }));
    const utc = await scheduler.calculate(series, [twap]);
    const newYork = await scheduler.calculate(series, [twap], { session: { timezone: 'America/New_York', startMinute: 0 } });
    expect(utc?.[0].points[1].value).toBe(101);
    expect(newYork?.[0].points[1].value).toBe(100.5);
    scheduler.destroy();
  });

  it('plots nothing for a compare symbol whose bars fail to load', async () => {
    const scheduler = new TradingIndicatorScheduler(null, () => Promise.reject(new Error('offline')));
    const correlation: CoreIndicatorInstance = { id: 'tv-correlation-coefficient-cc' as CoreIndicatorId, period: 20, enabled: true, compareSymbol: 'equity:NASDAQ:QQQ' };
    const outputs = await scheduler.calculate(bars(30), [correlation]);
    expect(outputs?.[0].points).toEqual([]);
    scheduler.destroy();
  });

  it('returns no result after disposal', async () => {
    const scheduler = new TradingIndicatorScheduler(null);
    scheduler.destroy();
    expect(await scheduler.calculate(bars(30), [{ id: 'rsi', period: 14, enabled: true }])).toBeNull();
  });
});
