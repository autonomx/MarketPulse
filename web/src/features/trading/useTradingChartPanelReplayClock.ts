import { useEffect, useMemo, useRef, useState } from 'react';
import { formingBar, loadIntrabars, MAX_INTRABAR_BARS } from './intrabarData';
import { barCloseTime, replaySubBarStepMs, replayTickPlan, replayVisibleCount } from './replayClock';
import { filterExtendedHours } from './tradingChartWorkflow';
import { useTradingReplayStore } from './tradingReplayStore';
import type { MarketBar } from './tradingTypes';
import { startTicker } from '../../shared/timers';

export type ChartReplayClockInput = {
  active: boolean;
  replayMode: boolean;
  /** The chart's loaded bars, normalized and time-ordered. */
  bars: readonly MarketBar[];
  /** The chart's instrument, binding and interval. */
  chartKey: string;
  /** The feed binding the chart's bars come from; replay trading on this chart uses it. */
  bindingId?: string | null;
  /** Reloads the chart's bars; called when replay ends, since no chart streams during replay. */
  reloadBars: () => void;
  /** The chart's instrument and interval, for sub-bar playback's intrabar data (TVP-8.1). */
  instrumentId?: string;
  interval?: string;
  /** Whether the chart shows pre- and post-market bars; the forming bar is built from the same sessions. */
  showExtendedHours?: boolean;
};

/** A run of the chart's bars whose lower bars one intrabar request covers, and their load state. */
type IntrabarChunk = { lowerInterval: string; first: number; last: number; bars: readonly MarketBar[] | 'loading' | 'failed' };

/** How long a failed chunk waits before it is asked for again. */
const INTRABAR_RETRY_MS = 5_000;

/** The run of bars from `first` whose span fits one request of `stepMs` bars (at least the one bar). */
function chunkFrom(bars: readonly MarketBar[], first: number, stepMs: number): number {
  const start = Date.parse(bars[first].start_time);
  let last = first;
  while (last + 1 < bars.length && (Date.parse(bars[last + 1].end_time) - start) / stepMs <= MAX_INTRABAR_BARS) last += 1;
  return last;
}

/**
 * The chart's forming bar at the clock in sub-bar playback (TVP-8.1): the next bar after the closed ones, built from
 * the lower bars that closed by then. Null in bar-by-bar playback, or until its intrabar data loads.
 */
function useReplayFormingBar(input: {
  replay: boolean; active: boolean; bars: readonly MarketBar[]; instrumentId?: string; bindingId: string | null; interval?: string; showExtendedHours?: boolean;
}): MarketBar | null {
  const { replay, active, bars, instrumentId, bindingId, interval, showExtendedHours = true } = input;
  const [forming, setForming] = useState<MarketBar | null>(null);
  // Only the active chart reports where its intrabar data stops; switching charts keeps the loaded chunks.
  const activeRef = useRef(active);
  activeRef.current = active;
  useEffect(() => {
    setForming(null);
    if (!replay || !instrumentId || !interval) return;
    const chunks: IntrabarChunk[] = [];
    const timers = new Set<ReturnType<typeof setTimeout>>();
    let cancelled = false;
    const note = (text: string | null) => { if (activeRef.current) useTradingReplayStore.getState().setIntrabarNote(text); };
    const load = (chunk: IntrabarChunk) => {
      chunk.bars = 'loading';
      const { lowerInterval } = chunk;
      loadIntrabars({ instrumentId, bindingId, interval, lowerInterval, start: Date.parse(bars[chunk.first].start_time), end: Date.parse(bars[chunk.last].end_time) })
        .then((response) => {
          if (cancelled) return;
          // The chart's extended-hours setting applies to the bars it is built from too.
          chunk.bars = filterExtendedHours(response.bars as MarketBar[], showExtendedHours);
          note(response.complete ? null
            : response.available_from ? `${lowerInterval} data starts ${new Date(response.available_from).toLocaleString()}` : `No ${lowerInterval} data here`);
          update();
          // Fetch the next run ahead of the clock, so its bars form too.
          const next = chunk.last + 1;
          if (next < bars.length && !chunks.some((item) => item.lowerInterval === lowerInterval && item.first === next)) {
            const following: IntrabarChunk = { lowerInterval, first: next, last: chunkFrom(bars, next, replaySubBarStepMs(interval, lowerInterval) ?? 1), bars: 'loading' };
            chunks.push(following);
            load(following);
          }
        })
        .catch(() => {
          if (cancelled) return;
          chunk.bars = 'failed';
          note(`Couldn't load ${lowerInterval} data; retrying`);
          const timer = setTimeout(() => {
            timers.delete(timer);
            if (cancelled) return;
            load(chunk);
          }, INTRABAR_RETRY_MS);
          timers.add(timer);
        });
    };
    const update = () => {
      const { clock, updateInterval } = useTradingReplayStore.getState();
      const stepMs = replaySubBarStepMs(interval, updateInterval);
      const index = clock === null ? -1 : replayVisibleCount(bars, clock);
      const next = index < 0 ? undefined : bars[index];
      const start = next ? Date.parse(next.start_time) : Number.NaN;
      if (clock === null || stepMs === null || !updateInterval || !next || !(start < clock)) {
        setForming(null);
        return;
      }
      let chunk = chunks.find((item) => item.lowerInterval === updateInterval && item.first <= index && index <= item.last);
      if (!chunk) {
        chunk = { lowerInterval: updateInterval, first: index, last: chunkFrom(bars, index, stepMs), bars: 'loading' };
        chunks.push(chunk);
        load(chunk);
        return;
      }
      if (chunk.bars === 'loading' || chunk.bars === 'failed') return;
      const end = Date.parse(next.end_time);
      const lower = chunk.bars.filter((bar) => {
        const time = Date.parse(bar.start_time);
        return time >= start && time < end;
      });
      setForming(formingBar(next, lower, clock));
    };
    update();
    const unsubscribe = useTradingReplayStore.subscribe((state, previous) => {
      if (state.clock !== previous.clock || state.updateInterval !== previous.updateInterval) update();
    });
    return () => {
      cancelled = true;
      for (const timer of timers) clearTimeout(timer);
      unsubscribe();
    };
  }, [bars, bindingId, instrumentId, interval, replay, showExtendedHours]);
  return forming;
}

/**
 * One chart's view of the shared replay clock (TVP-8.1).
 *
 * Every chart derives what it shows from the clock. A chart re-renders only
 * when its own visible bar count (or, in sub-bar playback, its forming bar)
 * changes, not on every playback tick. The
 * active chart also feeds the clock: it publishes its bars (which step and
 * play advance through) and runs the playback ticker.
 */
export function useChartReplayClock({ active, replayMode, bars, chartKey, bindingId = null, reloadBars, instrumentId, interval, showExtendedHours }: ChartReplayClockInput) {
  const selecting = useTradingReplayStore((state) => state.selecting);
  const clockSet = useTradingReplayStore((state) => state.clock !== null);
  const visibleBarCount = useTradingReplayStore((state) => (state.clock === null ? 0 : replayVisibleCount(bars, state.clock)));
  const startBarCount = useTradingReplayStore((state) => (state.startTime === null ? 0 : replayVisibleCount(bars, state.startTime)));
  const hasPreviousBar = useTradingReplayStore((state) => state.clock !== null && state.startTime !== null && state.clock > state.startTime);
  const startTime = useTradingReplayStore((state) => state.startTime);
  const playing = useTradingReplayStore((state) => state.playing);
  const speed = useTradingReplayStore((state) => state.speed);

  const choosingStart = replayMode && active && selecting;
  const visible = replayMode && clockSet && !choosingStart;
  const forming = useReplayFormingBar({ replay: visible, active, bars, instrumentId, bindingId, interval, showExtendedHours });
  const currentBar = replayMode && visibleBarCount > 0 ? bars[visibleBarCount - 1] ?? null : null;
  const startBar = replayMode && startBarCount > 0 ? bars[startBarCount - 1] ?? null : null;
  const hasNextBar = visible && visibleBarCount < bars.length;
  // Replay trading prices orders at the latest bar whose close is known.
  const tradableBar = useMemo(() => {
    if (!replayMode) return null;
    for (let index = visibleBarCount - 1; index >= 0; index -= 1) {
      if (Number.isFinite(barCloseTime(bars[index]))) return bars[index];
    }
    return null;
  }, [bars, replayMode, visibleBarCount]);

  // The active chart's identity decides whether replay trading restarts:
  // switching between charts of the same symbol, feed and interval keeps it.
  useEffect(() => {
    if (!replayMode || !active) return;
    useTradingReplayStore.getState().setActiveChartKey(chartKey);
  }, [active, chartKey, replayMode]);

  useEffect(() => {
    if (!active) return;
    const store = useTradingReplayStore.getState();
    if (!replayMode) {
      store.clear();
      return;
    }
    store.setActiveBars(bars, bindingId);
  }, [active, bars, bindingId, replayMode]);

  useEffect(() => {
    if (!replayMode || !active) return;
    useTradingReplayStore.getState().setBar(tradableBar);
  }, [active, replayMode, tradableBar]);

  useEffect(() => {
    if (!active || !playing || !visible) return;
    const { intervalMs, barsPerTick } = replayTickPlan(speed);
    // In sub-bar playback the store steps by the update interval instead of a bar.
    return startTicker(() => { useTradingReplayStore.getState().playbackTick(barsPerTick); }, intervalMs);
  }, [active, playing, speed, visible]);

  const wasReplayingRef = useRef(replayMode);
  useEffect(() => {
    // Bars that closed during replay were never streamed; reload them on the way back to real time.
    if (wasReplayingRef.current && !replayMode) reloadBars();
    wasReplayingRef.current = replayMode;
  }, [reloadBars, replayMode]);

  return {
    replayChoosingStart: choosingStart,
    replayVisible: visible,
    replayVisibleBarCount: replayMode ? visibleBarCount : 0,
    /** The forming bar after the visible ones in sub-bar playback (TVP-8.1). */
    replayFormingBar: visible && forming && forming.start_time === bars[visibleBarCount]?.start_time ? forming : null,
    replayCurrentBar: currentBar,
    replayStartBar: startBar,
    replayStartTime: startTime,
    replayHasNextBar: hasNextBar,
    replayHasPreviousBar: visible && hasPreviousBar,
    replayPlaying: playing,
  };
}
