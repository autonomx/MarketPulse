import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const replayApi = vi.hoisted(() => ({
  advanceExecution: vi.fn(),
  placeExecutionOrder: vi.fn(),
}));

vi.mock('./tradingReplayApi', () => ({ tradingReplayApi: replayApi }));

import type { PaperAccountSnapshot, PaperOrderInput } from './paperTypes';
import { barCloseTime, replayVisibleCount } from './replayClock';
import { replayAccountHasExposure, useTradingReplayStore } from './tradingReplayStore';
import type { MarketBar } from './tradingTypes';
import { useChartReplayClock } from './useTradingChartPanelReplayClock';
import { fixture } from '../../test/fixture';

const MINUTE = 60_000;
const BASE = Date.parse('2026-08-05T12:00:00Z');
const at = (minute: number) => BASE + minute * MINUTE;
const minuteOf = (time: number) => (time - BASE) / MINUTE;
const ROUND_TRIP_MS = 250;
const INSTRUMENT = 'crypto:BINANCE:spot:BTC-USDT';

function bar(index: number, low = 100): MarketBar {
  return fixture({
    instrument_id: INSTRUMENT,
    interval: '1m',
    start_time: new Date(at(index)).toISOString(),
    end_time: new Date(at(index + 1)).toISOString(),
    open: '101', high: '102', low: String(low), close: '101', volume: '1',
    is_final: true, adjustment_mode: 'raw', session: '24x7', provider: 'binance',
    ingestion_revision: 1, received_at: new Date(at(index + 1)).toISOString(),
  });
}

const series = (count: number) => Array.from({ length: count }, (_, index) => bar(index));

type TestSnapshot = PaperAccountSnapshot & { advanced: number[] };

function workingLimit(price: string, instrumentId = INSTRUMENT) {
  return { order_id: `limit-${price}`, instrument_id: instrumentId, side: 'buy', order_type: 'limit', quantity: '1', limit_price: price };
}

function account(openOrders: unknown[] = [], positions: unknown[] = []): TestSnapshot {
  return fixture({
    account: { account_id: 'paper-1' },
    balances: [],
    positions,
    open_orders: openOrders,
    order_history: [],
    recent_fills: [],
    recent_ledger: [],
    advanced: [],
  });
}

const log: string[] = [];

/** A stand-in for the server kernel: a buy limit fills when a bar trades at or below it. */
async function slowKernel(snapshot: TestSnapshot, advanced: MarketBar): Promise<TestSnapshot> {
  await new Promise((resolve) => setTimeout(resolve, ROUND_TRIP_MS));
  log.push(`bar ${minuteOf(barCloseTime(advanced))}`);
  const fills = snapshot.open_orders.filter((order) => Number(advanced.low) <= Number(order.limit_price));
  return {
    ...snapshot,
    advanced: [...snapshot.advanced, barCloseTime(advanced)],
    open_orders: snapshot.open_orders.filter((order) => !fills.includes(order)),
    positions: [...snapshot.positions, ...fills.map((order) => fixture<PaperAccountSnapshot['positions'][number]>({
      instrument_id: order.instrument_id, quantity: order.quantity, average_cost: order.limit_price,
    }))],
  };
}

async function slowOrder(snapshot: TestSnapshot, input: PaperOrderInput, placed: MarketBar, advanceBar: boolean) {
  await new Promise((resolve) => setTimeout(resolve, ROUND_TRIP_MS));
  log.push(`order ${input.order_id} at ${minuteOf(barCloseTime(placed))}${advanceBar ? ' advancing' : ''}`);
  const order = { ...workingLimit('1'), order_id: input.order_id, status: 'open' };
  return { snapshot: { ...snapshot, open_orders: [...snapshot.open_orders, order] }, order };
}

const replay = () => useTradingReplayStore.getState();
const wait = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const order = (orderId: string) => fixture<PaperOrderInput>({ order_id: orderId, instrument_id: INSTRUMENT });

/** Mount the active chart's replay hook (it runs the playback ticker) and start at `startIndex`. */
function startReplay(bars: MarketBar[], startIndex: number, seed: TestSnapshot, speed: 1 | 30 | 100 = 30) {
  const hook = renderHook(() => useChartReplayClock({
    active: true, replayMode: true, bars, chartKey: 'BTC||1m', reloadBars: () => undefined,
  }));
  act(() => {
    replay().chooseStart(barCloseTime(bars[startIndex]));
    replay().seedSnapshot(seed, 'test-session');
    replay().setSpeed(speed);
  });
  return hook;
}

/** A slow replay kernel on fake timers, cleared around every test. */
function mockReplayQueue(): void {
  beforeEach(() => {
    vi.useFakeTimers();
    replay().clear();
    log.length = 0;
    replayApi.advanceExecution.mockImplementation(slowKernel);
    replayApi.placeExecutionOrder.mockImplementation(slowOrder);
  });

  afterEach(() => {
    replay().clear();
    vi.useRealTimers();
    vi.clearAllMocks();
  });
}

describe('replay account execution queue', () => {
  mockReplayQueue();

  it('advances a working account through every bar, in order, each on the previous result', async () => {
    const bars = series(12);
    startReplay(bars, 0, account([workingLimit('50')]));
    act(() => { replay().setPlaying(true); });

    await wait(10_000);

    const calls = replayApi.advanceExecution.mock.calls as [TestSnapshot, MarketBar][];
    expect(calls.map(([, advanced]) => barCloseTime(advanced))).toEqual(bars.slice(1).map(barCloseTime));
    calls.forEach(([input], index) => {
      expect(input.advanced).toEqual(bars.slice(1, index + 1).map(barCloseTime));
    });
    expect(replay().advancedThrough).toBe(replay().clock);
  });

  it('fills a limit touched only by the middle bar of a three-bar tick', async () => {
    const bars = [bar(0), bar(1), bar(2), bar(3, 90), bar(4), bar(5)];
    startReplay(bars, 1, account([workingLimit('95')]));
    act(() => { replay().setPlaying(true); });

    await wait(5_000);

    const snapshot = replay().snapshot!;
    expect(snapshot.open_orders).toEqual([]);
    expect(snapshot.positions).toEqual([expect.objectContaining({ quantity: '1', average_cost: '95' })]);
  });

  it('never lets the clock run more than one tick ahead of a working account', async () => {
    const bars = series(40);
    let maxLag = 0;
    const unsubscribe = useTradingReplayStore.subscribe((state) => {
      if (state.clock === null || state.advancedThrough === null || !replayAccountHasExposure(state.snapshot, INSTRUMENT)) return;
      maxLag = Math.max(maxLag, replayVisibleCount(bars, state.clock) - replayVisibleCount(bars, state.advancedThrough));
    });
    startReplay(bars, 0, account([workingLimit('1')]));
    act(() => { replay().setPlaying(true); });

    await wait(1_000);
    // 30× wants 30 bars a second, but the account takes 250 ms a bar.
    expect(replayVisibleCount(bars, replay().clock!)).toBeLessThan(10);
    await wait(20_000);
    unsubscribe();

    expect(maxLag).toBeLessThanOrEqual(3);
    expect(replay().clock).toBe(barCloseTime(bars[39]));
    expect(replayApi.advanceExecution).toHaveBeenCalledTimes(39);
  });

  it('plays a flat account at full speed without a request per bar, and places an order at the current bar promptly', async () => {
    const bars = series(2_000);
    startReplay(bars, 0, account([], [{ instrument_id: 'equity:NYSE:OTHER', quantity: '5' }]), 100);
    act(() => { replay().setPlaying(true); });

    await wait(10_000);

    // 100× is 1,000 bars in ten seconds; a position in another instrument does not throttle it.
    expect(replayVisibleCount(bars, replay().clock!)).toBe(1_001);
    expect(replayApi.advanceExecution).not.toHaveBeenCalled();
    expect(replay().pendingExecutions).toBe(0);
    expect(replay().advancedThrough).toBe(replay().clock);

    const current = replay().bar!;
    let placedAfter: number | null = null;
    const started = Date.now();
    void replay().placeOrder(order('market-1')).then(() => { placedAfter = Date.now() - started; });
    await wait(ROUND_TRIP_MS);

    expect(placedAfter).toBe(ROUND_TRIP_MS);
    expect(replayApi.placeExecutionOrder).toHaveBeenCalledWith(
      expect.anything(), expect.anything(), expect.objectContaining({ start_time: current.start_time }), false,
    );
    // From now on the working order sees every bar.
    await wait(2_000);
    expect(replayApi.advanceExecution.mock.calls.length).toBeGreaterThan(0);
    expect(replay().advancedThrough).toBeLessThanOrEqual(replay().clock!);
  });

  it('places an order queued during playback after the bars already queued, without applying its bar twice', async () => {
    const bars = series(20);
    startReplay(bars, 0, account([workingLimit('1')]));
    act(() => { replay().setPlaying(true); });
    await wait(150);
    // One tick queued bars 2-4; the order waits for them.
    act(() => { replay().setBar(bars[3]); });
    const placed = replay().placeOrder(order('market-1'));
    act(() => { replay().setPlaying(false); });

    await wait(2_000);
    await placed;

    expect(log).toEqual(['bar 2', 'bar 3', 'bar 4', 'order market-1 at 4']);
  });

  it('resumes after a failed advance: a later order follows every missing bar', async () => {
    const bars = series(40);
    startReplay(bars, 0, account([workingLimit('1')]), 100);
    replayApi.advanceExecution.mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, ROUND_TRIP_MS));
      throw new Error('kernel unavailable');
    });
    act(() => { replay().setPlaying(true); });
    await wait(3_000);

    expect(replay().playing).toBe(false);
    expect(replay().executionError).toBe('kernel unavailable');
    expect(replay().advancedThrough).toBe(at(1));
    expect(minuteOf(replay().clock!)).toBe(11);

    act(() => { replay().setBar(bars[10]); });
    const placed = replay().placeOrder(order('market-1'));
    await wait(5_000);
    await placed;
    act(() => { replay().stepForward(); });
    await wait(1_000);

    expect(log).toEqual([
      ...Array.from({ length: 10 }, (_, index) => `bar ${index + 2}`),
      'order market-1 at 11',
      'bar 12',
    ]);
    expect(replay().executionError).toBeNull();
  });

  it('rejects an order queued behind a failing bar instead of applying it early', async () => {
    const bars = series(10);
    startReplay(bars, 0, account([workingLimit('1')]));
    replayApi.advanceExecution.mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, ROUND_TRIP_MS));
      throw new Error('kernel unavailable');
    });
    act(() => { replay().stepForward(); });
    act(() => { replay().setBar(bars[1]); });
    const outcome = replay().placeOrder(order('market-1')).then(() => 'placed', (error: Error) => error.message);

    await wait(1_000);

    expect(await outcome).toMatch(/not placed/);
    expect(replayApi.placeExecutionOrder).not.toHaveBeenCalled();
  });
});

describe('replay account execution queue', () => {
  mockReplayQueue();

  it('ignores a failure from a request of an earlier session', async () => {
    const bars = series(10);
    startReplay(bars, 0, account([workingLimit('1')]));
    replayApi.advanceExecution.mockImplementationOnce(async () => {
      await new Promise((resolve) => setTimeout(resolve, ROUND_TRIP_MS));
      throw new Error('stale failure');
    });
    act(() => { replay().stepForward(); });
    await wait(10);
    expect(replayApi.advanceExecution).toHaveBeenCalledOnce();
    act(() => {
      replay().resetToStart();
      replay().seedSnapshot(account([workingLimit('1')]), 'second-session');
      replay().setPlaying(true);
    });

    await wait(ROUND_TRIP_MS * 2);

    expect(replay().executionError).toBeNull();
    expect(replay().playing).toBe(true);
    expect(replayApi.advanceExecution.mock.calls.length).toBeGreaterThan(1);
  });

  it('drops in-flight results when stepping back or resetting', async () => {
    const bars = series(10);
    startReplay(bars, 0, account([workingLimit('1')]));
    act(() => { replay().stepForward(3); });
    await wait(100);
    act(() => { replay().stepBack(); });

    expect(replay().snapshot).toBeNull();
    expect(replay().pendingExecutions).toBe(0);
    act(() => { replay().seedSnapshot(account([workingLimit('1')]), 'after-step-back'); });
    const seeded = replay().snapshot;
    await wait(2_000);
    expect(replay().snapshot).toBe(seeded);

    act(() => { replay().stepForward(); });
    await wait(50);
    act(() => { replay().resetToStart(); });
    await wait(1_000);

    expect(replay().snapshot).toBeNull();
    expect(replay().pendingExecutions).toBe(0);
    expect(replay().clock).toBe(at(1));
  });

  it('places an order at the bar the clock just reached, even in the same handler as the step', async () => {
    const bars = series(10);
    startReplay(bars, 0, account([workingLimit('1')]));
    let placed: Promise<unknown> = Promise.resolve();
    act(() => {
      replay().stepForward();
      placed = replay().placeOrder(order('market-1'));
    });

    await wait(1_000);
    await placed;

    // The published bar still pointed at bar 1 when the order was placed.
    expect(log).toEqual(['bar 2', 'order market-1 at 2']);
  });

  it('sends a bar with an unreadable end at its derived close, and skips one whose close cannot be known', async () => {
    const bars = series(6);
    bars[2] = { ...bars[2], end_time: '' };
    bars[4] = { ...bars[4], end_time: '', interval: 'tick' };
    startReplay(bars, 0, account([workingLimit('1')]));

    act(() => { replay().stepForward(5); });
    await wait(2_000);

    expect(log).toEqual(['bar 2', 'bar 3', 'bar 4', 'bar 6']);
    expect(replay().advancedThrough).toBe(at(6));
    expect(replay().executionError).toBeNull();
  });

  it('trades on the replay session feed: bars and orders carry its binding', async () => {
    const bars = series(5);
    startReplay(bars, 0, account([workingLimit('1')]));
    act(() => { replay().setActiveBars(bars, 'bind-1'); });

    act(() => { replay().stepForward(); });
    const placed = replay().placeOrder(fixture<PaperOrderInput>({ order_id: 'market-1', instrument_id: INSTRUMENT, binding_id: null }));
    await wait(1_000);
    await placed;

    expect(replayApi.advanceExecution).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ binding_id: 'bind-1' }));
    expect(replayApi.placeExecutionOrder).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ binding_id: 'bind-1' }),
      expect.objectContaining({ binding_id: 'bind-1' }),
      false,
    );
  });
});
