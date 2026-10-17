import { act, renderHook } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { barCloseTime } from './replayClock';
import { useTradingReplayStore } from './tradingReplayStore';
import { useTradingStore } from './tradingStore';
import type { MarketBar } from './tradingTypes';
import { useChartReplayClock, type ChartReplayClockInput } from './useTradingChartPanelReplayClock';
import { fixture } from '../../test/fixture';

const MINUTE = 60_000;
const BASE = Date.parse('2026-08-05T12:00:00Z');
const at = (minute: number) => BASE + minute * MINUTE;

function bars(count: number, minutes: number): MarketBar[] {
  return Array.from({ length: count }, (_, index) => fixture<MarketBar>({
    interval: `${minutes}m`,
    start_time: new Date(at(index * minutes)).toISOString(),
    end_time: new Date(at((index + 1) * minutes)).toISOString(),
    close: String(index),
  }));
}

const fiveMinute = bars(24, 5);
const fifteenMinute = bars(8, 15);
const replay = () => useTradingReplayStore.getState();

function panel(initial: ChartReplayClockInput, strict = false) {
  return renderHook((props: ChartReplayClockInput) => useChartReplayClock(props), {
    initialProps: initial,
    ...(strict ? { wrapper: StrictMode } : {}),
  });
}

describe('charts on one replay clock', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    replay().clear();
    replay().setSpeed(1);
    useTradingStore.setState({ replayMode: true, replaySessionId: 0 });
  });

  afterEach(() => {
    replay().clear();
    useTradingStore.setState({ replayMode: false });
    vi.useRealTimers();
  });

  const fiveMinuteChart = (overrides: Partial<ChartReplayClockInput> = {}): ChartReplayClockInput => ({
    active: true, replayMode: true, bars: fiveMinute, chartKey: 'BTC||5m', reloadBars: vi.fn(), ...overrides,
  });
  const fifteenMinuteChart = (overrides: Partial<ChartReplayClockInput> = {}): ChartReplayClockInput => ({
    active: false, replayMode: true, bars: fifteenMinute, chartKey: 'BTC||15m', reloadBars: vi.fn(), ...overrides,
  });

  it('keeps several charts on one clock, with only the active chart ticking', () => {
    const active = panel(fiveMinuteChart());
    const other = panel(fifteenMinuteChart());
    act(() => {
      replay().chooseStart(barCloseTime(fiveMinute[1]));
      replay().setPlaying(true);
    });
    expect(active.result.current.replayVisibleBarCount).toBe(2);
    expect(other.result.current.replayVisibleBarCount).toBe(0);

    // 1× is one bar a second; a second ticker would double the pace.
    act(() => { vi.advanceTimersByTime(4_000); });

    expect(replay().clock).toBe(at(30));
    expect(active.result.current.replayVisibleBarCount).toBe(6);
    expect(active.result.current.replayCurrentBar).toBe(fiveMinute[5]);
    expect(other.result.current.replayVisibleBarCount).toBe(2);
    expect(other.result.current.replayVisible).toBe(true);
    expect(replay().bar).toBe(fiveMinute[5]);
  });

  it('keeps the clock and replay trading when the active chart switches to a chart of the same series', () => {
    const first = panel(fiveMinuteChart());
    const second = panel(fiveMinuteChart({ active: false }));
    act(() => { replay().chooseStart(at(20)); });
    const session = useTradingStore.getState().replaySessionId;

    first.rerender(fiveMinuteChart({ active: false }));
    second.rerender(fiveMinuteChart({ active: true }));

    expect(replay().clock).toBe(at(20));
    expect(useTradingStore.getState().replaySessionId).toBe(session);
  });

  it('keeps the clock but restarts replay trading when the active chart has another interval', () => {
    const first = panel(fiveMinuteChart());
    const second = panel(fifteenMinuteChart());
    act(() => { replay().chooseStart(at(20)); });
    const session = useTradingStore.getState().replaySessionId;

    first.rerender(fiveMinuteChart({ active: false }));
    second.rerender(fifteenMinuteChart({ active: true }));

    expect(replay().clock).toBe(at(20));
    expect(useTradingStore.getState().replaySessionId).toBe(session + 1);
    expect(replay().activeBars).toBe(fifteenMinute);
    act(() => { replay().stepForward(); });
    expect(replay().clock).toBe(at(30));
  });

  it('returns to real time: the clock is cleared and every chart reloads its bars', () => {
    const activeInput = fiveMinuteChart();
    const otherInput = fifteenMinuteChart();
    const active = panel(activeInput);
    const other = panel(otherInput);
    act(() => { replay().chooseStart(at(20)); });

    active.rerender({ ...activeInput, replayMode: false });
    other.rerender({ ...otherInput, replayMode: false });

    expect(replay().clock).toBeNull();
    expect(active.result.current.replayVisible).toBe(false);
    expect(other.result.current.replayVisible).toBe(false);
    expect(activeInput.reloadBars).toHaveBeenCalledOnce();
    expect(otherInput.reloadBars).toHaveBeenCalledOnce();
  });

  it('behaves the same under StrictMode double effects', () => {
    const activeInput = fiveMinuteChart();
    const otherInput = fifteenMinuteChart();
    const active = panel(activeInput, true);
    const other = panel(otherInput, true);
    act(() => {
      replay().chooseStart(barCloseTime(fiveMinute[1]));
      replay().setPlaying(true);
    });
    const session = useTradingStore.getState().replaySessionId;

    act(() => { vi.advanceTimersByTime(3_000); });
    expect(replay().clock).toBe(at(25));

    active.rerender({ ...activeInput, active: false });
    other.rerender({ ...otherInput, active: true });
    expect(useTradingStore.getState().replaySessionId).toBe(session + 1);
    expect(replay().clock).toBe(at(25));

    active.rerender({ ...activeInput, active: false, replayMode: false });
    other.rerender({ ...otherInput, active: true, replayMode: false });
    expect(replay().clock).toBeNull();
    expect(activeInput.reloadBars).toHaveBeenCalledOnce();
    expect(otherInput.reloadBars).toHaveBeenCalledOnce();
  });
});
