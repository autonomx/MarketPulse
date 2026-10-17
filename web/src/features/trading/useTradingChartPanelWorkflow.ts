import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { chartPalette } from './chartPalette';
import { isStockInstrument } from './corporateEvents';
import { tradingApi } from './tradingApi';
import {
  barCountdownEnabled,
  centeredLogicalRange,
  chartTemplatePayload,
  chartTemplateRecordId,
  copyChartSnapshotLink, copyImageDataUrlToClipboard,
  dataDelayLabel,
  extendedSessionPriceLine,
  hasExtendedHoursBars,
  hasSessionTaggedBars,
  marketStatusLabel,
  paneDoubleClickAction,
  parseChartTemplate,
  MAX_CHART_HISTORY_LIMIT,
  parseGoToDate,
  planGoToDate,
  type ChartTemplate,
  type GoToDateTargetTime,
} from './tradingChartWorkflow';
import { isTradingFormulaInstrumentId } from './tradingFormula';
import { tradingIntervalDurationMs } from './tradingIntervals';
import { useChartTimeSync } from './chartTimeSync';
import { useTradingStore, type TradingChartSettings } from './tradingStore';
import { dateInputValue } from './tradingTime';
import type { MarketBar } from './tradingTypes';
import type { TradingChartPanelProps } from './tradingChartPanelModel';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling, useChartPanelData } from './useTradingChartPanelData';
import type { useChartLifecycle } from './useTradingChartPanelLifecycle';
import type { useChartSync } from './useTradingChartPanelSync';
import type { useChartView } from './useTradingChartPanelView';
import type { useChartIndicatorActions, useChartRangeActions } from './useTradingChartPanelActions';
import type { useChartReplayActions } from './useTradingChartPanelReplay';

type WorkflowInput = TradingChartPanelProps
  & ReturnType<typeof useChartPanelState>
  & ReturnType<typeof useChartIndicatorScheduling>
  & ReturnType<typeof useChartPanelData>
  & ReturnType<typeof useChartLifecycle>
  & ReturnType<typeof useChartSync>
  & ReturnType<typeof useChartView>
  & ReturnType<typeof useChartIndicatorActions>
  & ReturnType<typeof useChartRangeActions>
  & ReturnType<typeof useChartReplayActions>;

export type GoToDateTarget = string | number | Date;
export type GoToDateResult = 'scrolled' | 'loading' | 'invalid' | 'unavailable';
export type ChartImageCopyStatus = 'idle' | 'copying' | 'copied' | 'error';

type PendingGoToDate = { target: GoToDateTargetTime; key: string; firstBarMs: number | null };

const firstBarTime = (bars: readonly MarketBar[]) => {
  const value = Date.parse(bars[0]?.start_time ?? '');
  return Number.isFinite(value) ? value : null;
};

/** Why going further back is impossible, worded so the user can tell the causes apart. */
export function goToDateUnavailableMessage(
  cause: 'no-data' | 'max-limit' | 'history-complete' | 'feed-limit',
  earliest: string | null,
  timeZone: string,
): string {
  if (cause === 'no-data' || !earliest) return 'No chart history is loaded.';
  const start = dateInputValue(earliest, timeZone);
  if (cause === 'history-complete') return `No earlier history exists: this market's data starts ${start}.`;
  if (cause === 'feed-limit') return `The feed serves history back to ${start} only.`;
  return `The chart loads at most ${MAX_CHART_HISTORY_LIMIT.toLocaleString('en-US')} bars, back to ${start}. A longer interval reaches further back.`;
}

/** Go to date: scroll to the bar holding a time, loading older history on the existing bars request first. */
function useChartGoToDate(ws: WorkflowInput) {
  const {
    adapterRef, allBarsRef, chartQuery, historyLimit, instrumentId, interval, loadedBars, provenance, replayMode,
    selectedRangeRef, selectedTimezone, setHistoryLimitOverride, setSelectedRangeLabel,
  } = ws;
  const [goToDateOpen, setGoToDateOpen] = useState(false);
  const [goToDateError, setGoToDateError] = useState<string | null>(null);
  const [goToDateLoading, setGoToDateLoading] = useState(false);
  const pendingGoToRef = useRef<PendingGoToDate | null>(null);
  const historyKey = `${instrumentId}|${interval}`;
  const historyComplete = Boolean(provenance?.history_complete);

  const finish = useCallback((error: string | null) => {
    pendingGoToRef.current = null;
    setGoToDateLoading(false);
    setGoToDateError(error);
  }, []);

  const runGoToDate = useCallback((pending: PendingGoToDate): GoToDateResult => {
    const bars = allBarsRef.current;
    const firstBarMs = firstBarTime(bars);
    // A larger request that brought nothing older means the feed's history ends there.
    const noProgress = pending.firstBarMs !== null && firstBarMs !== null && firstBarMs >= pending.firstBarMs;
    const plan = planGoToDate(bars, pending.target, historyLimit, MAX_CHART_HISTORY_LIMIT, historyComplete || noProgress);
    if (plan.kind === 'scroll') {
      finish(null);
      const targetAdapter = adapterRef.current;
      if (targetAdapter) {
        const timeScale = targetAdapter.api().timeScale();
        selectedRangeRef.current = null;
        setSelectedRangeLabel('');
        timeScale.setVisibleLogicalRange(centeredLogicalRange(plan.index, timeScale.getVisibleLogicalRange()));
      }
      setGoToDateOpen(false);
      return 'scrolled';
    }
    if (plan.kind === 'unavailable') {
      const cause = plan.reason === 'no-earlier' ? (historyComplete ? 'history-complete' : 'feed-limit') : plan.reason;
      finish(goToDateUnavailableMessage(cause, plan.earliest, selectedTimezone));
      return 'unavailable';
    }
    pendingGoToRef.current = { ...pending, firstBarMs };
    setGoToDateLoading(true);
    setGoToDateError(null);
    setHistoryLimitOverride({ key: pending.key, limit: plan.limit });
    return 'loading';
  }, [adapterRef, allBarsRef, finish, historyComplete, historyLimit, selectedRangeRef, selectedTimezone, setHistoryLimitOverride, setSelectedRangeLabel]);

  /**
   * Scrolls the chart to a date, loading older history first when needed. A `YYYY-MM-DD` string goes
   * to the first bar of that local day in the chart timezone; `YYYY-MM-DDTHH:mm`, a timestamp or a
   * Date goes to the bar holding that instant.
   */
  const goToDate = useCallback((target: GoToDateTarget): GoToDateResult => {
    if (replayMode) {
      setGoToDateError('Leave replay to go to a date.');
      return 'invalid';
    }
    const parsed: GoToDateTargetTime | null = typeof target === 'string'
      ? parseGoToDate(target, selectedTimezone)
      : { kind: 'instant', time: target instanceof Date ? target.getTime() : target };
    if (parsed === null || (parsed.kind === 'instant' && !Number.isFinite(parsed.time))) {
      setGoToDateError('Choose a date.');
      return 'invalid';
    }
    return runGoToDate({ target: parsed, key: historyKey, firstBarMs: null });
  }, [historyKey, replayMode, runGoToDate, selectedTimezone]);

  // Each history load re-runs a pending request: scroll if the bar arrived, or ask for more.
  useEffect(() => {
    const pending = pendingGoToRef.current;
    if (!pending || chartQuery.isFetching) return;
    if (pending.key !== historyKey) {
      finish(null);
      return;
    }
    if (chartQuery.isError) {
      const reason = chartQuery.error instanceof Error ? chartQuery.error.message : 'the request failed';
      finish(`Could not load older history: ${reason}`);
      return;
    }
    runGoToDate(pending);
  }, [chartQuery.error, chartQuery.isError, chartQuery.isFetching, finish, historyKey, loadedBars, runGoToDate]);

  /** The default date for the go-to-date box: the bar in the middle of the view. */
  const goToDateDefault = () => {
    const bars = allBarsRef.current;
    const range = adapterRef.current?.api().timeScale().getVisibleLogicalRange();
    const index = range ? Math.round((range.from + range.to) / 2) : bars.length - 1;
    const bar = bars[Math.max(0, Math.min(bars.length - 1, index))];
    return dateInputValue(bar ? bar.start_time : Date.now(), selectedTimezone);
  };

  /** Opens the go-to-date box; the keyboard layer's `chart.goToDate` command calls this. */
  const openGoToDate = useCallback(() => {
    setGoToDateError(null);
    setGoToDateOpen(true);
  }, []);

  return { goToDateOpen, setGoToDateOpen, goToDateError, goToDateLoading, goToDate, openGoToDate, goToDateDefault };
}

/** Copy chart image, and copy a link to a stored snapshot of it (TVP-2.1/2.5). */
function useChartImageCopy(adapterRef: WorkflowInput['adapterRef'], instrumentId: string, interval: string) {
  const [chartImageCopyStatus, setChartImageCopyStatus] = useState<ChartImageCopyStatus>('idle');
  const [snapshotLinkStatus, setSnapshotLinkStatus] = useState<ChartImageCopyStatus>('idle');
  const copyStatusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (copyStatusTimerRef.current) clearTimeout(copyStatusTimerRef.current);
  }, []);

  /** Uploads the chart image and copies its link; resolves to the link, or null when it could not. */
  const copySnapshotLink = useCallback(async (): Promise<string | null> => {
    const targetAdapter = adapterRef.current;
    if (!targetAdapter) return null;
    setSnapshotLinkStatus('copying');
    const link = await copyChartSnapshotLink(targetAdapter.snapshotDataUrl(), (image) => tradingApi.createSnapshot(image, instrumentId, interval))
      .catch(() => null);
    setSnapshotLinkStatus(link ? 'copied' : 'error');
    if (copyStatusTimerRef.current) clearTimeout(copyStatusTimerRef.current);
    copyStatusTimerRef.current = setTimeout(() => setSnapshotLinkStatus('idle'), 2_500);
    return link;
  }, [adapterRef, instrumentId, interval]);

  /** Copies a PNG of the chart to the clipboard; resolves to whether it was copied. */
  const copyChartImage = useCallback(async (): Promise<boolean> => {
    const targetAdapter = adapterRef.current;
    if (!targetAdapter) return false;
    setChartImageCopyStatus('copying');
    const copied = await copyImageDataUrlToClipboard(targetAdapter.snapshotDataUrl()).then(() => true, () => false);
    setChartImageCopyStatus(copied ? 'copied' : 'error');
    if (copyStatusTimerRef.current) clearTimeout(copyStatusTimerRef.current);
    copyStatusTimerRef.current = setTimeout(() => setChartImageCopyStatus('idle'), 2_000);
    return copied;
  }, [adapterRef]);

  return { chartImageCopyStatus, copyChartImage, snapshotLinkStatus, copySnapshotLink };
}

/** Chart templates: a chart's style and indicators, saved with the indicator-preset documents. */
function useChartTemplates(ws: WorkflowInput) {
  const { chartId, chartSettings, chartType, indicators } = ws;
  const updateChart = useTradingStore((state) => state.updateChart);
  const setIndicators = useTradingStore((state) => state.setIndicators);
  const { data: templateRecords, refetch: refetchTemplates } = useQuery({
    queryKey: ['trading', 'indicator-presets'],
    queryFn: () => tradingApi.documents('indicator-presets'),
    enabled: false,
    staleTime: 30_000,
  });
  const chartTemplates: ChartTemplate[] = (templateRecords ?? []).flatMap((record) => {
    const template = parseChartTemplate(record);
    return template ? [template] : [];
  });
  const [chartTemplateStatus, setChartTemplateStatus] = useState<'idle' | 'saving' | 'error'>('idle');

  const loadChartTemplates = useCallback(() => { void refetchTemplates(); }, [refetchTemplates]);

  /** Saves this chart's style and indicators, without its symbol or interval, as a template. */
  const saveChartTemplate = useCallback(async (name: string): Promise<boolean> => {
    setChartTemplateStatus('saving');
    try {
      const payload = chartTemplatePayload({ name, chartType, settings: chartSettings, indicators });
      await tradingApi.createDocument('indicator-presets', chartTemplateRecordId(name), payload);
      setChartTemplateStatus('idle');
      void refetchTemplates();
      return true;
    } catch {
      setChartTemplateStatus('error');
      return false;
    }
  }, [chartSettings, chartType, indicators, refetchTemplates]);

  /** Applies a template's chart type, settings and indicators; the symbol and interval stay. */
  const applyChartTemplateRecord = useCallback((template: ChartTemplate) => {
    updateChart(chartId, { chartType: template.chartType, settings: { ...template.settings } });
    setIndicators(chartId, template.indicators);
  }, [chartId, setIndicators, updateChart]);

  const deleteChartTemplate = useCallback(async (template: ChartTemplate) => {
    const record = templateRecords?.find((item) => item.record_id === template.recordId);
    if (!record) return;
    try {
      await tradingApi.archiveDocument('indicator-presets', record);
      void refetchTemplates();
    } catch {
      setChartTemplateStatus('error');
    }
  }, [refetchTemplates, templateRecords]);

  return { chartTemplates, chartTemplateStatus, loadChartTemplates, saveChartTemplate, applyChartTemplateRecord, deleteChartTemplate };
}

/** Market session (from the server's exchange calendar) and data delay for the legend. */
function useChartMarketStatus(ws: WorkflowInput) {
  const { instrumentId, provenance, resolvedBinding } = ws;
  const { data } = useQuery({
    queryKey: ['trading', 'market-status', instrumentId],
    queryFn: () => tradingApi.marketStatus(instrumentId),
    enabled: Boolean(instrumentId) && !isTradingFormulaInstrumentId(instrumentId),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
  });
  return {
    marketStatus: data ? marketStatusLabel(data.status, data.always_open) : null,
    marketStatusValue: data?.status ?? null,
    dataDelay: dataDelayLabel(resolvedBinding, provenance),
  };
}

/**
 * The chart workflow items of TVP-2.5 for one chart panel: chart settings, the pre/post-market
 * price line, go to date (with history loading), copy chart image, pane maximise/collapse by
 * double-click, chart templates, and market status / data delay for the legend.
 *
 * Keyboard commands (TVP-2.1–2.4) call `openGoToDate`, `goToDate` and `copyChartImage` from this
 * hook's results; it binds no keys itself.
 */
export function useChartWorkflow(ws: WorkflowInput) {
  const {
    active, adapter, adapterRef, chartFocusMode, chartId, chartQuery, chartSettings, drawingTool,
    fullscreenIndicator, fullscreenMainPane, hostRef, interval, paneIndicators, replayMode, showExtendedHours,
    toggleFullscreen, toggleFullscreenIndicator, toggleMinimizedIndicator,
  } = ws;
  const updateChart = useTradingStore((state) => state.updateChart);
  const settings: TradingChartSettings = chartSettings ?? {};
  const goTo = useChartGoToDate(ws);
  // Time sync (TVP-4.2): a bar clicked on another chart of the tab scrolls this one there (not in replay).
  useChartTimeSync(chartId, adapter, (timeMs) => (replayMode ? null : goTo.goToDate(timeMs)));

  const updateChartSettings = useCallback((patch: Partial<TradingChartSettings>) => {
    const current = useTradingStore.getState().charts.find((chart) => chart.chartId === chartId)?.settings ?? {};
    updateChart(chartId, { settings: { ...current, ...patch } });
  }, [chartId, updateChart]);

  const rawBars = (chartQuery.data?.bars ?? []) as MarketBar[];
  const barCountdownOn = barCountdownEnabled(settings, interval);

  const marketStatus = useChartMarketStatus(ws);

  // The pre/post-market price line follows the newest bar, whether or not extended bars are shown,
  // and never during the regular session.
  const extendedPriceLineEnabled = settings.extendedPriceLine !== false && !replayMode
    && marketStatus.marketStatusValue !== 'open';
  const extendedLine = extendedPriceLineEnabled ? extendedSessionPriceLine(rawBars) : null;
  const extendedLinePrice = extendedLine?.price ?? null;
  const extendedLineSession = extendedLine?.session ?? null;
  useEffect(() => {
    if (!adapter) return;
    adapter.setSessionPriceLine(extendedLinePrice === null || extendedLineSession === null ? null : {
      price: extendedLinePrice,
      title: extendedLineSession === 'pre' ? 'Pre-market' : 'Post-market',
      color: extendedLineSession === 'pre' ? chartPalette.cyan : chartPalette.amber,
    });
  }, [adapter, extendedLinePrice, extendedLineSession]);

  /** Double-click maximises the pane under the pointer; Ctrl/Cmd + double-click collapses an indicator pane. */
  const handleStageDoubleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const targetAdapter = adapterRef.current;
    const host = hostRef.current;
    if (!targetAdapter || !host || drawingTool !== 'cursor' || (replayMode && active)) return;
    if (!host.contains(event.target as Node)) return;
    // Double-clicking a price scale resets it (lightweight-charts); leave that alone.
    if (targetAdapter.isPriceScaleCoordinate(event.clientX - host.getBoundingClientRect().left)) return;
    const action = paneDoubleClickAction(
      targetAdapter.paneAtClientY(event.clientY),
      event.ctrlKey || event.metaKey,
      { indicatorPaneCount: paneIndicators.length, mainPaneFullscreen: fullscreenMainPane && chartFocusMode, fullscreenIndicator },
    );
    if (!action) return;
    event.preventDefault();
    if (action.type === 'toggle-indicator-fullscreen') toggleFullscreenIndicator(action.id);
    else if (action.type === 'toggle-indicator-minimized') toggleMinimizedIndicator(action.id);
    else toggleFullscreen();
  };

  return {
    updateChartSettings,
    extendedHoursAvailable: hasExtendedHoursBars(rawBars),
    sessionTaggedBars: hasSessionTaggedBars(rawBars),
    showExtendedHours,
    barCountdownOn,
    barCountdownVisible: barCountdownOn && !replayMode,
    barCountdownAvailable: tradingIntervalDurationMs(interval) !== null,
    extendedPriceLineEnabled,
    eventMarkersAvailable: isStockInstrument(ws.instrumentId),
    eventMarkersOn: settings.events !== false && isStockInstrument(ws.instrumentId),
    handleStageDoubleClick,
    ...goTo,
    ...useChartImageCopy(adapterRef, ws.instrumentId, interval),
    ...useChartTemplates(ws),
    ...marketStatus,
  };
}
