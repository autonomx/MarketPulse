import { useEffect, useRef } from 'react';
import type { TradingChartAdapter } from './chart/chartAdapter';
import { indicatorUsesSeparatePane } from './indicators/coreIndicators';
import { sessionForInstrument } from './indicators/tradingSessions';
import type { MarketBar } from './tradingTypes';
import { TradingChartPanelProps, applyVisibleRange } from './tradingChartPanelModel';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling } from './useTradingChartPanelData';
import type { useChartPanelData } from './useTradingChartPanelData';
import type { useChartLifecycle } from './useTradingChartPanelLifecycle';
import { useTradingCommand } from './commands/useTradingCommands';

/** A replay step that reveals more bars than this resets the series instead of appending. */
const MAX_APPENDED_REPLAY_BARS = 50;

/** Keeps the chart in step with its data, replay, chart type, indicators and comparisons. */
export function useChartSync(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData> & ReturnType<typeof useChartLifecycle>) {
  const {
    active, adapter, adapterRef, allBarsRef, barsRef, chartQuery, chartType, drawings, fittedBarsKeyRef,
    forceLiveRender, fullscreenIndicator, fullscreenMainPane, historyLimit, hostRef, indicators, indicatorBindingRef, indicatorSessionRef, indicatorsRef,
    interval, loadedBars, minimizedIndicators, pendingIntervalScrollRef, pendingRangeIntervalRef,
    refreshIndicatorPanes, replayFormingBar, replayMode, replayVisible, replayVisibleBarCount, replayWasVisibleRef, rightOffset,
    scheduleIndicators, selectedDrawing, drawingsHidden, selectedRangeRef, selectedTimezone, setSelectedRangeLabel, streamDataKeyRef,
    streamRevisionRef, timezoneId,
  } = ws;

  // What the adapter last showed in replay, so a step that only reveals new
  // bars appends them instead of resetting the whole series.
  const replayRenderRef = useRef<{ adapter: TradingChartAdapter; bars: readonly MarketBar[]; count: number; forming: boolean } | null>(null);

  useEffect(() => {
    const bars = loadedBars;
    allBarsRef.current = bars;
    const previousDataIdentity = streamDataKeyRef.current?.split('|').slice(0, 2).join('|') ?? null;
    const dataKey = chartQuery.data
      ? `${chartQuery.data.instrument.instrument_id}|${chartQuery.data.binding.binding_id}|${chartQuery.data.interval}|${historyLimit}`
      : null;
    if (dataKey !== null && dataKey !== streamDataKeyRef.current) {
      streamDataKeyRef.current = dataKey;
      streamRevisionRef.current = Math.max(
        1,
        ...bars.map((bar) => Number(bar.ingestion_revision) || 1),
      );
    }
    forceLiveRender((value) => value + 1);
    const dataChanged = dataKey !== null && dataKey !== fittedBarsKeyRef.current;
    const replayViewChanged = replayWasVisibleRef.current !== replayVisible;
    replayWasVisibleRef.current = replayVisible;
    // Selecting a replay bar replaces the data with the historical prefix,
    // but must preserve the pre-click logical range. Fitting that prefix would
    // move the selected bar to the right edge instead of leaving it where the
    // user clicked. Fit only when loading new data or returning to live mode.
    const shouldFit = dataChanged || (replayViewChanged && !replayVisible);
    const currentDataIdentity = dataKey?.split('|').slice(0, 2).join('|') ?? null;
    const keepCustomRange = typeof selectedRangeRef.current === 'object'
      && selectedRangeRef.current !== null
      && previousDataIdentity !== null
      && previousDataIdentity === currentDataIdentity;
    const keepSelectedRange = dataChanged && (pendingRangeIntervalRef.current === interval || keepCustomRange);
    if (dataChanged && !keepSelectedRange) {
      selectedRangeRef.current = undefined;
      setSelectedRangeLabel('All');
    }
    const replayCount = replayVisible ? replayVisibleBarCount : null;
    // Sub-bar playback (TVP-8.1): the forming bar follows the closed ones.
    const forming = replayCount === null ? null : replayFormingBar;
    const visibleBars = replayCount === null ? bars : forming ? [...bars.slice(0, replayCount), forming] : bars.slice(0, replayCount);
    barsRef.current = visibleBars;
    const targetAdapter = adapterRef.current;
    const previous = replayRenderRef.current;
    // Appending only ever updates the chart's last bar or adds later ones: a shown forming bar is replaced by its
    // closed bar or a newer forming bar, never dropped without a reset.
    const appendOnly = targetAdapter !== null && replayCount !== null && previous !== null && !shouldFit && !replayViewChanged
      && previous.adapter === targetAdapter && previous.bars === bars
      && replayCount >= previous.count && replayCount - previous.count <= MAX_APPENDED_REPLAY_BARS
      && (replayCount > previous.count || forming !== null || !previous.forming);
    if (appendOnly) {
      for (const bar of bars.slice(previous.count, replayCount)) targetAdapter.updateBar(bar);
      if (forming) targetAdapter.updateBar(forming);
    } else {
      targetAdapter?.setBars(visibleBars, shouldFit);
    }
    replayRenderRef.current = replayCount === null || targetAdapter === null ? null : { adapter: targetAdapter, bars, count: replayCount, forming: forming !== null };
    // The active chart keeps the viewport where its start bar was clicked;
    // the other charts in the layout jump to the replay clock.
    if (replayViewChanged && replayVisible && !active && visibleBars.length > 0) targetAdapter?.scrollToLatest();
    if (keepSelectedRange && selectedRangeRef.current !== undefined && visibleBars.length > 0 && targetAdapter) {
      applyVisibleRange(targetAdapter.api(), selectedRangeRef.current, visibleBars, interval, rightOffset, selectedTimezone);
    }
    if (pendingIntervalScrollRef.current && dataChanged && visibleBars.length > 0 && targetAdapter) {
      targetAdapter.scrollToLatest();
      pendingIntervalScrollRef.current = false;
    }
    if (keepSelectedRange) pendingRangeIntervalRef.current = null;
    if (dataKey !== null && bars.length > 0) fittedBarsKeyRef.current = dataKey;
    indicatorSessionRef.current = sessionForInstrument(chartQuery.data?.instrument);
    indicatorBindingRef.current = chartQuery.data?.binding.binding_id ?? ws.bindingId ?? null;
    scheduleIndicators();
    // Keyed by timezoneId and the loaded data, not by values derived from them (selectedTimezone, historyLimit).
    // Replay re-runs it only when this chart's visible bar count or forming bar changes, not on every clock tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, loadedBars, chartQuery.data, interval, replayFormingBar, replayMode, replayVisible, replayVisibleBarCount, rightOffset, scheduleIndicators, timezoneId, adapterRef, allBarsRef, barsRef, fittedBarsKeyRef, forceLiveRender, pendingIntervalScrollRef, pendingRangeIntervalRef, replayWasVisibleRef, selectedRangeRef, setSelectedRangeLabel, streamDataKeyRef, streamRevisionRef, indicatorSessionRef]);

  useEffect(() => {
    adapterRef.current?.setChartType(chartType, barsRef.current);
    if (selectedRangeRef.current !== undefined && barsRef.current.length > 0 && adapterRef.current) {
      applyVisibleRange(adapterRef.current.api(), selectedRangeRef.current, barsRef.current, interval, rightOffset, selectedTimezone);
    }
    // Keyed by timezoneId, not by the selectedTimezone derived from it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartType, interval, rightOffset, timezoneId, adapterRef, barsRef, selectedRangeRef]);

  useEffect(() => {
    indicatorsRef.current = indicators;
    scheduleIndicators();
  }, [indicators, scheduleIndicators, indicatorsRef]);

  useTradingCommand('drawing.undo', () => drawings.undo(), () => active);
  useTradingCommand('drawing.redo', () => drawings.redo(), () => active);
  useTradingCommand('drawing.delete', () => drawings.removeSelected(), () => active && Boolean(selectedDrawing) && !drawingsHidden);

  useEffect(() => {
    const targetAdapter = adapterRef.current;
    if (!targetAdapter) return;
    if (fullscreenIndicator) {
      targetAdapter.setIndicatorPaneFullscreen(fullscreenIndicator);
    } else if (fullscreenMainPane) {
      targetAdapter.setMainPaneFullscreen(true);
    } else {
      for (const indicator of indicators) {
        if (indicatorUsesSeparatePane(indicator.id)) {
          targetAdapter.setIndicatorPaneMinimized(indicator.id, minimizedIndicators.has(indicator.id));
        }
      }
    }
    refreshIndicatorPanes(targetAdapter);
    const frame = window.requestAnimationFrame(() => refreshIndicatorPanes(targetAdapter));
    return () => window.cancelAnimationFrame(frame);
  }, [adapter, fullscreenIndicator, fullscreenMainPane, indicators, minimizedIndicators, refreshIndicatorPanes, adapterRef]);

  useEffect(() => {
    if (!adapter) return;
    const refresh = () => {
      try {
        adapter.refreshIndicatorPaneFullscreen();
      } catch {
        // The adapter may be disposed during a chart switch.
      }
      const frame = window.requestAnimationFrame(() => refreshIndicatorPanes(adapter));
      return frame;
    };
    refresh();
    window.addEventListener('resize', refresh);
    const host = hostRef.current;
    const observer = typeof ResizeObserver === 'undefined' || !host ? null : new ResizeObserver(refresh);
    if (observer && host) observer.observe(host);
    return () => {
      window.removeEventListener('resize', refresh);
      observer?.disconnect();
    };
  }, [adapter, refreshIndicatorPanes, hostRef]);

  return {

  };
}
