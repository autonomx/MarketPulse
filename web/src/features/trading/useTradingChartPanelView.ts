import { useEffect } from 'react';
import { upsertChartBar } from './chart/chartAdapter';
import { indicatorUsesSeparatePane } from './indicators/coreIndicators';
import { bindingStreamsInterval } from './liveChartFeed';
import { tradingStreamHub } from './streaming/tradingStreamHub';
import { isTradingFormulaInstrumentId } from './tradingFormula';
import { TRADING_TIMEZONE_OPTIONS } from './tradingTime';
import { TradingChartPanelProps, normalizeStreamBar } from './tradingChartPanelModel';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling } from './useTradingChartPanelData';
import type { useChartPanelData } from './useTradingChartPanelData';
import type { useChartLifecycle } from './useTradingChartPanelLifecycle';
import type { useChartSync } from './useTradingChartPanelSync';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';

/** What the panel shows: quote, change, legend and visible indicator outputs. */
export function useChartView(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData> & ReturnType<typeof useChartLifecycle> & ReturnType<typeof useChartSync>) {
  const {
    adapterRef, allBarsRef, barsRef, chartFocusMode, chartId, chartQuery, chartSettings, comparisonRenderData, contextMenu,
    forceLiveRender, fullscreenIndicator, fullscreenIndicatorRef, fullscreenMainPane, fullscreenMainPaneRef,
    indicatorOutputs, indicators, instrumentId, interval, replayMode, scheduleIndicators, selectedIndicator,
    setFullscreenIndicator, setFullscreenMainPane, setStreamError, setStreamStatus, streamRevisionRef, timezoneId,
  } = ws;
  const showExtendedHours = chartSettings?.extendedHours !== false;

  useEffect(() => {
    const resolved = chartQuery.data?.binding;
    if (!instrumentId || !resolved) return;
    // Every chart follows the shared replay clock, so none of them streams live bars during replay.
    if (replayMode) {
      setStreamStatus('replay');
      return;
    }
    if (isTradingFormulaInstrumentId(instrumentId)) {
      setStreamStatus('polling');
      return startPolling(() => chartQuery.refetch(), POLL_INTERVALS_MS.chartFallback);
    }
    setStreamError(null);
    if (!bindingStreamsInterval(resolved, interval)) {
      setStreamStatus('polling');
      return startPolling(() => chartQuery.refetch(), POLL_INTERVALS_MS.chartFallback);
    }
    // While the server's upstream reconnects (IB Gateway restarting, say) the socket stays open: poll meanwhile.
    let stopFallbackPolling: (() => void) | null = null;
    const stopFallback = () => {
      stopFallbackPolling?.();
      stopFallbackPolling = null;
    };
    let continuityChecked = false;
    const unsubscribe = tradingStreamHub.subscribe(
      chartId,
      instrumentId,
      interval,
      (message) => {
        if (message.type === 'error') {
          // The gateway reports upstream disconnects before the hub retries.
          // Keep those transient transport errors out of the chart overlay;
          // the stream status indicator still shows the reconnect state, and
          // non-transport/configuration errors remain visible.
          if (message.code !== 'stream_failed') setStreamError(message.message);
          return;
        }
        if (message.type !== 'bar') return; // status messages reach the status listener instead
        const providerRevision = Number(message.bar.ingestion_revision) || 0;
        const ingestionRevision = Math.max(streamRevisionRef.current + 1, providerRevision);
        streamRevisionRef.current = ingestionRevision;
        const bar = normalizeStreamBar(message, resolved.provider, ingestionRevision);
        if (!continuityChecked) {
          continuityChecked = true;
          // A bar can close between the history request and the first live bar, and the
          // chart can only revise its last bar: reload the history once if one is missing.
          const last = barsRef.current[barsRef.current.length - 1];
          if (last && Date.parse(bar.start_time) > Date.parse(last.end_time)) void chartQuery.refetch();
        }
        if (adapterRef.current?.updateBar(bar)) {
          barsRef.current = upsertChartBar(barsRef.current, bar);
          allBarsRef.current = upsertChartBar(allBarsRef.current, bar);
          forceLiveRender((value) => value + 1);
          scheduleIndicators(bar.is_final ? 0 : 100);
        }
      },
      (status) => {
        setStreamStatus(status);
        if (status === 'live') {
          setStreamError(null);
          stopFallback();
        }
        if (status === 'polling') stopFallbackPolling ??= startPolling(() => chartQuery.refetch(), POLL_INTERVALS_MS.chartFallback);
        if (status === 'closed' || status === 'error') void chartQuery.refetch();
      },
      resolved.binding_id,
      // Session-calendar (IBKR) bars are bucketed with or without extended hours; 24x7 streams ignore it.
      resolved.provider === 'ibkr' ? showExtendedHours : null,
    );
    return () => {
      stopFallback();
      unsubscribe();
    };
    // Resubscribes the stream when its identity changes, not on every chart query result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartId, instrumentId, interval, replayMode, chartQuery.data?.binding.binding_id, showExtendedHours, scheduleIndicators, adapterRef, allBarsRef, barsRef, forceLiveRender, setStreamError, setStreamStatus, streamRevisionRef]);

  const provenance = chartQuery.data?.provenance;

  const resolvedBinding = chartQuery.data?.binding;

  const bars = barsRef.current.length > 0 ? barsRef.current : chartQuery.data?.bars ?? [];

  const latest = bars[bars.length - 1];

  const selectedTimezoneOption = TRADING_TIMEZONE_OPTIONS.find((option) => option.id === timezoneId) ?? TRADING_TIMEZONE_OPTIONS[0];

  const previous = bars[bars.length - 2];

  const latestClose = Number(latest?.close ?? 0);

  const previousClose = Number(previous?.close ?? latest?.open ?? 0);

  const change = latestClose - previousClose;

  const changePercent = previousClose === 0 ? 0 : change / previousClose * 100;

  const direction = change < 0 ? 'negative' : 'positive';

  const paneIndicators = indicators.filter((indicator) => indicator.enabled && indicatorUsesSeparatePane(indicator.id));

  const indicatorControls = indicators.filter((indicator) => indicator.enabled);

  const legendIndicators = fullscreenMainPane
    ? indicatorControls.filter((indicator) => !indicatorUsesSeparatePane(indicator.id))
    : fullscreenIndicator
      ? indicatorControls.filter((indicator) => indicator.id === fullscreenIndicator)
      : indicatorControls;

  const legendComparisons = fullscreenIndicator ? [] : comparisonRenderData;

  const legendCount = legendIndicators.length + legendComparisons.length;

  const visibleIndicatorOutputs = fullscreenMainPane
    ? indicatorOutputs.filter((output) => output.pane === 0)
    : fullscreenIndicator
      ? indicatorOutputs.filter((output) => output.key.split(':', 1)[0] === fullscreenIndicator)
      : indicatorOutputs;

  const selectedIndicatorConfig = selectedIndicator
    ? indicators.find((indicator) => indicator.id === selectedIndicator.id && indicator.enabled) ?? null
    : null;

  const contextIndicator = contextMenu?.contextIndicatorId
    ? indicators.find((indicator) => indicator.id === contextMenu.contextIndicatorId && indicator.enabled) ?? null
    : null;

  useEffect(() => {
    if (!fullscreenIndicator || paneIndicators.some((indicator) => indicator.id === fullscreenIndicator)) return;
    fullscreenIndicatorRef.current = null;
    setFullscreenIndicator(null);
    adapterRef.current?.setIndicatorPaneFullscreen(null);
  }, [fullscreenIndicator, paneIndicators, adapterRef, fullscreenIndicatorRef, setFullscreenIndicator]);

  useEffect(() => {
    if (chartFocusMode) return;
    if (fullscreenIndicatorRef.current !== null) {
      fullscreenIndicatorRef.current = null;
      setFullscreenIndicator(null);
      adapterRef.current?.setIndicatorPaneFullscreen(null);
    }
    if (fullscreenMainPaneRef.current) {
      fullscreenMainPaneRef.current = false;
      setFullscreenMainPane(false);
      adapterRef.current?.setMainPaneFullscreen(false);
    }
  }, [chartFocusMode, adapterRef, fullscreenIndicatorRef, fullscreenMainPaneRef, setFullscreenIndicator, setFullscreenMainPane]);

  return {
    provenance, resolvedBinding, bars, latest, selectedTimezoneOption, previous, latestClose, previousClose, change,
    changePercent, direction, paneIndicators, indicatorControls, legendIndicators, legendComparisons, legendCount,
    visibleIndicatorOutputs, selectedIndicatorConfig, contextIndicator,
  };
}
