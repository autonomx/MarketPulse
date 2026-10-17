import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fixture } from '../../test/fixture';
import { tradingCommandDefinition } from './commands/tradingCommands';
import { clearIntrabarCache } from './intrabarData';
import { barCloseTime, nextSubBarClock, replaySubBarStepMs, replayUpdateIntervals } from './replayClock';
import { tradingApi } from './tradingApi';
import { useTradingReplayStore } from './tradingReplayStore';
import type { MarketBar } from './tradingTypes';
import { useChartReplayClock } from './useTradingChartPanelReplayClock';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const T0 = Date.UTC(2026, 7, 13, 10);

function bar(start: number, minutes: number, close: number, interval: string): MarketBar {
  return fixture({
    instrument_id: 'crypto:BINANCE:spot:BTC-USDT', interval, start_time: new Date(start).toISOString(), end_time: new Date(start + minutes * MINUTE).toISOString(),
    open: String(close - 1), high: String(close + 1), low: String(close - 2), close: String(close), volume: '10', is_final: true,
    adjustment_mode: 'raw', session: '24x7', provider: 'binance', ingestion_revision: 1, received_at: new Date(start + minutes * MINUTE).toISOString(),
  });
}

const hours = Array.from({ length: 4 }, (_, i) => bar(T0 + i * HOUR, 60, 100 + i, '1h'));
const quarters = Array.from({ length: 16 }, (_, i) => bar(T0 + i * 15 * MINUTE, 15, 200 + i, '15m'));
const replay = () => useTradingReplayStore.getState();

beforeEach(() => {
  act(() => {
    replay().clear();
    replay().setUpdateInterval(null);
  });
});

afterEach(() => {
  clearIntrabarCache();
  vi.restoreAllMocks();
});

describe('sub-bar replay (TVP-8.1)', () => {
  it('offers update intervals that fit the chart interval and one intrabar request a bar', () => {
    expect(replayUpdateIntervals('1h')).toEqual(['1m', '5m', '15m']);
    expect(replayUpdateIntervals('1d')).toEqual(['1m', '5m', '15m', '1h', '4h']);
    expect(replayUpdateIntervals('1m')).toEqual([]);
    expect(replaySubBarStepMs('1h', '15m')).toBe(15 * MINUTE);
    expect(replaySubBarStepMs('1h', '4h')).toBeNull();
    expect(replaySubBarStepMs('1h', null)).toBeNull();
  });

  it('steps the clock by the update interval, stopping at the last close', () => {
    const last = barCloseTime(hours[3]);
    expect(nextSubBarClock(hours, T0 + HOUR, 15 * MINUTE)).toBe(T0 + HOUR + 15 * MINUTE);
    expect(nextSubBarClock(hours, T0 + HOUR, 15 * MINUTE, 3)).toBe(T0 + HOUR + 45 * MINUTE);
    expect(nextSubBarClock(hours, last - MINUTE, 15 * MINUTE)).toBe(last);
    expect(nextSubBarClock(hours, last, 15 * MINUTE)).toBeNull();
    // A gap between bars (overnight) is skipped: steps start from the next bar's open.
    const gapped = [hours[0], bar(T0 + 10 * HOUR, 60, 110, '1h')];
    expect(nextSubBarClock(gapped, T0 + HOUR, 15 * MINUTE)).toBe(T0 + 10 * HOUR + 15 * MINUTE);
  });

  it('refuses replay orders placed inside a bar in sub-bar playback', async () => {
    act(() => {
      replay().setActiveBars(hours);
      replay().chooseStart(barCloseTime(hours[0]));
      replay().setUpdateInterval('15m');
      replay().stepForward();
    });
    await expect(replay().placeOrder(fixture({ order_id: 'o', instrument_id: 'crypto:BINANCE:spot:BTC-USDT' }))).rejects.toThrow(/step to the bar close/);
  });

  it('moves the shared clock by update intervals, and bar by bar when it does not fit the chart', () => {
    act(() => {
      replay().setActiveBars(hours);
      replay().chooseStart(barCloseTime(hours[0]));
      replay().setUpdateInterval('15m');
    });
    act(() => { replay().stepForward(); });
    expect(replay().clock).toBe(T0 + HOUR + 15 * MINUTE);
    act(() => { replay().setUpdateInterval('4h'); });
    act(() => { replay().stepForward(); });
    expect(replay().clock).toBe(T0 + 2 * HOUR);
    // Back steps go to the previous closed bar.
    act(() => { replay().setUpdateInterval('15m'); replay().stepForward(); replay().stepBack(); });
    expect(replay().clock).toBe(T0 + 2 * HOUR);
  });

  it('shows the forming bar as it stood at the clock, from the intrabar data', async () => {
    const spy = vi.spyOn(tradingApi, 'intrabars').mockResolvedValue({ bars: quarters, complete: true, available_from: quarters[0].start_time } as never);
    const hook = renderHook(() => useChartReplayClock({
      active: true, replayMode: true, bars: hours, chartKey: 'BTC||1h', reloadBars: () => undefined, instrumentId: 'crypto:BINANCE:spot:BTC-USDT', interval: '1h',
    }));
    act(() => {
      replay().chooseStart(barCloseTime(hours[0]));
      replay().setUpdateInterval('15m');
    });
    act(() => { replay().stepForward(2); });
    await waitFor(() => expect(hook.result.current.replayFormingBar).not.toBeNull());
    // The second hour after two of its quarters: open of the first, close of the second, volume of both.
    expect(hook.result.current.replayFormingBar).toMatchObject({ start_time: hours[1].start_time, open: '203', close: '205', high: '206', low: '202', volume: '20', is_final: false });
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ interval: '1h', lowerInterval: '15m' }));
    act(() => { replay().stepForward(2); });
    // The hour closed: no forming bar until the next hour's first quarter closes.
    expect(hook.result.current.replayFormingBar).toBeNull();
    expect(hook.result.current.replayVisibleBarCount).toBe(2);
    act(() => { replay().setUpdateInterval(null); });
    expect(hook.result.current.replayFormingBar).toBeNull();
    hook.unmount();
  });

  it('builds the forming bar from the sessions the chart shows, and retries a failed load', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const extended = quarters.map((item, i) => (i === 4 ? { ...item, session: 'extended_pre', high: '999' } : item));
    const spy = vi.spyOn(tradingApi, 'intrabars').mockRejectedValueOnce(new Error('down')).mockResolvedValue({ bars: extended, complete: true } as never);
    const hook = renderHook(() => useChartReplayClock({
      active: true, replayMode: true, bars: hours, chartKey: 'BTC||1h', reloadBars: () => undefined, instrumentId: 'x', interval: '1h', showExtendedHours: false,
    }));
    act(() => {
      replay().chooseStart(barCloseTime(hours[0]));
      replay().setUpdateInterval('15m');
      replay().stepForward(2);
    });
    await waitFor(() => expect(replay().intrabarNote).toMatch(/Couldn't load 15m data/));
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    await waitFor(() => expect(hook.result.current.replayFormingBar).not.toBeNull());
    // The pre-market quarter is left out: the bar forms from the 10:15 quarter alone.
    expect(hook.result.current.replayFormingBar).toMatchObject({ open: '204', high: '206', volume: '10' });
    // The range is the chart bars one request covers, from the forming bar's own start.
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ start: T0 + HOUR, end: T0 + 4 * HOUR }));
    hook.unmount();
    vi.useRealTimers();
  });

  it('notes where the intrabar data stops', async () => {
    vi.spyOn(tradingApi, 'intrabars').mockResolvedValue({ bars: [], complete: false, available_from: '2026-08-14T00:00:00Z' } as never);
    const hook = renderHook(() => useChartReplayClock({
      active: true, replayMode: true, bars: hours, chartKey: 'BTC||1h', reloadBars: () => undefined, instrumentId: 'x', interval: '1h',
    }));
    act(() => {
      replay().chooseStart(barCloseTime(hours[0]));
      replay().setUpdateInterval('15m');
      replay().stepForward();
    });
    await waitFor(() => expect(replay().intrabarNote).toMatch(/^15m data starts/));
    hook.unmount();
  });

  it('has the TradingView replay keys: Shift+Down plays or pauses, Shift+Right and Shift+Left step', () => {
    expect(['replay.playPause', 'replay.stepForward', 'replay.stepBack'].map((id) => tradingCommandDefinition(id)?.defaultKeys))
      .toEqual([['shift+arrowdown'], ['shift+arrowright'], ['shift+arrowleft']]);
    expect(tradingCommandDefinition('replay.stepForward')).toMatchObject({ scope: 'chart', keyContext: 'chart', repeatable: true });
  });
});
