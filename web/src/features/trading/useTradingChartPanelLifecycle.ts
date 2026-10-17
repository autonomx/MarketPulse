import { APPEARANCE_CHANGE_EVENT, onOmnixEvent, TRADING_CHART_TIMEZONE_CHANGE_EVENT } from '../../events/bus';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { defaultTradingPriceScaleMenuState } from './TradingPriceScaleMenu';
import { TradingChartAdapter } from './chart/chartAdapter';
import { TradingIndicatorScheduler } from './indicators/indicatorScheduler';
import { resolveTradingTimezone, TRADING_TIMEZONE_OPTIONS } from './tradingTime';
import { TradingChartPanelProps, Y_AXIS_DRAG_ZOOM_SENSITIVITY, loadCompareSymbolBars } from './tradingChartPanelModel';
import type { useChartPanelState } from './useTradingChartPanelState';
import type { useChartIndicatorScheduling } from './useTradingChartPanelData';
import type { useChartPanelData } from './useTradingChartPanelData';

/** How far a press may move and still be a click (time sync, TVP-4.2). */
const CLICK_SLOP_PX = 4;

type ChartPan = {
  pointerId: number; startX: number; startY: number; lastX: number; lastY: number; paneY: number; paneId: string | null;
  mode: 'chart-pan' | 'price-scale' | 'price-pan';
};

/** A chart press that ended without dragging: a click, which taking the press kept from the chart's own click event. */
function isChartClick(pan: ChartPan, event: PointerEvent): boolean {
  return event.type === 'pointerup' && pan.mode === 'chart-pan'
    && Math.abs(event.clientX - pan.startX) < CLICK_SLOP_PX && Math.abs(event.clientY - pan.startY) < CLICK_SLOP_PX;
}

/** The chart adapter: creation, streaming, appearance and viewport. */
export function useChartLifecycle(ws: TradingChartPanelProps & ReturnType<typeof useChartPanelState> & ReturnType<typeof useChartIndicatorScheduling> & ReturnType<typeof useChartPanelData>) {
  const {
    active, adapter, adapterRef, chartId, chartQuery, chartType, customRangeOpen, customRangeRef, drawingTool,
    drawings, fittedBarsKeyRef, fullscreenIndicatorRef, fullscreenMainPaneRef, hostRef, indicatorSchedulerRef,
    indicatorTimerRef, interval, pendingIntervalScrollRef, pendingRangeIntervalRef, previousIntervalRef, replayMode,
    selectedRangeRef, setAdapter, setChartPanning, setContextMenu, setCustomRangeOpen, setFullscreenIndicator,
    setFullscreenMainPane, setIndicatorOutputs, setIndicatorPaneGeometry, setPanningIndicatorPane,
    setPriceScaleMenuOpen, setPriceScaleSettings, setSelectedIndicator, setSelectedRangeLabel, setTimezoneId,
    setTimezoneMenuOpen, showExtendedHours, streamDataKeyRef, streamRevisionRef, synchronization, timezoneId, timezoneMenuOpen,
    timezoneMenuRef,
  } = ws;
  const queryClient = useQueryClient();
  // Compare-symbol indicators read the compare symbol's bars with the chart's extended-hours setting.
  const extendedHoursRef = useRef(showExtendedHours);
  useEffect(() => {
    extendedHoursRef.current = showExtendedHours;
  }, [showExtendedHours]);

  useEffect(() => {
    if (!hostRef.current) return;
    const next = new TradingChartAdapter(hostRef.current, chartType);
    const scheduler = new TradingIndicatorScheduler(undefined, (id, barInterval, range) => (
      loadCompareSymbolBars(queryClient, id, barInterval, range, Date.now(), extendedHoursRef.current)
    ));
    adapterRef.current = next;
    indicatorSchedulerRef.current = scheduler;
    fittedBarsKeyRef.current = null;
    streamDataKeyRef.current = null;
    streamRevisionRef.current = 1;
    selectedRangeRef.current = undefined;
    pendingRangeIntervalRef.current = null;
    setSelectedRangeLabel('All');
    setPriceScaleMenuOpen(false);
    setPriceScaleSettings(defaultTradingPriceScaleMenuState);
    fullscreenIndicatorRef.current = null;
    setFullscreenIndicator(null);
    fullscreenMainPaneRef.current = false;
    setFullscreenMainPane(false);
    setAdapter(next);
    setIndicatorOutputs([]);
    setIndicatorPaneGeometry([]);
    const unregister = synchronization.register(chartId, next);
    return () => {
      unregister();
      if (indicatorTimerRef.current) clearTimeout(indicatorTimerRef.current);
      indicatorTimerRef.current = null;
      scheduler.destroy();
      next.destroy();
      indicatorSchedulerRef.current = null;
      adapterRef.current = null;
      setAdapter(null);
      setIndicatorOutputs([]);
    };
    // Creates the chart once per chart and synchronization group; it reads later values through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartId, synchronization, queryClient, adapterRef, fittedBarsKeyRef, fullscreenIndicatorRef, fullscreenMainPaneRef, hostRef, indicatorSchedulerRef, indicatorTimerRef, pendingRangeIntervalRef, selectedRangeRef, setAdapter, setFullscreenIndicator, setFullscreenMainPane, setIndicatorOutputs, setIndicatorPaneGeometry, setPriceScaleMenuOpen, setPriceScaleSettings, setSelectedRangeLabel, streamDataKeyRef, streamRevisionRef]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !adapter) return;
    let pan: ChartPan | null = null;
    const insideHost = (event: PointerEvent) => event.target instanceof Node && host.contains(event.target);
    const pointerDown = (event: PointerEvent) => {
      const target = event.target;
      const element = target instanceof Element ? target : null;
      if (!element?.closest('.trading-chart-context-menu')) setContextMenu(null);
      if (!element?.closest('.trading-indicator-object-toolbar')) setSelectedIndicator(null);
      if (!element?.closest('[data-drawing-id], .trading-drawing-manager, .trading-chart-context-menu')) drawings.select(null);
      if (!insideHost(event)) return;
      const isPrimaryPan = event.button === 0 && drawingTool === 'cursor';
      const isMiddlePan = event.button === 1;
      if ((!isPrimaryPan && !isMiddlePan) || event.pointerType === 'touch') return;
      const bounds = host.getBoundingClientRect();
      const x = event.clientX - bounds.left;
      const paneY = event.clientY - bounds.top;
      const onPriceScale = adapter.isPriceScaleCoordinate(x);
      // Replay owns the chart canvas for bar selection/playback, but the
      // price scale remains an independent viewport control. Keep its
      // TradingView-style drag zoom available while replay is active.
      if (replayMode && active && !onPriceScale) return;
      pan = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        paneY,
        paneId: onPriceScale ? null : adapter.indicatorPaneIdAtCoordinate(paneY),
        mode: onPriceScale ? 'price-scale' : event.shiftKey ? 'price-pan' : 'chart-pan',
      };
      setChartPanning(!onPriceScale);
      setPanningIndicatorPane(pan.paneId);
      host.setPointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    };
    const pointerMove = (event: PointerEvent) => {
      if (!pan || pan.pointerId !== event.pointerId) return;
      const deltaX = event.clientX - pan.lastX;
      const deltaY = event.clientY - pan.lastY;
      const bounds = host.getBoundingClientRect();
      if (pan.mode === 'price-scale') adapter.zoomPriceScaleAtCoordinate(event.clientY - bounds.top, deltaY * Y_AXIS_DRAG_ZOOM_SENSITIVITY);
      else if (pan.mode === 'price-pan') adapter.panPriceScaleByPixelsAtCoordinate(pan.paneY, -deltaY);
      else {
        // TradingView-style chart dragging: horizontal motion pans time and
        // vertical motion translates the visible price range at the same time.
        adapter.panTimeByPixels(deltaX);
        // Invert the screen delta so dragging upward moves the visible chart
        // downward, matching the requested TradingView-style y-axis feel.
        adapter.panPriceScaleByPixelsAtCoordinate(pan.paneY, -deltaY);
      }
      pan.lastX = event.clientX;
      pan.lastY = event.clientY;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    };
    const pointerUp = (event: PointerEvent) => {
      if (!pan || pan.pointerId !== event.pointerId) return;
      if (isChartClick(pan, event)) adapter.clickAt(pan.startX - host.getBoundingClientRect().left);
      pan = null;
      setChartPanning(false);
      setPanningIndicatorPane(null);
      if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
    };
    const lostPointerCapture = () => {
      pan = null;
      setChartPanning(false);
      setPanningIndicatorPane(null);
    };
    window.addEventListener('pointerdown', pointerDown, true);
    window.addEventListener('pointermove', pointerMove, true);
    window.addEventListener('pointerup', pointerUp, true);
    window.addEventListener('pointercancel', pointerUp, true);
    host.addEventListener('lostpointercapture', lostPointerCapture, true);
    return () => {
      window.removeEventListener('pointerdown', pointerDown, true);
      window.removeEventListener('pointermove', pointerMove, true);
      window.removeEventListener('pointerup', pointerUp, true);
      window.removeEventListener('pointercancel', pointerUp, true);
      host.removeEventListener('lostpointercapture', lostPointerCapture, true);
      pan = null;
      setChartPanning(false);
      setPanningIndicatorPane(null);
    };
    // Rebinds the pan handlers only when panning can change; the handlers read the rest through refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, adapter, drawingTool, replayMode, hostRef, setChartPanning, setContextMenu, setPanningIndicatorPane, setSelectedIndicator]);

  useEffect(() => {
    if (!adapter) return;
    const applyAppearance = () => {
      adapter.setAppearance(document.documentElement.dataset.omnixAppearance === 'light' ? 'light' : 'dark');
    };
    applyAppearance();
    window.addEventListener(APPEARANCE_CHANGE_EVENT, applyAppearance);
    return () => window.removeEventListener(APPEARANCE_CHANGE_EVENT, applyAppearance);
  }, [adapter]);

  useEffect(() => {
    adapter?.setTimezone(resolveTradingTimezone(timezoneId, chartQuery.data?.instrument.exchange_timezone));
  }, [adapter, chartQuery.data?.instrument.exchange_timezone, timezoneId]);

  useEffect(() => {
    if (!customRangeOpen && !timezoneMenuOpen) return;
    const closeMenus = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (customRangeOpen && !customRangeRef.current?.contains(target)) setCustomRangeOpen(false);
      if (timezoneMenuOpen && !timezoneMenuRef.current?.contains(target)) setTimezoneMenuOpen(false);
    };
    document.addEventListener('pointerdown', closeMenus, true);
    return () => document.removeEventListener('pointerdown', closeMenus, true);
  }, [customRangeOpen, timezoneMenuOpen, customRangeRef, setCustomRangeOpen, setTimezoneMenuOpen, timezoneMenuRef]);

  useEffect(() => onOmnixEvent(TRADING_CHART_TIMEZONE_CHANGE_EVENT, (timezone) => {
    if (TRADING_TIMEZONE_OPTIONS.some((option) => option.id === timezone)) setTimezoneId(timezone);
  }), [setTimezoneId]);

  useEffect(() => {
    if (previousIntervalRef.current === interval) return;
    previousIntervalRef.current = interval;
    pendingIntervalScrollRef.current = true;
  }, [interval, pendingIntervalScrollRef, previousIntervalRef]);

  return {

  };
}
