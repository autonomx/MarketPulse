import { useEffect } from 'react';
import { useTradingCommand } from './commands/useTradingCommands';
import { replayBarAtClock, replayClockForBar, replayVisibleCount } from './replayClock';
import { TradingChartPanelProps } from './tradingChartPanelModel';
import { useTradingReplayStore } from './tradingReplayStore';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling } from './useTradingChartPanelData';
import type { useChartPanelData } from './useTradingChartPanelData';
import type { useChartLifecycle } from './useTradingChartPanelLifecycle';
import type { useChartSync } from './useTradingChartPanelSync';
import type { useChartView } from './useTradingChartPanelView';
import type { useChartIndicatorActions } from './useTradingChartPanelActions';
import type { useChartRangeActions } from './useTradingChartPanelActions';

type ChartReplayWorkspace = TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData> & ReturnType<typeof useChartLifecycle> & ReturnType<typeof useChartSync> & ReturnType<typeof useChartView> & ReturnType<typeof useChartIndicatorActions> & ReturnType<typeof useChartRangeActions>;

/** Bar replay on one chart: choosing the start, stepping the shared clock, and leaving replay. */
export function useChartReplayActions(ws: ChartReplayWorkspace) {
  const {
    active, adapter, allBarsRef, chartQuery, hostRef, replayChoosingStart, replayMode,
    replaySelectionIndex, replayStartTime, setReplayMarkerX, setReplayMode, setReplaySelectionIndex,
    setReplaySelectionX,
  } = ws;

  useChartWheelZoom(ws);

  const handleReplayStageClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!replayMode || !active || !adapter || allBarsRef.current.length === 0) return;
    // A chart click chooses a new replay start only after the user explicitly
    // enters Select bar mode. Normal clicks during an active replay must not
    // restart the session and discard its simulated positions.
    if (!replayChoosingStart) return;
    const target = event.target as Element;
    if (target.closest('button, input, select, textarea, [role="dialog"], .trading-drawing-overlay')) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    if (adapter.isPriceScaleCoordinate(x)) return;
    const index = adapter.barIndexAtCoordinate(x, allBarsRef.current.length);
    if (index === null) return;
    const bar = allBarsRef.current[index];
    const clock = bar ? replayClockForBar(bar) : null;
    if (clock === null) return;
    setReplaySelectionIndex(index);
    // Sets the shared clock for every chart and restarts replay trading.
    useTradingReplayStore.getState().chooseStart(clock);
  };

  const selectReplayStart = () => useTradingReplayStore.getState().beginSelecting();

  const resetReplay = () => useTradingReplayStore.getState().resetToStart();

  const previousReplayBar = () => {
    useTradingReplayStore.getState().stepBack();
  };

  const nextReplayBar = () => {
    useTradingReplayStore.getState().stepForward();
  };

  const toggleReplayPlaying = () => useTradingReplayStore.getState().togglePlaying();

  /** Leave replay for live data (jump to real time); every chart refits to its latest bars. */
  const exitReplay = () => setReplayMode(false);

  // Replay keys (TVP-8.1) act on the active chart's replay once a start bar is chosen.
  const replaying = () => active && replayMode && !replayChoosingStart;
  useTradingCommand('replay.playPause', toggleReplayPlaying, replaying);
  useTradingCommand('replay.stepForward', nextReplayBar, replaying);
  useTradingCommand('replay.stepBack', previousReplayBar, replaying);

  useEffect(() => {
    if (!adapter || !replayMode || !active || replayStartTime === null) {
      setReplayMarkerX(null);
      return;
    }
    const updateMarker = () => {
      const startBar = replayBarAtClock(allBarsRef.current, replayStartTime);
      setReplayMarkerX(startBar
        ? adapter.barTimeToCoordinate(startBar.start_time) ?? adapter.timeToCoordinate(startBar.start_time)
        : null);
    };
    const frame = window.requestAnimationFrame(updateMarker);
    window.addEventListener('resize', updateMarker);
    const host = hostRef.current;
    const observer = typeof ResizeObserver === 'undefined' || !host ? null : new ResizeObserver(updateMarker);
    observer?.observe(host as Element);
    const unsubscribeViewport = adapter.onViewportChange(updateMarker);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', updateMarker);
      observer?.disconnect();
      unsubscribeViewport();
    };
    // Not keyed by the clock: replay steps keep the start bar's logical position,
    // and viewport changes are observed above, so playback never rebuilds the observer.
  }, [active, adapter, chartQuery.data, replayMode, replayStartTime, allBarsRef, hostRef, setReplayMarkerX]);

  useEffect(() => {
    if (!replayChoosingStart || !adapter || allBarsRef.current.length === 0) {
      setReplaySelectionX(null);
      setReplaySelectionIndex(null);
      return;
    }
    if (replaySelectionIndex === null || replaySelectionIndex >= allBarsRef.current.length) {
      // Jumping during a replay starts from the clock's bar, then the start bar.
      const replayClock = useTradingReplayStore.getState().clock;
      const clockIndex = replayClock === null ? -1 : replayVisibleCount(allBarsRef.current, replayClock) - 1;
      const startIndex = replayStartTime === null ? -1 : replayVisibleCount(allBarsRef.current, replayStartTime) - 1;
      const initialIndex = clockIndex >= 0 ? clockIndex : startIndex >= 0 ? startIndex : adapter.barIndexAtCoordinate(
        adapter.indicatorPlotWidth() / 2,
        allBarsRef.current.length,
      );
      setReplaySelectionIndex(initialIndex);
      return;
    }
    const updateDivider = () => {
      const bar = allBarsRef.current[replaySelectionIndex];
      setReplaySelectionX(bar
        ? adapter.barTimeToCoordinate(bar.start_time) ?? adapter.timeToCoordinate(bar.start_time)
        : null);
    };
    updateDivider();
    window.addEventListener('resize', updateDivider);
    const host = hostRef.current;
    const observer = typeof ResizeObserver === 'undefined' || !host ? null : new ResizeObserver(updateDivider);
    observer?.observe(host as Element);
    const unsubscribeViewport = adapter.onViewportChange(updateDivider);
    return () => {
      window.removeEventListener('resize', updateDivider);
      observer?.disconnect();
      unsubscribeViewport();
    };
  }, [active, adapter, chartQuery.data, replayChoosingStart, replaySelectionIndex, replayStartTime, allBarsRef, hostRef, setReplaySelectionIndex, setReplaySelectionX]);

  return {
    handleReplayStageClick, selectReplayStart, resetReplay, previousReplayBar, nextReplayBar, toggleReplayPlaying,
    exitReplay,
  };
}

/** Wheel zoom and pan on the chart: over the price scale (or with Shift) it zooms prices, sideways it pans time. */
function useChartWheelZoom({ adapter, adapterRef, hostRef }: Pick<ChartReplayWorkspace, 'adapter' | 'adapterRef' | 'hostRef'>) {
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    // React delegates wheel events through a passive listener in this setup.
    // Use a native non-passive listener because chart zoom intentionally
    // consumes the wheel event so the page does not scroll underneath it.
    const handleChartWheel = (event: WheelEvent) => {
      const targetAdapter = adapterRef.current;
      if (!targetAdapter) return;
      event.preventDefault();
      event.stopPropagation();
      const bounds = host.getBoundingClientRect();
      const x = event.clientX - bounds.left;
      const y = event.clientY - bounds.top;
      if (targetAdapter.isPriceScaleCoordinate(x)) {
        targetAdapter.zoomPriceScaleAtCoordinate(y, event.deltaY);
        return;
      }
      if (event.deltaX !== 0) {
        targetAdapter.panTimeByPixels(-event.deltaX);
        return;
      }
      if (event.shiftKey) {
        targetAdapter.zoomPriceScaleAtCoordinate(y, event.deltaY);
        return;
      }
      targetAdapter.zoomAtCoordinate(x, event.deltaY);
    };

    host.addEventListener('wheel', handleChartWheel, { capture: true, passive: false });
    return () => host.removeEventListener('wheel', handleChartWheel, true);
  }, [adapter, adapterRef, hostRef]);
}
