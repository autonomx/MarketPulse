import { create } from 'zustand';
import type { PaperAccountSnapshot, PaperOrderInput } from './paperTypes';
import {
  barCloseTime,
  DEFAULT_REPLAY_SPEED,
  nextReplayClock,
  nextSubBarClock,
  previousReplayClock,
  replaySubBarStepMs,
  replayTradableBarAtClock,
  replayVisibleCount,
  type ReplaySpeed,
} from './replayClock';
import { advanceReplaySnapshot, placeReplayOrder, type ReplayResult } from './replayTrading';
import type { ReplayExecutionMarketBar } from './tradingReplayApi';
import { useTradingStore } from './tradingStore';
import type { MarketBar } from './tradingTypes';

/**
 * Bar replay state shared by every chart in the layout (TVP-8.1).
 *
 * Replay mode and the trading session id live in the workspace store; the
 * clock lives here because it changes on every playback tick and the
 * workspace store is saved on every change.
 *
 * The replay account is advanced here too, not in the order ticket (which
 * may be hidden): every active-chart bar the clock passes is sent to the
 * server execution kernel once, in order, one request at a time, each built
 * on the previous result. Orders join the same queue.
 *
 * The step, play and select actions work without a chart panel, so a command
 * or hotkey layer (TVP-0.3) can call them through `getState()`.
 */
type TradingReplayState = {
  /** The active chart's bar at the clock; the price replay orders use. */
  bar: MarketBar | null;
  /** The simulated replay account. */
  snapshot: PaperAccountSnapshot | null;
  /** The replay clock in epoch milliseconds; null until a start bar is chosen. */
  clock: number | null;
  /** The clock at the chosen start bar; reset and step back stop here. */
  startTime: number | null;
  /** The active chart is waiting for a click that chooses the start bar. */
  selecting: boolean;
  playing: boolean;
  speed: ReplaySpeed;
  /** Sub-bar playback: the clock steps by this interval (TVP-8.1); null steps bar by bar. Ignored where it doesn't fit the active chart. */
  updateInterval: string | null;
  /** Where the active chart's intrabar data stops, when the replay is older than it reaches; null when it covers the replay. */
  intrabarNote: string | null;
  /** The active chart's bars, which step, play and the replay account advance through. */
  activeBars: readonly MarketBar[];
  /** The feed binding the active chart's bars come from; replay orders and bars use it. */
  activeBindingId: string | null;
  /** The active chart's instrument, binding and interval; a change restarts replay trading. */
  activeChartKey: string | null;
  /** The close of the last bar the replay account has been advanced through. */
  advancedThrough: number | null;
  /** Replay account requests (bar advances and orders) not yet finished. */
  pendingExecutions: number;
  executionError: string | null;
  setBar: (bar: MarketBar | null) => void;
  /** Replace the replay account outright (tests and recovery); prefer seedSnapshot and placeOrder. */
  setSnapshot: (snapshot: PaperAccountSnapshot | null) => void;
  /**
   * Start the replay account at the current clock. A repeated call with the
   * same key keeps the existing account, so remounting the order ticket does
   * not discard simulated orders.
   */
  seedSnapshot: (snapshot: PaperAccountSnapshot, key: string) => void;
  /** Place a replay order after every queued bar advance, at the current bar. */
  placeOrder: (input: PaperOrderInput) => Promise<ReplayResult>;
  setSpeed: (speed: ReplaySpeed) => void;
  setUpdateInterval: (updateInterval: string | null) => void;
  setIntrabarNote: (note: string | null) => void;
  setActiveBars: (bars: readonly MarketBar[], bindingId?: string | null) => void;
  /** Record the active chart's identity; a different instrument, binding or interval restarts replay trading. */
  setActiveChartKey: (key: string) => void;
  setPlaying: (playing: boolean) => void;
  togglePlaying: () => void;
  /** Start replay at `time` (a bar's close) and restart the replay trading session. */
  chooseStart: (time: number) => void;
  /** Pause and let the active chart choose a new start bar (jump to bar). */
  beginSelecting: () => void;
  /** Move the clock forward by `steps` bars of the active chart (update intervals in sub-bar playback); false at the end. */
  stepForward: (steps?: number) => boolean;
  /**
   * One playback tick. It waits (returns false) while the replay account has
   * working orders or positions and earlier bars are still being advanced, so
   * the clock never runs ahead of the account.
   */
  playbackTick: (steps: number) => boolean;
  /** Move the clock back one bar of the active chart, not before the start. */
  stepBack: () => boolean;
  resetToStart: () => void;
  /** Drop the replay account and its queue (a new replay trading session). */
  resetExecution: () => void;
  /** Leave replay: forget the clock, bar and simulated account; the speed is kept. */
  clear: () => void;
};

const NO_BARS: readonly MarketBar[] = [];

/** The clock `steps` forward on the active chart: update intervals in sub-bar playback, else bars. */
function forwardClock(state: Pick<TradingReplayState, 'activeBars' | 'updateInterval'>, clock: number, steps = 1): number | null {
  const stepMs = replaySubBarStepMs(state.activeBars[0]?.interval, state.updateInterval);
  return stepMs === null ? nextReplayClock(state.activeBars, clock, steps) : nextSubBarClock(state.activeBars, clock, stepMs, steps);
}

/** Queued replay-account work that will not run: its session restarted or an earlier bar failed. */
class ReplayDroppedError extends Error {}

const RESTARTED = 'Replay restarted before the request ran.';

/** The replay account's request queue; `generation` changes whenever queued work must be dropped. */
const execution = {
  generation: 0,
  chain: Promise.resolve() as Promise<unknown>,
  /** The clock up to which bars have been queued. */
  enqueuedThrough: null as number | null,
  seedKey: null as string | null,
  dropReason: RESTARTED,
};

function restartTradingSession(): void {
  useTradingStore.getState().restartReplaySession();
}

/**
 * Whether a bar of `instrumentId` could fill or move the replay account: a
 * working order or an open position in that instrument. Positions copied from
 * the live account in other instruments do not count.
 */
export function replayAccountHasExposure(snapshot: PaperAccountSnapshot | null, instrumentId: string | undefined): boolean {
  if (!snapshot || !instrumentId) return false;
  return snapshot.open_orders.some((order) => order.instrument_id === instrumentId)
    || snapshot.positions.some((position) => position.instrument_id === instrumentId && Number(position.quantity) !== 0);
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function dropQueue(reason: string): void {
  execution.generation += 1;
  execution.chain = Promise.resolve();
  execution.dropReason = reason;
}

function resetExecution(): void {
  dropQueue(RESTARTED);
  execution.enqueuedThrough = null;
  execution.seedKey = null;
  useTradingReplayStore.setState({ snapshot: null, advancedThrough: null, pendingExecutions: 0, executionError: null });
}

type ExecutionContext = {
  snapshot: () => PaperAccountSnapshot;
  /** Store a result; throws when the queue was dropped meanwhile. */
  commit: (snapshot: PaperAccountSnapshot, through?: number) => void;
};

/** Run `work` on the replay account after everything already queued, one request at a time. */
function enqueue<T>(work: (context: ExecutionContext) => Promise<T>): Promise<T> {
  const store = useTradingReplayStore;
  const generation = execution.generation;
  const current = () => {
    if (generation !== execution.generation) throw new ReplayDroppedError(execution.dropReason);
    const snapshot = store.getState().snapshot;
    if (!snapshot) throw new Error('Replay account is still loading.');
    return snapshot;
  };
  const context: ExecutionContext = {
    snapshot: current,
    commit: (snapshot, through) => {
      current();
      store.setState(through === undefined ? { snapshot } : { snapshot, advancedThrough: through });
    },
  };
  store.setState((state) => ({ pendingExecutions: state.pendingExecutions + 1 }));
  const task = execution.chain.then(() => work(context)).finally(() => {
    if (generation === execution.generation) {
      store.setState((state) => ({ pendingExecutions: Math.max(0, state.pendingExecutions - 1) }));
    }
  });
  execution.chain = task.catch(() => undefined);
  return task;
}

/**
 * Queue every active-chart bar the clock has passed since the last queued
 * one. Bars that cannot touch the account (no working order or position in
 * the instrument) only move `advancedThrough`; the rest go to the server
 * kernel one at a time, each on the previous result.
 */
function advanceToClock(): void {
  const { activeBars, clock, snapshot } = useTradingReplayStore.getState();
  const from = execution.enqueuedThrough;
  if (!snapshot || clock === null || from === null || clock <= from) return;
  const bars = activeBars.slice(replayVisibleCount(activeBars, from), replayVisibleCount(activeBars, clock));
  execution.enqueuedThrough = clock;
  const generation = execution.generation;
  void enqueue(async ({ snapshot: current, commit }) => {
    for (const bar of bars) {
      const close = barCloseTime(bar);
      // A bar with no knowable close is skipped here as it is by the clock.
      if (!Number.isFinite(close)) continue;
      if (replayAccountHasExposure(current(), bar.instrument_id)) commit(await advanceReplaySnapshot(current(), sessionBar(bar)), close);
      else commit(current(), close);
    }
    commit(current(), clock);
    useTradingReplayStore.setState({ executionError: null });
  }).catch((error: unknown) => {
    // Ignore work dropped on purpose and failures from an earlier session.
    if (error instanceof ReplayDroppedError || generation !== execution.generation) return;
    const message = errorMessage(error, 'Replay execution failed.');
    // Drop what is queued after the failed bar and resume from the last advanced bar.
    dropQueue(`Replay execution failed; the order was not placed. ${message}`);
    execution.enqueuedThrough = useTradingReplayStore.getState().advancedThrough;
    useTradingReplayStore.setState({ pendingExecutions: 0, playing: false, executionError: message });
  });
}

function seedSnapshot(snapshot: PaperAccountSnapshot, key: string): void {
  const { clock, snapshot: existing } = useTradingReplayStore.getState();
  if (execution.seedKey === key && existing) return;
  dropQueue(RESTARTED);
  execution.seedKey = key;
  execution.enqueuedThrough = clock;
  useTradingReplayStore.setState({ snapshot, advancedThrough: clock, pendingExecutions: 0, executionError: null });
}

/** The bar as the replay kernel sees it: priced by the replay session's own feed binding. */
function sessionBar(bar: MarketBar): ReplayExecutionMarketBar {
  return { ...bar, binding_id: useTradingReplayStore.getState().activeBindingId };
}

/**
 * The bar an order placed now executes at: derived from the clock itself, not
 * from the published `bar`, which an effect updates only after the clock moves.
 */
function orderBar(): MarketBar | null {
  const { activeBars, bar, clock } = useTradingReplayStore.getState();
  if (clock !== null && activeBars.length > 0) return replayTradableBarAtClock(activeBars, clock);
  return bar;
}

/**
 * Whether the clock is inside a bar in sub-bar playback. Replay orders fill against whole bars, so an order placed
 * mid-bar would be priced at the last closed bar and then meet the part of the forming bar before it existed.
 */
function clockMidBar(): boolean {
  const { activeBars, clock, updateInterval } = useTradingReplayStore.getState();
  if (clock === null || replaySubBarStepMs(activeBars[0]?.interval, updateInterval) === null) return false;
  const count = replayVisibleCount(activeBars, clock);
  return count === 0 || barCloseTime(activeBars[count - 1]) !== clock;
}

function placeOrder(input: PaperOrderInput): Promise<ReplayResult> {
  if (clockMidBar()) return Promise.reject(new Error('Replay orders fill on whole bars: step to the bar close to trade during sub-bar playback.'));
  const bar = orderBar();
  if (!bar) return Promise.reject(new Error('Select a replay bar before trading.'));
  // Replay orders trade on the replay session's feed, the one its bars come from.
  const order = { ...input, binding_id: useTradingReplayStore.getState().activeBindingId };
  // Bring the account up to the clock first, so the order follows every earlier bar.
  advanceToClock();
  return enqueue(async ({ snapshot, commit }) => {
    const close = barCloseTime(bar);
    const advancedThrough = useTradingReplayStore.getState().advancedThrough;
    // The queue has normally applied this bar already; it must not reach working orders twice.
    const advanceBar = !(advancedThrough !== null && advancedThrough >= close);
    const result = await placeReplayOrder(snapshot(), order, sessionBar(bar), { advanceBar });
    commit(result.snapshot, advanceBar && Number.isFinite(close) ? close : undefined);
    useTradingReplayStore.setState({ executionError: null });
    return result;
  });
}

export const useTradingReplayStore = create<TradingReplayState>((set, get) => ({
  bar: null,
  snapshot: null,
  clock: null,
  startTime: null,
  selecting: true,
  playing: false,
  speed: DEFAULT_REPLAY_SPEED,
  updateInterval: null,
  intrabarNote: null,
  activeBars: NO_BARS,
  activeBindingId: null,
  activeChartKey: null,
  advancedThrough: null,
  pendingExecutions: 0,
  executionError: null,
  setBar: (bar) => set({ bar }),
  setSnapshot: (snapshot) => set({ snapshot }),
  seedSnapshot,
  placeOrder,
  setSpeed: (speed) => set({ speed }),
  setUpdateInterval: (updateInterval) => set({ updateInterval, intrabarNote: null }),
  setIntrabarNote: (intrabarNote) => set({ intrabarNote }),
  setActiveBars: (activeBars, bindingId = null) => set({ activeBars, activeBindingId: bindingId }),
  setActiveChartKey: (key) => {
    const previous = get().activeChartKey;
    if (previous === key) return;
    set({ activeChartKey: key });
    if (previous === null) return;
    get().setPlaying(false);
    restartTradingSession();
  },
  setPlaying: (playing) => {
    const state = get();
    if (!playing) {
      if (state.playing) set({ playing: false });
      return;
    }
    if (state.clock === null || state.selecting) return;
    if (forwardClock(state, state.clock) === null) return;
    set({ playing: true, executionError: null });
  },
  togglePlaying: () => get().setPlaying(!get().playing),
  chooseStart: (time) => {
    set({ clock: time, startTime: time, selecting: false, playing: false });
    restartTradingSession();
  },
  beginSelecting: () => set({ selecting: true, playing: false }),
  stepForward: (steps = 1) => {
    const state = get();
    const { clock, playing, selecting } = state;
    if (clock === null || selecting) return false;
    const next = forwardClock(state, clock, steps);
    if (next === null) {
      if (playing) set({ playing: false });
      return false;
    }
    // Playback stops on the active chart's last bar.
    set({ clock: next, playing: playing && forwardClock(state, next) !== null });
    advanceToClock();
    return true;
  },
  playbackTick: (steps) => {
    const { activeBars, bar, pendingExecutions, snapshot } = get();
    const instrumentId = bar?.instrument_id ?? activeBars[0]?.instrument_id;
    if (pendingExecutions > 0 && replayAccountHasExposure(snapshot, instrumentId)) return false;
    return get().stepForward(steps);
  },
  stepBack: () => {
    const { activeBars, clock, selecting, startTime } = get();
    if (clock === null || startTime === null || selecting) return false;
    const previous = previousReplayClock(activeBars, clock, startTime);
    if (previous === null) return false;
    set({ clock: previous, playing: false });
    restartTradingSession();
    return true;
  },
  resetToStart: () => {
    const { startTime } = get();
    if (startTime === null) return;
    set({ clock: startTime, selecting: false, playing: false });
    restartTradingSession();
  },
  resetExecution,
  clear: () => {
    resetExecution();
    set({
      bar: null,
      clock: null,
      intrabarNote: null,
      startTime: null,
      selecting: true,
      playing: false,
      activeBars: NO_BARS,
      activeBindingId: null,
      activeChartKey: null,
    });
  },
}));

// A new replay trading session (restart, new start bar, tab switch, entering
// replay) starts from a fresh account: queued work for the old one is dropped.
useTradingStore.subscribe((state, previous) => {
  if (state.replaySessionId !== previous.replaySessionId) useTradingReplayStore.getState().resetExecution();
});
