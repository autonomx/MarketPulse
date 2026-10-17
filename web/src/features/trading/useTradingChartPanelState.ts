import { useEffect, useRef, useState } from 'react';
import { defaultTradingPriceScaleMenuState, type TradingPriceScaleMenuState } from './TradingPriceScaleMenu';
import { TradingChartAdapter, type TradingIndicatorPaneGeometry, type TradingIndicatorSelection } from './chart/chartAdapter';
import { type ChartAlertPlacement } from './drawings/TradingDrawingOverlay';
import { useTradingDrawings } from './drawings/useTradingDrawings';
import { type CoreIndicatorId, type CoreIndicatorInstance, type IndicatorOutput } from './indicators/coreIndicators';
import { TradingIndicatorScheduler } from './indicators/indicatorScheduler';
import { UTC_SESSION, type TradingSessionSpec } from './indicators/tradingSessions';
import { type TradingStreamStatus } from './streaming/tradingStreamHub';
import { useTradingStore } from './tradingStore';
import { drawingScopeId } from './drawings/drawingToolSettings';
import { drawingVisibleOnInterval } from './drawings/drawingVisibility';
import type { MarketBar } from './tradingTypes';
import { readTradingTimezoneId } from './tradingTime';
import { SelectedVisibleRange, TradingChartPanelProps, TradingContextMenuState, readTradingRightOffset } from './tradingChartPanelModel';

/** The panel's refs, store selections and UI state. */
export function useChartPanelState(ws: TradingChartPanelProps) {
  const { chartId, indicators, instrumentId, interval, onActivate, sessionId } = ws;

  const hostRef = useRef<HTMLDivElement | null>(null);

  const panelRef = useRef<HTMLElement | null>(null);

  const adapterRef = useRef<TradingChartAdapter | null>(null);

  const onActivateRef = useRef(onActivate);

  const barsRef = useRef<MarketBar[]>([]);

  const allBarsRef = useRef<MarketBar[]>([]);

  const replayWasVisibleRef = useRef(false);

  const fittedBarsKeyRef = useRef<string | null>(null);

  const streamDataKeyRef = useRef<string | null>(null);

  const streamRevisionRef = useRef(1);

  const previousIntervalRef = useRef(interval);

  const pendingIntervalScrollRef = useRef(false);

  const [, forceLiveRender] = useState(0);

  const selectedRangeRef = useRef<SelectedVisibleRange | undefined>(undefined);

  const pendingRangeIntervalRef = useRef<string | null>(null);

  const indicatorsRef = useRef<CoreIndicatorInstance[]>(indicators);

  const indicatorSchedulerRef = useRef<TradingIndicatorScheduler | null>(null);

  // The chart instrument's session calendar, for session-aware indicators.
  const indicatorSessionRef = useRef<TradingSessionSpec>(UTC_SESSION);
  /** The feed binding the chart's bars come from, once loaded; intrabar indicators read the same feed. */
  const indicatorBindingRef = useRef<string | null>(null);

  const indicatorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const drawingTool = useTradingStore((state) => state.drawingTool);

  const setDrawingTool = useTradingStore((state) => state.setDrawingTool);

  const drawingSnapMode = useTradingStore((state) => state.drawingSnapMode);
  const drawingsHidden = useTradingStore((state) => state.drawingsHidden);
  const drawingToolSettings = useTradingStore((state) => state.drawingToolSettings);
  const toggleDrawingsHidden = useTradingStore((state) => state.toggleDrawingsHidden);

  const replayMode = useTradingStore((state) => state.replayMode);

  const replaySessionId = useTradingStore((state) => state.replaySessionId);

  const setReplayMode = useTradingStore((state) => state.setReplayMode);

  const restartReplaySession = useTradingStore((state) => state.restartReplaySession);

  const drawings = useTradingDrawings(instrumentId, drawingScopeId(sessionId, chartId, drawingToolSettings.syncDrawings));

  const selectedDrawing = drawings.state.drawings.find((drawing) => drawing.drawingId === drawings.state.selectedId) ?? null;
  // A selected drawing that this interval doesn't show is deselected, so keys and the header never act on it (TVP-3.8).
  const selectedHiddenHere = selectedDrawing !== null && !drawingVisibleOnInterval(selectedDrawing.visibility, interval);
  useEffect(() => {
    if (selectedHiddenHere) drawings.select(null);
  }, [selectedHiddenHere, drawings]);

  const [adapter, setAdapter] = useState<TradingChartAdapter | null>(null);

  const [streamStatus, setStreamStatus] = useState<TradingStreamStatus | 'replay'>('connecting');

  const [streamError, setStreamError] = useState<string | null>(null);

  const [indicatorError, setIndicatorError] = useState<string | null>(null);

  const [alertPlacement, setAlertPlacement] = useState<ChartAlertPlacement | null>(null);

  const [contextMenu, setContextMenu] = useState<TradingContextMenuState | null>(null);

  const [priceScaleMenuOpen, setPriceScaleMenuOpen] = useState(false);

  const [priceScaleSettings, setPriceScaleSettings] = useState<TradingPriceScaleMenuState>(defaultTradingPriceScaleMenuState);

  const [priceScaleCurrency, setPriceScaleCurrency] = useState('USD');

  const [priceScaleHovered, setPriceScaleHovered] = useState(false);

  const [tableVisible, setTableVisible] = useState(false);

  const [objectTreeVisible, setObjectTreeVisible] = useState(false);

  const [settingsVisible, setSettingsVisible] = useState(false);

  const [cursorLocked, setCursorLocked] = useState(false);

  const [chartPanning, setChartPanning] = useState(false);

  const [panningIndicatorPane, setPanningIndicatorPane] = useState<string | null>(null);

  const [indicatorPaneGeometry, setIndicatorPaneGeometry] = useState<TradingIndicatorPaneGeometry[]>([]);

  const [indicatorOutputs, setIndicatorOutputs] = useState<IndicatorOutput[]>([]);

  const [indicatorLegendCollapsed, setIndicatorLegendCollapsed] = useState(false);

  const [compareDialogOpen, setCompareDialogOpen] = useState(false);

  const [hoveredIndicatorPane, setHoveredIndicatorPane] = useState<CoreIndicatorId | null>(null);

  const [resizingIndicatorPane, setResizingIndicatorPane] = useState<CoreIndicatorId | null>(null);

  const [settingsIndicator, setSettingsIndicator] = useState<CoreIndicatorInstance | null>(null);

  const [selectedIndicator, setSelectedIndicator] = useState<TradingIndicatorSelection | null>(null);

  const [selectedRangeLabel, setSelectedRangeLabel] = useState('All');

  const [customRangeOpen, setCustomRangeOpen] = useState(false);

  const [customRangeStart, setCustomRangeStart] = useState('');

  const [customRangeEnd, setCustomRangeEnd] = useState('');

  const [customRangeError, setCustomRangeError] = useState<string | null>(null);

  const [timezoneId, setTimezoneId] = useState(readTradingTimezoneId);

  const [timezoneMenuOpen, setTimezoneMenuOpen] = useState(false);

  const customRangeRef = useRef<HTMLDivElement | null>(null);

  const timezoneMenuRef = useRef<HTMLDivElement | null>(null);

  const [rightOffset, setRightOffset] = useState(readTradingRightOffset);

  const [replayMarkerX, setReplayMarkerX] = useState<number | null>(null);

  const [replaySelectionIndex, setReplaySelectionIndex] = useState<number | null>(null);

  const [replaySelectionX, setReplaySelectionX] = useState<number | null>(null);

  const [minimizedIndicators, setMinimizedIndicators] = useState<Set<CoreIndicatorId>>(() => new Set());

  const minimizedIndicatorsRef = useRef<Set<CoreIndicatorId>>(new Set());

  const [fullscreenIndicator, setFullscreenIndicator] = useState<CoreIndicatorId | null>(null);

  const fullscreenIndicatorRef = useRef<CoreIndicatorId | null>(null);

  const [fullscreenMainPane, setFullscreenMainPane] = useState(false);

  const fullscreenMainPaneRef = useRef(false);

  const indicatorResizeRef = useRef<{ id: CoreIndicatorId; edge: 'top' | 'bottom'; pointerId: number; lastY: number; target: HTMLDivElement } | null>(null);

  // TVP-2.5: per-chart display settings and a go-to-date request for more history.
  const chartSettings = useTradingStore((state) => state.charts.find((chart) => chart.chartId === chartId)?.settings);

  const [historyLimitOverride, setHistoryLimitOverride] = useState<{ key: string; limit: number } | null>(null);

  return {
    hostRef, panelRef, adapterRef, onActivateRef, barsRef, allBarsRef, replayWasVisibleRef, fittedBarsKeyRef,
    streamDataKeyRef, streamRevisionRef, previousIntervalRef, pendingIntervalScrollRef, forceLiveRender,
    selectedRangeRef, pendingRangeIntervalRef, indicatorsRef, indicatorSchedulerRef, indicatorSessionRef, indicatorBindingRef, indicatorTimerRef, drawingTool,
    setDrawingTool, drawingSnapMode, drawingsHidden, toggleDrawingsHidden, drawingToolSettings, replayMode, replaySessionId, setReplayMode, restartReplaySession, drawings,
    selectedDrawing, adapter, setAdapter, streamStatus, setStreamStatus, streamError,
    setStreamError, indicatorError, setIndicatorError, alertPlacement, setAlertPlacement, contextMenu,
    setContextMenu, priceScaleMenuOpen, setPriceScaleMenuOpen, priceScaleSettings, setPriceScaleSettings,
    priceScaleCurrency, setPriceScaleCurrency, priceScaleHovered, setPriceScaleHovered, tableVisible,
    setTableVisible, objectTreeVisible, setObjectTreeVisible, settingsVisible, setSettingsVisible, cursorLocked,
    setCursorLocked, chartPanning, setChartPanning, panningIndicatorPane, setPanningIndicatorPane,
    indicatorPaneGeometry, setIndicatorPaneGeometry, indicatorOutputs, setIndicatorOutputs, indicatorLegendCollapsed,
    setIndicatorLegendCollapsed, compareDialogOpen, setCompareDialogOpen, hoveredIndicatorPane,
    setHoveredIndicatorPane, resizingIndicatorPane, setResizingIndicatorPane, settingsIndicator,
    setSettingsIndicator, selectedIndicator, setSelectedIndicator, selectedRangeLabel, setSelectedRangeLabel,
    customRangeOpen, setCustomRangeOpen, customRangeStart, setCustomRangeStart, customRangeEnd, setCustomRangeEnd,
    customRangeError, setCustomRangeError, timezoneId, setTimezoneId, timezoneMenuOpen, setTimezoneMenuOpen,
    customRangeRef, timezoneMenuRef, rightOffset, setRightOffset, replayMarkerX, setReplayMarkerX,
    replaySelectionIndex, setReplaySelectionIndex, replaySelectionX,
    setReplaySelectionX, minimizedIndicators, setMinimizedIndicators, minimizedIndicatorsRef, fullscreenIndicator,
    setFullscreenIndicator, fullscreenIndicatorRef, fullscreenMainPane, setFullscreenMainPane, fullscreenMainPaneRef,
    indicatorResizeRef, chartSettings, historyLimitOverride, setHistoryLimitOverride,
  };
}
