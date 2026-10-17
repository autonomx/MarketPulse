import { useEffect } from 'react';
import { DEFAULT_TRADING_RIGHT_OFFSET } from './chart/chartAdapter';
import { type ChartAlertPlacement } from './drawings/TradingDrawingOverlay';
import { type CoreIndicatorId } from './indicators/coreIndicators';
import { isIntervalAvailable } from './tradingIntervals';
import { dateInputValue, tradingDateRangeWithinLoadedHistory, zonedDateTimeToUtc } from './tradingTime';
import { CustomVisibleRange, TradingChartPanelProps, applyVisibleRange, closestSupportedInterval, isAlertIndicatorId, rightOffsetOptions, rightOffsetStorageKey } from './tradingChartPanelModel';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling } from './useTradingChartPanelData';
import type { useChartPanelData } from './useTradingChartPanelData';
import type { useChartLifecycle } from './useTradingChartPanelLifecycle';
import type { useChartSync } from './useTradingChartPanelSync';
import type { useChartView } from './useTradingChartPanelView';

/** Indicator pane actions: fullscreen, minimize, close and resize. */
export function useChartIndicatorActions(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData> & ReturnType<typeof useChartLifecycle> & ReturnType<typeof useChartSync> & ReturnType<typeof useChartView>) {
  const {
    adapterRef, chartFocusMode, fullscreenIndicatorRef, fullscreenMainPaneRef, indicatorResizeRef,
    minimizedIndicatorsRef, onChartFocusChange, onToggleIndicator, refreshIndicatorPanes, setFullscreenIndicator,
    setFullscreenMainPane, setHoveredIndicatorPane, setMinimizedIndicators, setResizingIndicatorPane,
    setSettingsIndicator,
  } = ws;

  const toggleFullscreen = () => {
    const nextFocused = !chartFocusMode;
    fullscreenIndicatorRef.current = null;
    setFullscreenIndicator(null);
    adapterRef.current?.setIndicatorPaneFullscreen(null);
    fullscreenMainPaneRef.current = nextFocused;
    setFullscreenMainPane(nextFocused);
    if (nextFocused) adapterRef.current?.setMainPaneFullscreen(true);
    else adapterRef.current?.setMainPaneFullscreen(false);
    onChartFocusChange(nextFocused);
  };

  const toggleMinimizedIndicator = (id: CoreIndicatorId) => {
    if (fullscreenIndicatorRef.current === id) return;
    setMinimizedIndicators((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      minimizedIndicatorsRef.current = next;
      return next;
    });
  };

  const resetIndicatorPaneView = (id: CoreIndicatorId) => {
    // Resetting a price scale causes Lightweight Charts to reflow the pane.
    // Clear the hover latch at the same time so the controls remain hover-only
    // after that reflow.
    setHoveredIndicatorPane(null);
    adapterRef.current?.resetIndicatorPaneView(id);
  };

  const toggleFullscreenIndicator = (id: CoreIndicatorId) => {
    const next = fullscreenIndicatorRef.current === id ? null : id;
    fullscreenIndicatorRef.current = next;
    setFullscreenIndicator(next);
    if (next) {
      fullscreenMainPaneRef.current = false;
      setFullscreenMainPane(false);
      adapterRef.current?.setMainPaneFullscreen(false);
      setMinimizedIndicators((current) => {
        const updated = new Set(current);
        updated.delete(id);
        minimizedIndicatorsRef.current = updated;
        return updated;
      });
      adapterRef.current?.setIndicatorPaneFullscreen(next);
      onChartFocusChange(true);
      return;
    }
    fullscreenMainPaneRef.current = false;
    setFullscreenMainPane(false);
    adapterRef.current?.setIndicatorPaneFullscreen(null);
    onChartFocusChange(false);
  };

  const closeIndicator = (id: CoreIndicatorId) => {
    if (fullscreenIndicatorRef.current === id) {
      fullscreenIndicatorRef.current = null;
      setFullscreenIndicator(null);
      adapterRef.current?.setIndicatorPaneFullscreen(null);
      onChartFocusChange(false);
    }
    setSettingsIndicator((current) => current?.id === id ? null : current);
    setMinimizedIndicators((current) => {
      const next = new Set(current);
      next.delete(id);
      minimizedIndicatorsRef.current = next;
      return next;
    });
    onToggleIndicator(id);
  };

  const startIndicatorPaneResize = (event: React.PointerEvent<HTMLDivElement>, id: CoreIndicatorId, edge: 'top' | 'bottom') => {
    if (event.button !== 0 || event.pointerType === 'touch' || fullscreenIndicatorRef.current !== null) return;
    indicatorResizeRef.current = { id, edge, pointerId: event.pointerId, lastY: event.clientY, target: event.currentTarget };
    setResizingIndicatorPane(id);
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  };

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const resize = indicatorResizeRef.current;
      if (!resize || resize.pointerId !== event.pointerId) return;
      const deltaY = event.clientY - resize.lastY;
      resize.lastY = event.clientY;
      adapterRef.current?.resizeIndicatorPaneByPixels(resize.id, resize.edge, deltaY);
      refreshIndicatorPanes(adapterRef.current);
      event.preventDefault();
    };
    const finish = (event: PointerEvent) => {
      const resize = indicatorResizeRef.current;
      if (!resize || resize.pointerId !== event.pointerId) return;
      if (resize.target.hasPointerCapture(event.pointerId)) resize.target.releasePointerCapture(event.pointerId);
      indicatorResizeRef.current = null;
      setResizingIndicatorPane(null);
      event.preventDefault();
    };
    window.addEventListener('pointermove', move, { passive: false });
    window.addEventListener('pointerup', finish, { passive: false });
    window.addEventListener('pointercancel', finish, { passive: false });
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', finish);
      window.removeEventListener('pointercancel', finish);
    };
  }, [refreshIndicatorPanes, adapterRef, indicatorResizeRef, setResizingIndicatorPane]);

  return {
    toggleFullscreen, toggleMinimizedIndicator, resetIndicatorPaneView, toggleFullscreenIndicator, closeIndicator,
    startIndicatorPaneResize,
  };
}

/** Visible-range, context-menu and pointer actions. */
export function useChartRangeActions(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData> & ReturnType<typeof useChartLifecycle> & ReturnType<typeof useChartSync> & ReturnType<typeof useChartView> & ReturnType<typeof useChartIndicatorActions>) {
  const {
    active, adapterRef, allBarsRef, barsRef, chartQuery, contextMenu, customRangeEnd, customRangeStart, drawingTool,
    drawings, hostRef, indicatorPaneGeometry, indicators, interval, onActivate, onChangeInterval, onClearIndicators,
    onToggleIndicator, paneIndicators, pendingRangeIntervalRef, replayChoosingStart, rightOffset,
    selectedRangeRef, selectedTimezone, setAlertPlacement, setContextMenu, setCustomRangeEnd, setCustomRangeError,
    setCustomRangeOpen, setCustomRangeStart, setHoveredIndicatorPane, setPriceScaleHovered, setReplaySelectionIndex,
    setRightOffset, setSelectedIndicator, setSelectedRangeLabel, setTimezoneMenuOpen,
  } = ws;

  const changeRightOffset = (value: string | number) => {
    const parsed = Number(value);
    const next = rightOffsetOptions.includes(parsed as typeof rightOffsetOptions[number])
      ? parsed
      : DEFAULT_TRADING_RIGHT_OFFSET;
    setRightOffset(next);
    selectedRangeRef.current = null;
    setSelectedRangeLabel('All');
    adapterRef.current?.setRightOffset(next);
    adapterRef.current?.fitContent();
    try {
      window.localStorage.setItem(rightOffsetStorageKey, String(next));
    } catch {
      // The setting remains active for the current session when storage is unavailable.
    }
  };

  const openCustomRange = () => {
    const first = barsRef.current[0]?.start_time ?? new Date().toISOString();
    const last = barsRef.current.at(-1)?.end_time ?? new Date().toISOString();
    setCustomRangeStart((current) => current || dateInputValue(first, selectedTimezone));
    setCustomRangeEnd((current) => current || dateInputValue(last, selectedTimezone));
    setCustomRangeError(null);
    setCustomRangeOpen((current) => !current);
    setTimezoneMenuOpen(false);
  };

  const applyCustomRange = () => {
    const fromTime = zonedDateTimeToUtc(customRangeStart, selectedTimezone);
    const toTime = zonedDateTimeToUtc(customRangeEnd, selectedTimezone, true);
    if (fromTime === null || toTime === null) {
      setCustomRangeError('Choose both a start and end date.');
      return;
    }
    if (fromTime > toTime) {
      setCustomRangeError('The start date must be before the end date.');
      return;
    }
    const firstLoaded = barsRef.current[0]?.start_time;
    const lastLoaded = barsRef.current.at(-1)?.end_time ?? barsRef.current.at(-1)?.start_time;
    if (!firstLoaded || !lastLoaded) {
      setCustomRangeError('No chart history is loaded for this range.');
      return;
    }
    if (!tradingDateRangeWithinLoadedHistory(
      customRangeStart,
      customRangeEnd,
      firstLoaded,
      lastLoaded,
      selectedTimezone,
    )) {
      setCustomRangeError(
        `Requested dates are outside loaded history (${dateInputValue(firstLoaded, selectedTimezone)} to ${dateInputValue(lastLoaded, selectedTimezone)}).`,
      );
      return;
    }
    const selection = { from: customRangeStart, to: customRangeEnd } satisfies CustomVisibleRange;
    selectedRangeRef.current = selection;
    setSelectedRangeLabel('Custom');
    setCustomRangeError(null);
    setCustomRangeOpen(false);
    const chart = adapterRef.current?.api();
    if (chart) applyVisibleRange(chart, selection, barsRef.current, interval, rightOffset, selectedTimezone);
  };

  const showRange = (label: string, days: number | null, requestedInterval: string) => {
    selectedRangeRef.current = days;
    setSelectedRangeLabel(label);
    const supportedIntervals = chartQuery.data?.binding.supported_intervals ?? [];
    const nextInterval = isIntervalAvailable(requestedInterval, supportedIntervals)
      ? requestedInterval
      : closestSupportedInterval(requestedInterval, supportedIntervals);
    pendingRangeIntervalRef.current = interval === nextInterval ? null : nextInterval;
    onChangeInterval(nextInterval);
    const chart = adapterRef.current?.api();
    if (!chart) return;
    if (interval === nextInterval) applyVisibleRange(chart, days, barsRef.current, interval, rightOffset, selectedTimezone);
  };

  const openContextMenu = (point: ChartAlertPlacement, indicatorId?: CoreIndicatorId) => {
    if (!active) onActivate();
    setSelectedIndicator(null);
    const resolvedIndicatorId = indicatorId;
    const resolvedIndicator = resolvedIndicatorId
      ? indicators.find((indicator) => indicator.id === resolvedIndicatorId && indicator.enabled)
      : undefined;
    const alertIndicatorId = resolvedIndicatorId && isAlertIndicatorId(resolvedIndicatorId)
      ? resolvedIndicatorId
      : undefined;
    const stage = hostRef.current?.parentElement;
    const width = stage?.clientWidth ?? 0;
    const height = stage?.clientHeight ?? 0;
    setContextMenu({
      ...point,
      contextIndicatorId: resolvedIndicatorId,
      indicatorId: alertIndicatorId,
      chartIndicatorId: resolvedIndicatorId,
      indicatorPeriod: resolvedIndicator?.period,
      x: Math.max(6, Math.min(point.x, Math.max(6, width - 286))),
      y: Math.max(6, Math.min(point.y, Math.max(6, height - 420))),
    });
  };

  const handleStageContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    const target = event.target as Element;
    if (target.closest('.trading-chart-context-menu, .trading-price-scale-menu, .trading-price-scale-trigger, .trading-chart-alert-editor, .trading-chart-table-view, .trading-chart-object-tree, .trading-chart-settings, .trading-indicator-pane-controls, .trading-indicator-object-toolbar')) return;
    const stage = event.currentTarget;
    const bounds = stage.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    const indicatorId = adapterRef.current?.indicatorPaneIdAtClientY(event.clientY);
    const indicator = indicatorId === null || indicatorId === undefined
      ? undefined
      : paneIndicators.find((item) => item.id === indicatorId);
    if (indicator) {
      const point = adapterRef.current?.drawingPointFromCoordinate(x, y);
      const indicatorValue = adapterRef.current?.indicatorValueFromClientY(indicator.id, event.clientY);
      if (point) openContextMenu({ ...point, price: indicatorValue ?? point.price, x, y, source: 'context-menu' }, indicator.id);
      return;
    }
    const point = adapterRef.current?.drawingPointFromCoordinate(x, y);
    if (point) openContextMenu({ ...point, x, y, source: 'context-menu' });
  };

  const handleStagePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const targetAdapter = adapterRef.current;
    const target = event.target as Element;
    // Drawing and alert tools own the chart pointer. Do not let transient pane
    // hover chrome appear underneath that interaction and intercept placement.
    if (drawingTool !== 'cursor') {
      setHoveredIndicatorPane(null);
    } else if (targetAdapter) {
      const bounds = event.currentTarget.getBoundingClientRect();
      const y = event.clientY - bounds.top;
      const hoveredPane = indicatorPaneGeometry.find((pane) => y >= pane.top && y <= pane.top + pane.height);
      const nextHoveredPane = hoveredPane ? hoveredPane.id as CoreIndicatorId : null;
      setHoveredIndicatorPane((current) => current === nextHoveredPane ? current : nextHoveredPane);
    } else {
      setHoveredIndicatorPane(null);
    }
    if (target.closest('.trading-y-axis-controls')) {
      setPriceScaleHovered(true);
      return;
    }
    if (!targetAdapter) {
      setPriceScaleHovered(false);
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    if (replayChoosingStart && !target.closest('button, input, select, textarea, [role="dialog"]')) {
      const x = event.clientX - bounds.left;
      if (!targetAdapter.isPriceScaleCoordinate(x)) {
        const index = targetAdapter.barIndexAtCoordinate(x, allBarsRef.current.length);
        if (index !== null) setReplaySelectionIndex(index);
      }
    }
    const nextHovered = targetAdapter.isMainPriceScaleCoordinate(event.clientX - bounds.left);
    setPriceScaleHovered((current) => current === nextHovered ? current : nextHovered);
  };

  const handleStagePointerLeave = () => {
    setPriceScaleHovered(false);
    setHoveredIndicatorPane(null);
  };

  const copyContextPrice = () => {
    if (!contextMenu) return;
    void navigator.clipboard?.writeText(String(contextMenu.price));
  };

  const pasteContextPrice = () => {
    if (!contextMenu) return;
    void navigator.clipboard?.readText().then((value) => {
      const pasted = Number(value.trim());
      if (Number.isFinite(pasted)) setAlertPlacement({ ...contextMenu, price: pasted, source: 'context-menu' });
    }).catch(() => undefined);
  };

  const applyChartTemplate = (template: 'default' | 'clean' | 'momentum') => {
    if (template === 'clean') {
      onClearIndicators();
      drawings.removeAll();
      return;
    }
    if (template === 'momentum') {
      for (const id of ['rsi', 'macd'] as CoreIndicatorId[]) {
        if (!indicators.some((indicator) => indicator.id === id && indicator.enabled)) onToggleIndicator(id);
      }
    }
  };

  const contextMenuAlert = () => {
    // Any pane's indicator can be alerted on from the dialog's chart indicators (TVP-1.3).
    if (contextMenu) setAlertPlacement(contextMenu);
  };

  return {
    changeRightOffset, openCustomRange, applyCustomRange, showRange, openContextMenu, handleStageContextMenu,
    handleStagePointerMove, handleStagePointerLeave, copyContextPrice, pasteContextPrice, applyChartTemplate,
    contextMenuAlert,
  };
}
