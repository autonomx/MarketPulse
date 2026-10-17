// Colour link groups (TVP-4.1): a chart can join one colour group, or none.
// Charts in a group share their symbol across every tab; the tab-wide links
// ("link all") stay as they are. A change is applied in one store update, so
// it can't echo back and forth between charts.
import type { TradingChartState, TradingTabState } from './tradingStore';

export const CHART_LINK_GROUPS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple'] as const;
export type ChartLinkGroup = typeof CHART_LINK_GROUPS[number];

export const CHART_LINK_GROUP_LABELS: Record<ChartLinkGroup, string> = {
  red: 'Red', orange: 'Orange', yellow: 'Yellow', green: 'Green', blue: 'Blue', purple: 'Purple',
};

export function isChartLinkGroup(value: unknown): value is ChartLinkGroup {
  return typeof value === 'string' && (CHART_LINK_GROUPS as readonly string[]).includes(value);
}

type Symbol = Pick<TradingChartState, 'instrumentId' | 'bindingId'>;

/** The charts with the group's symbol: members take it (the binding resolves again for each chart). */
export function withGroupSymbol(charts: readonly TradingChartState[], group: ChartLinkGroup, symbol: Symbol, except?: string): TradingChartState[] {
  return charts.map((chart) => (
    chart.linkGroup === group && chart.chartId !== except && chart.instrumentId !== symbol.instrumentId
      ? { ...chart, instrumentId: symbol.instrumentId, bindingId: null }
      : chart
  ));
}

/** Every other tab with the group's symbol applied to its members. */
export function tabsWithGroupSymbol(tabs: readonly TradingTabState[], activeTabId: string, group: ChartLinkGroup, symbol: Symbol): TradingTabState[] {
  return tabs.map((tab) => {
    if (tab.tabId === activeTabId) return tab;
    const charts = withGroupSymbol(tab.charts, group, symbol);
    return charts.some((chart, index) => chart !== tab.charts[index]) ? { ...tab, charts } : tab;
  });
}

/** The symbol a chart joining `group` takes: a member's in this tab, else in another tab; null when it is the first. */
export function groupSymbol(
  charts: readonly TradingChartState[],
  tabs: readonly TradingTabState[],
  activeTabId: string,
  group: ChartLinkGroup,
  except: string,
): Symbol | null {
  const member = charts.find((chart) => chart.linkGroup === group && chart.chartId !== except)
    ?? tabs.filter((tab) => tab.tabId !== activeTabId).flatMap((tab) => tab.charts).find((chart) => chart.linkGroup === group);
  return member ? { instrumentId: member.instrumentId, bindingId: member.bindingId } : null;
}

type LinkState = {
  charts: readonly TradingChartState[];
  tabs: readonly TradingTabState[];
  activeTabId: string;
  links: { instrument: boolean };
};

/**
 * A symbol change on `chartId` carried as far as groups and tab-wide links reach (TVP-4.1): the groups of charts
 * that take the symbol pass it to their members in every tab, and a tab whose instrument link is on passes it to
 * all its charts, whose groups pass it on in turn. Six groups and a finite set of tabs bound the walk. Null when
 * the symbol reaches no group (the tab's own link already did the rest).
 */
export function linkedSymbolChange(
  state: LinkState,
  chartId: string,
  instrumentId: string | undefined,
  charts: readonly TradingChartState[],
): { charts: TradingChartState[]; otherTabs: (tabs: readonly TradingTabState[]) => TradingTabState[] } | null {
  if (instrumentId === undefined) return null;
  const symbol = { instrumentId, bindingId: null };
  const reached = (chart: TradingChartState) => chart.chartId === chartId || state.links.instrument;
  const groups = new Set(charts.filter((chart) => chart.linkGroup && reached(chart)).map((chart) => chart.linkGroup as ChartLinkGroup));
  if (groups.size === 0) return null;
  let active = [...charts];
  const others = new Map(state.tabs.filter((tab) => tab.tabId !== state.activeTabId).map((tab) => [tab.tabId, tab]));
  for (let seen = -1; seen !== groups.size;) {
    seen = groups.size;
    active = spread(active, groups, symbol, state.links.instrument, chartId);
    for (const [tabId, tab] of others) others.set(tabId, { ...tab, charts: spread(tab.charts, groups, symbol, tab.links.instrument) });
  }
  return {
    charts: active,
    otherTabs: (tabs) => tabs.map((tab) => others.get(tab.tabId) ?? tab),
  };
}

/** One step: group members take the symbol; a linked tab with a member that took it passes it to all its charts. */
function spread(
  charts: readonly TradingChartState[],
  groups: Set<ChartLinkGroup>,
  symbol: Symbol,
  tabLinked: boolean,
  except?: string,
): TradingChartState[] {
  let next = [...charts];
  for (const group of groups) next = withGroupSymbol(next, group, symbol, except);
  const changed = next.some((chart, index) => chart !== charts[index]);
  if (tabLinked && changed) {
    next = next.map((chart) => (chart.instrumentId === symbol.instrumentId || chart.chartId === except ? chart : { ...chart, instrumentId: symbol.instrumentId, bindingId: null }));
  }
  for (const chart of next) if (chart.linkGroup && chart.instrumentId === symbol.instrumentId) groups.add(chart.linkGroup);
  return next;
}

/** The active tab's charts with `chartId` put in `group`, or taken out of any group with null; symbols unchanged. */
export function chartsWithGroup(charts: readonly TradingChartState[], chartId: string, group: ChartLinkGroup | null): TradingChartState[] {
  return charts.map((chart) => {
    if (chart.chartId !== chartId) return chart;
    const rest = { ...chart };
    delete rest.linkGroup;
    return group ? { ...rest, linkGroup: group } : rest;
  });
}

/** A reopened tab's group members take their groups' current symbols, which may have changed while it was closed. */
export function reconcileGroups(tab: TradingTabState, charts: readonly TradingChartState[], tabs: readonly TradingTabState[], activeTabId: string): TradingTabState {
  let next = tab.charts;
  for (const group of new Set(tab.charts.flatMap((chart) => (chart.linkGroup ? [chart.linkGroup] : [])))) {
    const symbol = groupSymbol(charts, tabs, activeTabId, group, '');
    if (symbol) next = withGroupSymbol(next, group, symbol);
  }
  return next === tab.charts ? tab : { ...tab, charts: next };
}
