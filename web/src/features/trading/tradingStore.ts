import { chartsWithGroup, groupSymbol, linkedSymbolChange, reconcileGroups, type ChartLinkGroup } from './chartLinkGroups';
import { loadDrawingToolSettings, saveDrawingToolSettings, type DrawingToolSettings } from './drawings/drawingToolSettings';
import { create } from 'zustand';
import type { TradingChartType } from './chart/chartAdapter';
import type { DrawingSnapMode, DrawingTool } from './drawings/drawingCommands';
import { indicatorUsesSeparatePane, type CoreIndicatorId, type CoreIndicatorInstance } from './indicators/coreIndicators';
import { isAutoChartPatternId } from './indicators/autoPatterns';
import {
  isTradingViewBuiltInId,
  tradingViewBuiltInDefaultPeriod,
  tradingViewBuiltInUsesSeparatePane,
} from './indicators/tradingViewBuiltIns';
import type { TradingComparison } from './tradingComparisons';
import { DEFAULT_FAVORITE_INTERVALS, MAX_FAVORITE_INTERVALS, sortTradingIntervals } from './tradingIntervals';

export type TradingLayout =
  | 'auto'
  | 'columns-1'
  | 'columns-2'
  | 'columns-3'
  | 'columns-4'
  | 'rows-2'
  | 'rows-3'
  | 'rows-4'
  | 'main-left-3'
  | 'main-right-3'
  | 'main-top-3'
  | 'main-bottom-3';
export const MIN_TRADING_CHARTS = 1;
export const MAX_TRADING_CHARTS = 16;
export const MAX_TRADING_TABS = 12;
/** Closed tabs that Reopen closed tab can bring back, newest last. Session only: never saved. */
export const MAX_CLOSED_TRADING_TABS = 10;

/** Per-chart display settings (TVP-2.5). An unset value takes its default. */
export type TradingChartSettings = {
  /** Countdown to bar close under the last-price label; defaults on for intraday intervals. */
  barCountdown?: boolean;
  /** Pre- and post-market bars; defaults to shown. */
  extendedHours?: boolean;
  /** A price line at the latest pre/post-market price; defaults on. */
  extendedPriceLine?: boolean;
  /** Earnings, dividend and split markers on stock charts (TVP-10.1); defaults on. */
  events?: boolean;
};

export type TradingChartState = {
  chartId: string;
  instrumentId: string;
  bindingId: string | null;
  interval: string;
  chartType: TradingChartType;
  indicators: CoreIndicatorInstance[];
  comparisons?: TradingComparison[];
  settings?: TradingChartSettings;
  /** The colour link group the chart shares its symbol with, across tabs (TVP-4.1); none when unset. */
  linkGroup?: ChartLinkGroup;
};
export type TradingIndicatorMove = 'up' | 'down';
export type TradingLinkState = {
  instrument: boolean;
  interval: boolean;
  crosshair: boolean;
  visibleRange: boolean;
  /** Clicking a bar scrolls the tab's other charts to its time (TVP-4.2). */
  time?: boolean;
};
export type TradingPanelState = {
  right: boolean;
  bottom: boolean;
};

export type TradingTabState = {
  tabId: string;
  name: string;
  layout: TradingLayout;
  activeChartId: string;
  charts: TradingChartState[];
  links: TradingLinkState;
  panels: TradingPanelState;
};

/** A closed tab and where it was, so reopening puts it back in place. */
export type ClosedTradingTab = { tab: TradingTabState; index: number };

type TradingWorkspaceState = {
  activeTabId: string;
  tabs: TradingTabState[];
  closedTabs: ClosedTradingTab[];
  layout: TradingLayout;
  activeChartId: string;
  replayMode: boolean;
  replaySessionId: number;
  drawingTool: DrawingTool;
  drawingSnapMode: DrawingSnapMode;
  /** Ctrl+Alt+H hides every drawing on every chart (TVP-2.2); they stay stored. */
  drawingsHidden: boolean;
  /** Drawing tool behaviour (TVP-3.8), kept in this browser: see `drawingToolSettings.ts`. */
  drawingToolSettings: DrawingToolSettings;
  charts: TradingChartState[];
  links: TradingLinkState;
  panels: TradingPanelState;
  favoriteInstrumentIds: string[];
  favoriteIntervals: string[];
  setLayout: (layout: TradingLayout) => void;
  setActiveTab: (tabId: string) => void;
  addTab: (name?: string) => string | null;
  /**
   * A copy of a tab, its charts and settings, placed after it (TVP-4.4); returns its id. `newTabId` names the copy
   * (its drawings are copied to that id first, see `duplicateTradingTab`).
   */
  duplicateTab: (tabId: string, newTabId?: string) => string | null;
  /** A new tab with one default chart (TVP-4.4); returns its id. */
  addBlankTab: () => string | null;
  renameTab: (tabId: string, name: string) => void;
  removeTab: (tabId?: string) => void;
  /** Reopens the most recently closed tab and returns its id, or null when there is none or no room. */
  reopenClosedTab: () => string | null;
  setActiveChart: (chartId: string) => void;
  setReplayMode: (enabled: boolean) => void;
  restartReplaySession: () => void;
  addChart: () => void;
  removeChart: (chartId?: string) => void;
  setChartCount: (count: number) => void;
  setDrawingTool: (tool: DrawingTool) => void;
  toggleDrawingsHidden: () => void;
  setDrawingToolSetting: <K extends keyof DrawingToolSettings>(key: K, value: DrawingToolSettings[K]) => void;
  setDrawingSnapMode: (mode: DrawingSnapMode) => void;
  /** Not the link group: `setChartLinkGroup` changes it, with its symbol. */
  updateChart: (chartId: string, patch: Partial<Omit<TradingChartState, 'chartId' | 'linkGroup'>>) => void;
  /** Puts a chart in a colour link group (it takes the group's symbol) or, with null, takes it out. */
  setChartLinkGroup: (chartId: string, group: ChartLinkGroup | null) => void;
  toggleIndicator: (chartId: string, id: CoreIndicatorId, period?: number) => void;
  toggleIndicatorVisibility: (chartId: string, id: CoreIndicatorId) => void;
  updateIndicator: (chartId: string, id: CoreIndicatorId, patch: Partial<CoreIndicatorInstance>) => void;
  moveIndicator: (chartId: string, id: CoreIndicatorId, direction: TradingIndicatorMove) => void;
  setIndicators: (chartId: string, indicators: CoreIndicatorInstance[]) => void;
  setLink: (key: keyof TradingLinkState, enabled: boolean) => void;
  setPanel: (key: keyof TradingPanelState, open: boolean) => void;
  toggleFavoriteInstrument: (instrumentId: string) => void;
  toggleFavoriteInterval: (interval: string) => void;
  addFavoriteInterval: (interval: string) => void;
};

const defaultInstrument = 'crypto:BINANCE:spot:BTC-USDT';
export const defaultTradingIndicators = (): CoreIndicatorInstance[] => [
  { id: 'sma', period: 20, enabled: true },
  { id: 'ema', period: 20, enabled: false },
  { id: 'rsi', period: 14, enabled: true },
  { id: 'macd', period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9, enabled: false },
  { id: 'bollinger', period: 20, standardDeviations: 2, enabled: false },
  { id: 'atr', period: 14, enabled: false },
  { id: 'vwap', period: 1, anchorTime: null, enabled: false },
];

function usesSeparatePane(id: CoreIndicatorId): boolean {
  return isTradingViewBuiltInId(id)
    ? tradingViewBuiltInUsesSeparatePane(id)
    : indicatorUsesSeparatePane(id);
}

/** A new instance with its default inputs (also the alert contract's defaults, TVP-1.3). */
export function newIndicatorInstance(id: CoreIndicatorId, period?: number): CoreIndicatorInstance {
  const defaults: CoreIndicatorInstance = isAutoChartPatternId(id)
    ? { id, period: 3, enabled: true, visible: true, style: { labelsOnPriceScale: false, valuesInStatusLine: false, inputsInStatusLine: false } }
    : isTradingViewBuiltInId(id)
      ? { id, period: tradingViewBuiltInDefaultPeriod(id) ?? 20, enabled: true, visible: true }
    : id === 'death-cross' || id === 'golden-cross'
    ? { id, period: 50, fastPeriod: 50, slowPeriod: 200, enabled: true, visible: true }
    : id === 'bull-market-band'
      ? { id, period: 20, fastPeriod: 20, slowPeriod: 21, enabled: true, visible: true }
    : id === 'ema-stack'
      ? { id, period: 9, enabled: true, visible: true }
      : id === 'fair-value-gap'
        ? { id, period: 3, enabled: true, visible: true }
        : id === 'ideal-bb'
          ? { id, period: 120, enabled: true, visible: true }
          : id === 'log-macd' || id === 'macd-dema'
            ? { id, period: 9, fastPeriod: 12, slowPeriod: 26, signalPeriod: 9, enabled: true, visible: true }
            : id === 'rsi-divergence'
              ? { id, period: 14, fastPeriod: 5, enabled: true, visible: true }
              : id === 'stochastic-rsi'
                ? { id, period: 14, fastPeriod: 3, signalPeriod: 3, enabled: true, visible: true }
                : id === 'swing-liquidity'
                  ? { id, period: 5, enabled: true, visible: true }
                  : id === 'volume-profile'
                    ? { id, period: 100, enabled: true, visible: true }
                    : { id, period: 20, enabled: true, visible: true };
  return period === undefined ? defaults : { ...defaults, period };
}

function initialChart(): TradingChartState {
  return {
    chartId: 'chart-1',
    instrumentId: defaultInstrument,
    bindingId: null,
    interval: '1h',
    chartType: 'candlestick',
    indicators: defaultTradingIndicators(),
    comparisons: [],
  };
}

function nextChartId(charts: readonly TradingChartState[]): string {
  let index = 1;
  const ids = new Set(charts.map((chart) => chart.chartId));
  while (ids.has(`chart-${index}`)) index += 1;
  return `chart-${index}`;
}

function copyChart(source: TradingChartState, chartId: string): TradingChartState {
  return {
    ...source,
    chartId,
    indicators: source.indicators.map((indicator) => ({ ...indicator })),
    comparisons: (source.comparisons ?? []).map((comparison) => ({ ...comparison })),
    ...(source.settings ? { settings: { ...source.settings } } : {}),
  };
}

function boundedChartCount(count: number): number {
  if (!Number.isFinite(count)) return MIN_TRADING_CHARTS;
  return Math.max(MIN_TRADING_CHARTS, Math.min(MAX_TRADING_CHARTS, Math.trunc(count)));
}

function initialSessionTab(): TradingTabState {
  const chart = initialChart();
  return {
    tabId: 'tab-1',
    name: 'Main Session',
    layout: 'auto',
    activeChartId: chart.chartId,
    charts: [chart],
    links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
    panels: { right: true, bottom: true },
  };
}

function copySessionCharts(source: readonly TradingChartState[], tabId: string): TradingChartState[] {
  return source.map((chart) => copyChart(chart, `${tabId}-${chart.chartId}`));
}

/**
 * The state with a new tab, built from its id, placed at `index` (default: last) and made active. Null when the
 * workspace has no room for another tab.
 */
function stateWithNewTab(
  state: TradingWorkspaceState,
  build: (tabId: string, tabs: readonly TradingTabState[]) => TradingTabState,
  index?: number,
  tabId?: string,
): Partial<TradingWorkspaceState> | null {
  if (state.tabs.length >= MAX_TRADING_TABS) return null;
  const current = state.tabs.find((tab) => tab.tabId === state.activeTabId);
  const tabs = current ? state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab) : state.tabs;
  const tab = build(tabId && !tabs.some((item) => item.tabId === tabId) ? tabId : nextTabId(tabs), tabs);
  const at = index === undefined ? tabs.length : Math.max(0, Math.min(index, tabs.length));
  return {
    activeTabId: tab.tabId,
    tabs: [...tabs.slice(0, at), tab, ...tabs.slice(at)],
    layout: tab.layout,
    activeChartId: tab.activeChartId,
    charts: tab.charts,
    links: tab.links,
    panels: tab.panels,
    replayMode: false,
    replaySessionId: state.replaySessionId + 1,
  };
}

/** A copy of `source` with new chart ids, named "<name> copy". */
function duplicatedTab(source: TradingTabState, tabId: string): TradingTabState {
  const charts = source.charts.map((chart) => copyChart(chart, duplicatedChartId(tabId, chart.chartId)));
  const activeIndex = Math.max(0, source.charts.findIndex((chart) => chart.chartId === source.activeChartId));
  return {
    tabId,
    name: `${source.name} copy`,
    layout: source.layout,
    activeChartId: charts[Math.min(activeIndex, charts.length - 1)].chartId,
    charts,
    links: { ...source.links },
    panels: { ...source.panels },
  };
}

/** A tab with one default chart. */
function blankTab(tabId: string, tabs: readonly TradingTabState[], panels: TradingPanelState): TradingTabState {
  const chart = copyChart(initialChart(), `${tabId}-chart-1`);
  return { ...initialSessionTab(), tabId, name: `Session ${tabs.length + 1}`, activeChartId: chart.chartId, charts: [chart], panels: { ...panels } };
}

/** Applies `next` (a new tab) and returns its id, or null when there was no room. */
function applyNewTab(set: (state: Partial<TradingWorkspaceState>) => void, next: Partial<TradingWorkspaceState> | null): string | null {
  if (!next) return null;
  set(next);
  return next.activeTabId ?? null;
}

/** A fresh tab id for the workspace's tabs (a duplicate's, chosen before its drawings are copied). */
export function newTradingTabId(): string {
  return nextTabId(useTradingStore.getState().tabs);
}

/** A duplicate tab's chart ids: the source's, under the new tab (as `duplicatedTab` names them). */
export function duplicatedChartId(newTabId: string, chartId: string): string {
  return `${newTabId}-${chartId}`;
}

let fallbackTabSequence = 0;

function nextTabId(tabs: readonly TradingTabState[]): string {
  const ids = new Set(tabs.map((tab) => tab.tabId));
  for (let attempt = 0; attempt < 10; attempt += 1) {
    fallbackTabSequence += 1;
    const uniquePart = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${fallbackTabSequence.toString(36)}`;
    const tabId = `tab-${uniquePart}`;
    if (!ids.has(tabId)) return tabId;
  }
  return `tab-${Date.now().toString(36)}-${fallbackTabSequence.toString(36)}-${tabs.length}`;
}

function sessionFromState(state: TradingWorkspaceState, tab: TradingTabState): TradingTabState {
  return {
    ...tab,
    layout: state.layout,
    activeChartId: state.activeChartId,
    charts: state.charts,
    links: state.links,
    panels: state.panels,
  };
}

/** Session-only state that a freshly loaded workspace starts without. */
export function freshTradingSessionState(): Pick<TradingWorkspaceState, 'replayMode' | 'closedTabs'> {
  return { replayMode: false, closedTabs: [] };
}

function rememberClosedTab(closed: readonly ClosedTradingTab[], tab: TradingTabState, index: number): ClosedTradingTab[] {
  return [...closed, { tab, index }].slice(-MAX_CLOSED_TRADING_TABS);
}

function reopenedTabState(state: TradingWorkspaceState): Partial<TradingWorkspaceState> | null {
  const entry = state.closedTabs.at(-1);
  if (!entry || state.tabs.length >= MAX_TRADING_TABS) return null;
  const closedTabs = state.closedTabs.slice(0, -1);
  if (state.tabs.some((tab) => tab.tabId === entry.tab.tabId)) return { closedTabs };
  const tabs = state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab);
  const index = Math.min(entry.index, tabs.length);
  // Its group members take their groups' current symbols (TVP-4.1).
  const tab = reconcileGroups(entry.tab, state.charts, tabs, state.activeTabId);
  return {
    activeTabId: tab.tabId,
    tabs: [...tabs.slice(0, index), tab, ...tabs.slice(index)],
    closedTabs,
    layout: tab.layout,
    activeChartId: tab.activeChartId,
    charts: tab.charts,
    links: tab.links,
    panels: tab.panels,
    replayMode: false,
    replaySessionId: state.replaySessionId + 1,
  };
}

function syncActiveTab(
  state: TradingWorkspaceState,
  patch: Partial<Pick<TradingWorkspaceState, 'layout' | 'activeChartId' | 'charts' | 'links' | 'panels'>> = {},
) {
  const next = { ...state, ...patch };
  const activeTab = state.tabs.find((tab) => tab.tabId === state.activeTabId);
  if (!activeTab) return { ...patch, tabs: [sessionFromState(next, initialSessionTab())] };
  return {
    ...patch,
    tabs: state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(next, activeTab) : tab),
  };
}

/** A chart change, with the tab's links and the chart's colour group (TVP-4.1) carrying it to other charts. */
function chartUpdate(state: TradingWorkspaceState, chartId: string, patch: Partial<Omit<TradingChartState, 'chartId' | 'linkGroup'>>) {
  const instrumentChanged = patch.instrumentId !== undefined;
  const linkedInstrument = instrumentChanged && state.links.instrument;
  const linkedInterval = patch.interval !== undefined && state.links.interval;
  const charts = state.charts.map((chart) => {
    if (chart.chartId === chartId) {
      return { ...chart, ...patch, ...(instrumentChanged && patch.bindingId === undefined ? { bindingId: null } : {}) };
    }
    return {
      ...chart,
      ...(linkedInstrument ? { instrumentId: patch.instrumentId, bindingId: null } : {}),
      ...(linkedInterval ? { interval: patch.interval } : {}),
    };
  });
  // A chart in a colour group carries its new symbol to the group's charts in every tab.
  const linked = linkedSymbolChange(state, chartId, patch.instrumentId, charts);
  const next = syncActiveTab(state, { charts: linked?.charts ?? charts });
  return linked ? { ...next, tabs: linked.otherTabs(next.tabs) } : next;
}

export const useTradingStore = create<TradingWorkspaceState>((set, get) => ({
  activeTabId: 'tab-1',
  tabs: [initialSessionTab()],
  closedTabs: [],
  layout: 'auto',
  activeChartId: 'chart-1',
  replayMode: false,
  replaySessionId: 0,
  drawingTool: 'cursor',
  drawingSnapMode: 'ohlc',
  drawingsHidden: false,
  drawingToolSettings: loadDrawingToolSettings(),
  charts: [initialChart()],
  links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
  panels: { right: true, bottom: true },
  favoriteInstrumentIds: [],
  favoriteIntervals: [...DEFAULT_FAVORITE_INTERVALS],
  setLayout: (layout) => set((state) => syncActiveTab(state, { layout })),
  setActiveTab: (activeTabId) => set((state) => {
    if (activeTabId === state.activeTabId) return state;
    const selected = state.tabs.find((tab) => tab.tabId === activeTabId);
    if (!selected) return state;
    const current = state.tabs.find((tab) => tab.tabId === state.activeTabId);
    const tabs = current
      ? state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab)
      : state.tabs;
    return {
      activeTabId,
      tabs,
      layout: selected.layout,
      activeChartId: selected.activeChartId,
      charts: selected.charts,
      links: selected.links,
      panels: selected.panels,
      replayMode: false,
      replaySessionId: state.replaySessionId + 1,
    };
  }),
  addTab: (name) => {
    let createdId: string | null = null;
    set((state) => {
      if (state.tabs.length >= MAX_TRADING_TABS) return state;
      const current = state.tabs.find((tab) => tab.tabId === state.activeTabId);
      const tabs = current
        ? state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab)
        : state.tabs;
      const tabId = nextTabId(tabs);
      const charts = copySessionCharts(state.charts, tabId);
      const activeChartIndex = Math.max(0, state.charts.findIndex((chart) => chart.chartId === state.activeChartId));
      const selectedChart = charts[Math.min(activeChartIndex, charts.length - 1)];
      const tab: TradingTabState = {
        tabId,
        name: name?.trim() || `Session ${tabs.length + 1}`,
        layout: state.layout,
        activeChartId: selectedChart.chartId,
        charts,
        links: { ...state.links },
        panels: { ...state.panels },
      };
      createdId = tabId;
      return {
        activeTabId: tabId,
        tabs: [...tabs, tab],
        layout: tab.layout,
        activeChartId: tab.activeChartId,
        charts: tab.charts,
        links: tab.links,
        panels: tab.panels,
        replayMode: false,
        replaySessionId: state.replaySessionId + 1,
      };
    });
    return createdId;
  },
  duplicateTab: (tabId, newTabId) => {
    const state = get();
    const index = state.tabs.findIndex((tab) => tab.tabId === tabId);
    if (index < 0) return null;
    const source = tabId === state.activeTabId ? sessionFromState(state, state.tabs[index]) : state.tabs[index];
    return applyNewTab(set, stateWithNewTab(state, (newId) => duplicatedTab(source, newId), index + 1, newTabId));
  },
  addBlankTab: () => applyNewTab(set, stateWithNewTab(get(), (tabId, tabs) => blankTab(tabId, tabs, get().panels))),
  renameTab: (tabId, name) => set((state) => {
    const nextName = name.trim();
    if (!nextName) return state;
    const current = state.tabs.find((tab) => tab.tabId === state.activeTabId);
    const tabs = current
      ? state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab)
      : state.tabs;
    return { tabs: tabs.map((tab) => tab.tabId === tabId ? { ...tab, name: nextName } : tab) };
  }),
  removeTab: (tabId) => set((state) => {
    if (state.tabs.length <= 1) return state;
    const targetId = tabId ?? state.activeTabId;
    const targetIndex = state.tabs.findIndex((tab) => tab.tabId === targetId);
    if (targetIndex < 0) return state;
    const current = state.tabs.find((tab) => tab.tabId === state.activeTabId);
    const syncedTabs = current
      ? state.tabs.map((tab) => tab.tabId === state.activeTabId ? sessionFromState(state, tab) : tab)
      : state.tabs;
    const tabs = syncedTabs.filter((tab) => tab.tabId !== targetId);
    const closedTabs = rememberClosedTab(state.closedTabs, syncedTabs[targetIndex], targetIndex);
    if (targetId !== state.activeTabId) return { tabs, closedTabs };
    const selected = tabs[Math.min(targetIndex, tabs.length - 1)];
    return {
      activeTabId: selected.tabId,
      tabs,
      closedTabs,
      layout: selected.layout,
      activeChartId: selected.activeChartId,
      charts: selected.charts,
      links: selected.links,
      panels: selected.panels,
      replayMode: false,
      replaySessionId: state.replaySessionId + 1,
    };
  }),
  reopenClosedTab: () => {
    let reopenedId: string | null = null;
    set((state) => {
      const next = reopenedTabState(state);
      reopenedId = next?.activeTabId ?? null;
      return next ?? state;
    });
    return reopenedId;
  },
  setActiveChart: (activeChartId) => set((state) => (
    state.charts.some((chart) => chart.chartId === activeChartId) ? syncActiveTab(state, { activeChartId }) : state
  )),
  setReplayMode: (replayMode) => set((state) => replayMode
    ? { replayMode: true, replaySessionId: state.replaySessionId + 1 }
    : { replayMode: false }),
  restartReplaySession: () => set((state) => ({ replaySessionId: state.replaySessionId + 1 })),
  addChart: () => set((state) => {
    if (state.charts.length >= MAX_TRADING_CHARTS) return state;
    const source = state.charts.find((chart) => chart.chartId === state.activeChartId) ?? state.charts[0] ?? initialChart();
    const chart = copyChart(source, nextChartId(state.charts));
    return syncActiveTab(state, { charts: [...state.charts, chart], activeChartId: chart.chartId });
  }),
  removeChart: (chartId) => set((state) => {
    if (state.charts.length <= MIN_TRADING_CHARTS) return state;
    const targetId = chartId ?? state.activeChartId;
    const targetIndex = state.charts.findIndex((chart) => chart.chartId === targetId);
    if (targetIndex < 0) return state;
    const charts = state.charts.filter((chart) => chart.chartId !== targetId);
    const activeChartId = state.activeChartId === targetId
      ? charts[Math.min(targetIndex, charts.length - 1)].chartId
      : state.activeChartId;
    return syncActiveTab(state, { charts, activeChartId });
  }),
  setChartCount: (count) => set((state) => {
    const target = boundedChartCount(count);
    if (target === state.charts.length) return state;
    if (target < state.charts.length) {
      const charts = state.charts.slice(0, target);
      return syncActiveTab(state, {
        charts,
        activeChartId: charts.some((chart) => chart.chartId === state.activeChartId)
          ? state.activeChartId
          : charts[charts.length - 1].chartId,
      });
    }
    const charts = [...state.charts];
    const source = state.charts.find((chart) => chart.chartId === state.activeChartId) ?? state.charts[0] ?? initialChart();
    while (charts.length < target) {
      charts.push(copyChart(source, nextChartId(charts)));
    }
    return syncActiveTab(state, { charts });
  }),
  setDrawingTool: (drawingTool) => set({ drawingTool }),
  toggleDrawingsHidden: () => set((state) => ({ drawingsHidden: !state.drawingsHidden })),
  setDrawingToolSetting: (key, value) => set((state) => {
    const drawingToolSettings = { ...state.drawingToolSettings, [key]: value };
    saveDrawingToolSettings(drawingToolSettings);
    return { drawingToolSettings };
  }),
  setDrawingSnapMode: (drawingSnapMode) => set({ drawingSnapMode }),
  updateChart: (chartId, patch) => set((state) => chartUpdate(state, chartId, patch)),
  setChartLinkGroup: (chartId, group) => set((state) => {
    // A chart joining a group takes the group's symbol, which then goes wherever its links reach.
    const symbol = group ? groupSymbol(state.charts, state.tabs, state.activeTabId, group, chartId) : null;
    const grouped = { ...state, charts: chartsWithGroup(state.charts, chartId, group) };
    const current = grouped.charts.find((chart) => chart.chartId === chartId);
    if (!symbol || !current || current.instrumentId === symbol.instrumentId) return syncActiveTab(grouped, { charts: grouped.charts });
    return { charts: grouped.charts, ...chartUpdate(grouped, chartId, { instrumentId: symbol.instrumentId, bindingId: null }) };
  }),
  toggleIndicator: (chartId, id, period) => set((state) => syncActiveTab(state, { charts: state.charts.map((chart) => {
      if (chart.chartId !== chartId) return chart;
      const existing = chart.indicators.find((indicator) => indicator.id === id);
      if (!existing) return { ...chart, indicators: [...chart.indicators, newIndicatorInstance(id, period)] };
      return {
        ...chart,
        indicators: chart.indicators.map((indicator) => {
          // Removing an indicator removes it as a source too (TVP-6.5): what read it reads the close again.
          if (indicator.id !== id) return existing.enabled && indicator.source?.indicatorId === id ? { ...indicator, source: null } : indicator;
          return { ...indicator, enabled: !indicator.enabled, visible: indicator.enabled ? false : true, period: period ?? indicator.period };
        }),
      };
    }) })),
  toggleIndicatorVisibility: (chartId, id) => set((state) => syncActiveTab(state, { charts: state.charts.map((chart) => chart.chartId !== chartId ? chart : {
      ...chart,
      indicators: chart.indicators.map((indicator) => indicator.id !== id || !indicator.enabled ? indicator : {
        ...indicator,
        visible: indicator.visible === false,
      }),
    }) })),
  updateIndicator: (chartId, id, patch) => set((state) => syncActiveTab(state, { charts: state.charts.map((chart) => chart.chartId !== chartId ? chart : {
      ...chart,
      indicators: chart.indicators.map((indicator) => indicator.id === id ? { ...indicator, ...patch, id: indicator.id } : indicator),
    }) })),
  moveIndicator: (chartId, id, direction) => set((state) => syncActiveTab(state, { charts: state.charts.map((chart) => {
      if (chart.chartId !== chartId || !usesSeparatePane(id)) return chart;
      const paneIndicators = chart.indicators.filter((indicator) => usesSeparatePane(indicator.id) && indicator.enabled);
      const currentIndex = paneIndicators.findIndex((indicator) => indicator.id === id);
      if (currentIndex < 0) return chart;
      const targetIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1;
      if (targetIndex < 0 || targetIndex >= paneIndicators.length) return chart;
      const nextPaneIndicators = [...paneIndicators];
      [nextPaneIndicators[currentIndex], nextPaneIndicators[targetIndex]] = [nextPaneIndicators[targetIndex], nextPaneIndicators[currentIndex]];
      let paneIndex = 0;
      const indicators = chart.indicators.map((indicator) => {
        if (!usesSeparatePane(indicator.id) || !indicator.enabled) return indicator;
        const next = nextPaneIndicators[paneIndex];
        paneIndex += 1;
        return next ?? indicator;
      });
      return { ...chart, indicators };
    }) })),
  setIndicators: (chartId, indicators) => set((state) => syncActiveTab(state, { charts: state.charts.map((chart) => chart.chartId === chartId
      ? { ...chart, indicators: indicators.map((indicator) => ({ ...indicator })) }
      : chart) }) ),
  setLink: (key, enabled) => set((state) => syncActiveTab(state, { links: { ...state.links, [key]: enabled } })),
  setPanel: (key, open) => set((state) => syncActiveTab(state, { panels: { ...state.panels, [key]: open } })),
  toggleFavoriteInstrument: (instrumentId) => set((state) => ({
    favoriteInstrumentIds: state.favoriteInstrumentIds.includes(instrumentId)
      ? state.favoriteInstrumentIds.filter((item) => item !== instrumentId)
      : [...state.favoriteInstrumentIds, instrumentId],
  })),
  toggleFavoriteInterval: (interval) => set((state) => ({
    favoriteIntervals: state.favoriteIntervals.includes(interval)
      ? state.favoriteIntervals.filter((item) => item !== interval)
      : sortTradingIntervals([...state.favoriteIntervals, interval]).slice(0, MAX_FAVORITE_INTERVALS),
  })),
  addFavoriteInterval: (interval) => set((state) => (
    state.favoriteIntervals.includes(interval)
      ? state
      : { favoriteIntervals: sortTradingIntervals([...state.favoriteIntervals, interval]).slice(0, MAX_FAVORITE_INTERVALS) }
  )),
}));
