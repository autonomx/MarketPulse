import { isChartLinkGroup } from '../chartLinkGroups';
import type { CoreIndicatorInstance, CoreIndicatorStyle } from '../indicators/coreIndicators';
import { isAutoChartPatternId } from '../indicators/autoPatterns';
import { isTradingViewBuiltInId } from '../indicators/tradingViewBuiltIns';
import { TRADING_CHART_TYPE_OPTIONS } from '../chart/chartAdapter';
import { binanceInstrumentIdFor } from '../cryptoInstrumentDefaults';
import {
  MAX_TRADING_CHARTS,
  MAX_TRADING_TABS,
  type TradingChartSettings,
  type TradingChartState,
  type TradingLayout,
  type TradingLinkState,
  type TradingPanelState,
} from '../tradingStore';
import type { TradingComparison, TradingComparisonPlacement } from '../tradingComparisons';
import { normalizeFavoriteIntervals } from '../tradingIntervals';

export type TradingWorkspacePayload = {
  schemaVersion: 3;
  name: string;
  layout: TradingLayout;
  activeChartId: string;
  charts: TradingChartState[];
  links: TradingLinkState;
  panels: TradingPanelState;
  favoriteInstrumentIds: string[];
  /** Interval favourites (TVP-2.5); absent in documents saved before them. */
  favoriteIntervals?: string[];
  activeTabId?: string;
  tabs?: TradingTabPayload[];
};

export type TradingTabPayload = {
  tabId: string;
  name: string;
  layout: TradingLayout;
  activeChartId: string;
  charts: TradingChartState[];
  links: TradingLinkState;
  panels: TradingPanelState;
};

type PersistableChart = Omit<TradingChartState, 'indicators' | 'bindingId' | 'comparisons' | 'settings'> & {
  indicators?: CoreIndicatorInstance[];
  bindingId?: string | null;
  comparisons?: TradingComparison[];
  settings?: TradingChartSettings;
};

type LegacyLayout = 'one' | 'two-horizontal' | 'two-vertical' | 'four';

const layouts: TradingLayout[] = [
  'auto', 'columns-1', 'columns-2', 'columns-3', 'columns-4',
  'rows-2', 'rows-3', 'rows-4',
  'main-left-3', 'main-right-3', 'main-top-3', 'main-bottom-3',
];
const legacyLayouts: LegacyLayout[] = ['one', 'two-horizontal', 'two-vertical', 'four'];

export function serializeTradingWorkspace(input: {
  name?: string;
  layout: TradingLayout;
  activeChartId: string;
  charts: PersistableChart[];
  links: TradingLinkState;
  panels?: TradingPanelState;
  favoriteInstrumentIds?: string[];
  favoriteIntervals?: string[];
  activeTabId?: string;
  tabs?: TradingTabPayload[];
}): TradingWorkspacePayload {
  const payload: TradingWorkspacePayload = {
    schemaVersion: 3,
    name: input.name?.trim() || 'Main Workspace',
    layout: input.layout,
    activeChartId: input.activeChartId,
    charts: input.charts.map(serializeChart),
    links: { ...input.links },
    panels: { ...(input.panels ?? { right: true, bottom: true }) },
    favoriteInstrumentIds: [...new Set(input.favoriteInstrumentIds ?? [])],
  };
  if (input.favoriteIntervals) payload.favoriteIntervals = [...input.favoriteIntervals];
  if (input.activeTabId && input.tabs?.length) {
    payload.activeTabId = input.activeTabId;
    payload.tabs = input.tabs.map((tab) => ({
      ...tab,
      charts: tab.charts.map(serializeChart),
      links: { ...tab.links },
      panels: { ...tab.panels },
    }));
  }
  return payload;
}

function serializeChart(chart: PersistableChart): TradingChartState {
  const { settings, ...rest } = chart;
  return {
    ...rest,
    bindingId: chart.bindingId ?? null,
    indicators: (chart.indicators ?? []).map((indicator) => ({ ...indicator })),
    comparisons: (chart.comparisons ?? []).map((comparison) => ({ ...comparison })),
    ...(settings && Object.keys(settings).length > 0 ? { settings: { ...settings } } : {}),
  };
}

const CHART_SETTING_KEYS = ['barCountdown', 'extendedHours', 'extendedPriceLine', 'events'] as const;

/**
 * The chart settings a document may carry. A malformed setting falls back to its default
 * (and is logged) rather than failing the whole workspace; unknown keys are dropped.
 */
export function parseChartSettings(value: unknown): TradingChartSettings {
  if (value === undefined || value === null) return {};
  if (typeof value !== 'object' || Array.isArray(value)) {
    console.warn('Trading chart settings are malformed; using defaults.', value);
    return {};
  }
  const raw = value as Record<string, unknown>;
  const settings: TradingChartSettings = {};
  for (const key of CHART_SETTING_KEYS) {
    if (raw[key] === undefined) continue;
    if (typeof raw[key] !== 'boolean') {
      console.warn(`Trading chart setting ${key} is malformed; using its default.`, raw[key]);
      continue;
    }
    settings[key] = raw[key] as boolean;
  }
  return settings;
}

/** Validated indicator instances, or null when any entry is malformed. */
export function parseIndicatorInstances(value: unknown): CoreIndicatorInstance[] | null {
  if (!Array.isArray(value)) return null;
  const indicators = value.filter(indicator);
  return indicators.length === value.length ? indicators.map((item) => ({ ...item })) : null;
}

function indicator(value: unknown): value is CoreIndicatorInstance {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<CoreIndicatorInstance>;
  const ids = ['sma', 'ema', 'rsi', 'macd', 'bollinger', 'atr', 'vwap', 'bull-market-band', 'death-cross', 'ema-stack', 'fair-value-gap', 'golden-cross', 'ideal-bb', 'log-macd', 'macd-dema', 'rsi-divergence', 'stochastic-rsi', 'swing-liquidity', 'volume-profile'];
  if (!item.id || (!ids.includes(item.id) && !isAutoChartPatternId(item.id) && !isTradingViewBuiltInId(item.id) && !/^script-[A-Za-z0-9_-]{1,190}$/.test(item.id))) return false;
  if (typeof item.period !== 'number' || !Number.isInteger(item.period) || item.period < 1) return false;
  if (typeof item.enabled !== 'boolean') return false;
  if (item.visible !== undefined && typeof item.visible !== 'boolean') return false;
  for (const optionalPeriod of [item.fastPeriod, item.slowPeriod, item.signalPeriod]) {
    if (optionalPeriod !== undefined && (!Number.isInteger(optionalPeriod) || optionalPeriod < 1)) return false;
  }
  if (item.standardDeviations !== undefined && (!Number.isFinite(item.standardDeviations) || item.standardDeviations <= 0)) return false;
  if (item.anchorTime !== undefined && item.anchorTime !== null && typeof item.anchorTime !== 'string') return false;
  if (item.compareSymbol !== undefined && item.compareSymbol !== null && typeof item.compareSymbol !== 'string') return false;
  if (item.source !== undefined && item.source !== null) {
    const source = item.source as { indicatorId?: unknown; output?: unknown };
    if (typeof source !== 'object' || typeof source.indicatorId !== 'string' || typeof source.output !== 'string') return false;
  }
  if (item.params !== undefined) {
    if (!item.params || typeof item.params !== 'object' || Array.isArray(item.params)) return false;
    for (const [key, value] of Object.entries(item.params)) {
      if (!key || !(typeof value === 'string' || (typeof value === 'number' && Number.isFinite(value)))) return false;
    }
  }
  if (item.style !== undefined) {
    if (!item.style || typeof item.style !== 'object') return false;
    const style = item.style as CoreIndicatorStyle;
    if (style.lineWidth !== undefined && (!Number.isInteger(style.lineWidth) || style.lineWidth < 1 || style.lineWidth > 4)) return false;
    for (const [key, visible] of Object.entries(style.plots ?? {})) {
      if (!key || typeof visible !== 'boolean') return false;
    }
    for (const [key, lineStyle] of Object.entries(style.lineStyles ?? {})) {
      if (!key || !['solid', 'dotted', 'dashed', 'large-dashed', 'sparse-dotted'].includes(lineStyle)) return false;
    }
    for (const [key, color] of Object.entries(style.colors ?? {})) {
      if (!key || typeof color !== 'string' || !/^#[0-9a-f]{6}$/i.test(color)) return false;
    }
    if (style.backgroundVisible !== undefined && typeof style.backgroundVisible !== 'boolean') return false;
    if (style.backgroundColor !== undefined && (typeof style.backgroundColor !== 'string' || !/^#[0-9a-f]{6}$/i.test(style.backgroundColor))) return false;
    if (style.precision !== undefined && style.precision !== null && (!Number.isInteger(style.precision) || style.precision < 0 || style.precision > 8)) return false;
    for (const statusLineOption of [style.labelsOnPriceScale, style.valuesInStatusLine, style.inputsInStatusLine]) {
      if (statusLineOption !== undefined && typeof statusLineOption !== 'boolean') return false;
    }
  }
  return true;
}

function comparison(value: unknown): value is TradingComparison {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<TradingComparison>;
  const placements: TradingComparisonPlacement[] = ['percent', 'price-scale', 'pane'];
  return typeof item.instrumentId === 'string'
    && item.instrumentId.length > 0
    && typeof item.placement === 'string'
    && placements.includes(item.placement as TradingComparisonPlacement)
    && (item.visible === undefined || typeof item.visible === 'boolean');
}

function parseCharts(value: unknown): TradingChartState[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_TRADING_CHARTS) return null;
  const chartTypes = TRADING_CHART_TYPE_OPTIONS.map((option) => option.value);
  const charts: TradingChartState[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') return null;
    const chart = raw as Partial<TradingChartState>;
    if (typeof chart.chartId !== 'string' || typeof chart.instrumentId !== 'string' || typeof chart.interval !== 'string') return null;
    if (!chart.chartType || !chartTypes.includes(chart.chartType)) return null;
    const bindingId = chart.bindingId === undefined || chart.bindingId === null
      ? null
      : typeof chart.bindingId === 'string'
        ? chart.bindingId
        : undefined;
    if (bindingId === undefined) return null;
    const indicators = Array.isArray(chart.indicators) ? chart.indicators.filter(indicator) : [];
    if (Array.isArray(chart.indicators) && indicators.length !== chart.indicators.length) return null;
    const comparisons = Array.isArray(chart.comparisons) ? chart.comparisons.filter(comparison) : [];
    if (Array.isArray(chart.comparisons) && comparisons.length !== chart.comparisons.length) return null;
    const settings = parseChartSettings(chart.settings);
    charts.push({
      chartId: chart.chartId,
      instrumentId: chart.instrumentId,
      bindingId,
      interval: chart.interval,
      chartType: chart.chartType,
      indicators,
      comparisons,
      ...(Object.keys(settings).length > 0 ? { settings } : {}),
      // An unknown group (a newer colour) is dropped rather than failing the document.
      ...(isChartLinkGroup(chart.linkGroup) ? { linkGroup: chart.linkGroup } : {}),
    });
  }
  if (new Set(charts.map((chart) => chart.chartId)).size !== charts.length) return null;
  return charts;
}

function parseLinks(value: unknown): TradingLinkState | null {
  if (!value || typeof value !== 'object') return null;
  const links = value as Partial<TradingLinkState>;
  if (['instrument', 'interval', 'crosshair', 'visibleRange'].some((key) => typeof links[key as keyof TradingLinkState] !== 'boolean')) return null;
  // Time sync (TVP-4.2) is optional: documents written before it have none (off).
  if (links.time !== undefined && typeof links.time !== 'boolean') return null;
  return links as TradingLinkState;
}

function parsePanels(value: unknown): TradingPanelState {
  if (!value || typeof value !== 'object') return { right: true, bottom: true };
  const panels = value as Partial<TradingPanelState>;
  return {
    right: typeof panels.right === 'boolean' ? panels.right : true,
    bottom: typeof panels.bottom === 'boolean' ? panels.bottom : true,
  };
}

function parseFavorites(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === 'string' && item.length > 0))].slice(0, 500);
}

function parseTabs(value: unknown): TradingTabPayload[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_TRADING_TABS) return null;
  const tabs: TradingTabPayload[] = [];
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') return null;
    const tab = raw as Partial<TradingTabPayload>;
    const charts = parseCharts(tab.charts);
    const links = parseLinks(tab.links);
    if (typeof tab.tabId !== 'string' || !tab.tabId || typeof tab.name !== 'string' || !tab.name.trim() || !charts || !links) return null;
    const requestedActive = typeof tab.activeChartId === 'string' ? tab.activeChartId : charts[0].chartId;
    tabs.push({
      tabId: tab.tabId,
      name: tab.name.trim(),
      layout: typeof tab.layout === 'string' && layouts.includes(tab.layout as TradingLayout) ? tab.layout as TradingLayout : 'auto',
      activeChartId: charts.some((chart) => chart.chartId === requestedActive) ? requestedActive : charts[0].chartId,
      charts,
      links,
      panels: parsePanels(tab.panels),
    });
  }
  if (new Set(tabs.map((tab) => tab.tabId)).size !== tabs.length) return null;
  return tabs;
}

function migrateLegacyCharts(
  charts: TradingChartState[],
  layout: LegacyLayout,
  activeChartId: string,
): TradingChartState[] {
  const activeIndex = Math.max(0, charts.findIndex((chart) => chart.chartId === activeChartId));
  if (layout === 'one') return charts.slice(activeIndex, activeIndex + 1).length === 1
    ? charts.slice(activeIndex, activeIndex + 1)
    : charts.slice(0, 1);
  if (layout === 'four') return charts.slice(0, 4);
  const selected = charts.slice(activeIndex, activeIndex + 2);
  return selected.length === 2 ? selected : charts.slice(0, 2);
}

function migrateLegacyLayout(layout: LegacyLayout): TradingLayout {
  if (layout === 'one' || layout === 'two-vertical') return 'columns-1';
  return 'columns-2';
}

function migrateCryptoChartsToBinance(charts: TradingChartState[]): TradingChartState[] {
  return charts.map((chart) => {
    const instrumentId = binanceInstrumentIdFor(chart.instrumentId);
    return instrumentId === chart.instrumentId
      ? chart
      : { ...chart, instrumentId, bindingId: null };
  });
}

function migrateCryptoFavoritesToBinance(value: unknown): string[] {
  return [...new Set(parseFavorites(value).map(binanceInstrumentIdFor))];
}

export function parseTradingWorkspace(value: unknown): TradingWorkspacePayload | null {
  if (!value || typeof value !== 'object') return null;
  const payload = value as Record<string, unknown>;
  const charts = parseCharts(payload.charts);
  const links = parseLinks(payload.links);
  if (!charts || !links) return null;

  if (payload.schemaVersion === 1) {
    if (typeof payload.layout !== 'string' || !legacyLayouts.includes(payload.layout as LegacyLayout)) return null;
    const legacyLayout = payload.layout as LegacyLayout;
    const requestedActive = typeof payload.activeChartId === 'string' ? payload.activeChartId : charts[0].chartId;
    const visibleCharts = migrateLegacyCharts(charts, legacyLayout, requestedActive);
    const migratedCharts = migrateCryptoChartsToBinance(visibleCharts);
    const activeChartId = visibleCharts.some((chart) => chart.chartId === requestedActive)
      ? requestedActive
      : migratedCharts[0].chartId;
    return {
      schemaVersion: 3,
      name: typeof payload.name === 'string' ? payload.name : 'Main Workspace',
      layout: migrateLegacyLayout(legacyLayout),
      activeChartId,
      charts: migratedCharts,
      links: { ...links, visibleRange: false },
      panels: parsePanels(payload.panels),
      favoriteInstrumentIds: migrateCryptoFavoritesToBinance(payload.favoriteInstrumentIds),
    };
  }

  if ((payload.schemaVersion !== 2 && payload.schemaVersion !== 3) || typeof payload.layout !== 'string' || !layouts.includes(payload.layout as TradingLayout)) return null;
  const parsedTabs = payload.tabs === undefined ? undefined : parseTabs(payload.tabs);
  if (payload.tabs !== undefined && !parsedTabs) return null;
  const requestedActive = typeof payload.activeChartId === 'string' ? payload.activeChartId : charts[0].chartId;
  const migratedCharts = migrateCryptoChartsToBinance(charts);
  const migratedLinks = payload.schemaVersion === 2 ? { ...links, visibleRange: false } : links;
  const result: TradingWorkspacePayload = {
    schemaVersion: 3,
    name: typeof payload.name === 'string' && payload.name.trim() ? payload.name.trim() : 'Main Workspace',
    layout: payload.layout as TradingLayout,
    activeChartId: migratedCharts.some((chart) => chart.chartId === requestedActive) ? requestedActive : migratedCharts[0].chartId,
    charts: migratedCharts,
    links: migratedLinks,
    panels: parsePanels(payload.panels),
    favoriteInstrumentIds: migrateCryptoFavoritesToBinance(payload.favoriteInstrumentIds),
  };
  const favoriteIntervals = normalizeFavoriteIntervals(payload.favoriteIntervals);
  if (favoriteIntervals) result.favoriteIntervals = favoriteIntervals;
  if (parsedTabs) {
    const migratedTabs = parsedTabs.map((tab) => ({
      ...tab,
      charts: migrateCryptoChartsToBinance(tab.charts),
    }));
    result.tabs = migratedTabs;
    result.activeTabId = typeof payload.activeTabId === 'string' && migratedTabs.some((tab) => tab.tabId === payload.activeTabId)
      ? payload.activeTabId
      : migratedTabs[0].tabId;
  }
  return result;
}
