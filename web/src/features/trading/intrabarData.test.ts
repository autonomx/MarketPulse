import { afterEach, describe, expect, it, vi } from 'vitest';
import { fixture } from '../../test/fixture';
import {
  autoIntrabarInterval, clearIntrabarCache, tradingViewIntrabarInterval, formingBar, groupIntrabars, intrabarRange, isIntrabarInterval, loadIntrabars, MAX_INTRABAR_BARS,
} from './intrabarData';
import { tradingApi } from './tradingApi';
import type { MarketBar } from './tradingTypes';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const T0 = Date.UTC(2026, 7, 13, 10);

function bar(start: number, minutes: number, [open, high, low, close, volume]: number[], interval = `${minutes}m`): MarketBar {
  return fixture({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT', interval, start_time: new Date(start).toISOString(), end_time: new Date(start + minutes * MINUTE).toISOString(),
    open: String(open), high: String(high), low: String(low), close: String(close), volume: String(volume), is_final: true,
    adjustment_mode: 'raw', session: '24x7', provider: 'binance', ingestion_revision: 1, received_at: new Date(start + minutes * MINUTE).toISOString(),
  });
}

afterEach(() => {
  clearIntrabarCache();
  vi.restoreAllMocks();
});

describe('intrabar data (TVP-0.6)', () => {
  it('picks a lower interval that fits inside the chart interval', () => {
    expect(autoIntrabarInterval('30s')).toBeNull();
    // TradingView's own choice, read for the latest bars (TVP-6.4): 1m intraday, 5m daily, 1h above.
    expect(['1m', '4h', '1d', '1w'].map(tradingViewIntrabarInterval)).toEqual([null, '1m', '5m', '1h']);
    expect(autoIntrabarInterval('1m')).toBeNull();
    expect(autoIntrabarInterval('5m')).toBe('1m');
    expect(autoIntrabarInterval('1h')).toBe('1m');
    expect(autoIntrabarInterval('4h')).toBe('5m');
    expect(autoIntrabarInterval('1d')).toBe('1h');
    expect(autoIntrabarInterval('1s')).toBeNull();
    expect(isIntrabarInterval('1h', '1m')).toBe(true);
    expect(isIntrabarInterval('1h', '7m')).toBe(false);
    expect(isIntrabarInterval('1m', '1h')).toBe(false);
  });

  it('keeps the latest chart bars whose lower bars fit one request', () => {
    const hours = Array.from({ length: 200 }, (_, i) => bar(T0 + i * HOUR, 60, [1, 1, 1, 1, 1], '1h'));
    const range = intrabarRange(hours, '1h', '1m')!;
    expect(range.end).toBe(T0 + 200 * HOUR);
    expect((range.end - range.start) / MINUTE).toBeLessThanOrEqual(MAX_INTRABAR_BARS);
    expect(range.start).toBe(T0 + 117 * HOUR);
    expect(intrabarRange([], '1h', '1m')).toBeNull();
  });

  it('groups lower bars under their chart bar and builds the bar as it stood at a time', () => {
    const hours = [bar(T0, 60, [10, 12, 9, 11, 0], '1h'), bar(T0 + HOUR, 60, [11, 13, 10, 12, 0], '1h')];
    const minutes = [
      bar(T0 + HOUR + 2 * MINUTE, 1, [11.5, 12, 11, 11.8, 4]),
      bar(T0 - MINUTE, 1, [9, 9, 9, 9, 1]),
      bar(T0, 1, [10, 10.5, 9.5, 10.2, 2]),
      bar(T0 + HOUR, 1, [11, 11.4, 10.8, 11.2, 3]),
      bar(T0 + MINUTE, 1, [10.2, 11, 10, 10.8, 5]),
    ];
    const groups = groupIntrabars(hours, minutes, '1h');
    expect(groups.get(T0)!.map((item) => item.start_time)).toEqual([new Date(T0).toISOString(), new Date(T0 + MINUTE).toISOString()]);
    expect(groups.get(T0 + HOUR)).toHaveLength(2);
    const forming = formingBar(hours[1], groups.get(T0 + HOUR)!, T0 + HOUR + 3 * MINUTE)!;
    expect(forming).toMatchObject({ open: '11', high: '12', low: '10.8', close: '11.8', volume: '7', is_final: false, start_time: hours[1].start_time });
    expect(formingBar(hours[1], groups.get(T0 + HOUR)!, T0 + HOUR + MINUTE)).toMatchObject({ close: '11.2', volume: '3' });
    expect(formingBar(hours[1], groups.get(T0 + HOUR)!, T0 + HOUR)).toBeNull();
  });

  it('loads a range once, and retries a failed load', async () => {
    const response = { bars: [], complete: true } as never;
    const spy = vi.spyOn(tradingApi, 'intrabars').mockRejectedValueOnce(new Error('down')).mockResolvedValue(response);
    const request = { instrumentId: 'x', interval: '1h', lowerInterval: '1m', start: T0, end: T0 + HOUR };
    vi.useFakeTimers({ now: T0 });
    await expect(loadIntrabars(request)).rejects.toThrow('down');
    // A failed load is held a few seconds, then asked for again.
    await expect(loadIntrabars(request)).rejects.toThrow('down');
    vi.setSystemTime(T0 + 4_001);
    await expect(loadIntrabars(request)).resolves.toBe(response);
    vi.useRealTimers();
    await loadIntrabars(request);
    expect(spy).toHaveBeenCalledTimes(2);
    await loadIntrabars({ ...request, lowerInterval: '5m' });
    expect(spy).toHaveBeenCalledTimes(3);
  });
});
