import { currentTradingWorkspaceScopeId } from '../persistence/useTradingWorkspacePersistence';
import { MAX_TRADING_TABS, useTradingStore, type TradingTabState } from '../tradingStore';
import { currentWindowKey, tabsForWindow, windowAssignments } from '../tradingWindowSets';
import type { TradingCommandId } from './tradingCommands';
import { useTradingCommand } from './useTradingCommands';

/** The item `step` places after `currentId`, wrapping around. */
export function cycleId<T>(items: readonly T[], currentId: string, idOf: (item: T) => string, step: 1 | -1): string | null {
  if (items.length < 2) return null;
  const index = Math.max(0, items.findIndex((item) => idOf(item) === currentId));
  return idOf(items[(index + step + items.length) % items.length]);
}

/** The tabs this browser window shows (popped-out windows show their own, TVP-4.6). */
function windowTabs(): TradingTabState[] {
  return tabsForWindow(useTradingStore.getState().tabs, windowAssignments(currentTradingWorkspaceScopeId()), currentWindowKey());
}

function useGoToTab(id: TradingCommandId, index: number): void {
  useTradingCommand(id, () => {
    const tab = windowTabs()[index];
    if (tab) useTradingStore.getState().setActiveTab(tab.tabId);
  }, () => windowTabs().length > index);
}

/**
 * Tab and chart-switching commands (TVP-2.3). Closing goes through the
 * workspace's own close action so it asks the same question as the tab's
 * close button.
 */
export function useTradingTabCommands(onCloseTab: (tab: TradingTabState) => void): void {
  const store = useTradingStore.getState;
  const hasSeveralTabs = () => windowTabs().length > 1;
  const switchTab = (step: 1 | -1) => () => {
    const { activeTabId, setActiveTab } = store();
    const next = cycleId(windowTabs(), activeTabId, (tab) => tab.tabId, step);
    if (next) setActiveTab(next);
  };
  // Tab stops at the first and last chart, so the next Tab moves focus on as usual.
  const neighbourChart = (step: 1 | -1) => {
    const { charts, activeChartId } = store();
    return charts[charts.findIndex((chart) => chart.chartId === activeChartId) + step]?.chartId ?? null;
  };
  const switchChart = (step: 1 | -1) => () => {
    const next = neighbourChart(step);
    if (next) store().setActiveChart(next);
  };

  useTradingCommand('layout.nextChart', switchChart(1), () => neighbourChart(1) !== null);
  useTradingCommand('layout.previousChart', switchChart(-1), () => neighbourChart(-1) !== null);
  // A new tab starts with one default chart, as the + launcher's "New chart tab" (TVP-4.4); duplicating copies one.
  useTradingCommand('tab.new', () => { store().addBlankTab(); }, () => store().tabs.length < MAX_TRADING_TABS);
  useTradingCommand('tab.close', () => {
    const { tabs, activeTabId } = store();
    const tab = tabs.find((item) => item.tabId === activeTabId);
    if (tab) onCloseTab(tab);
  }, hasSeveralTabs);
  useTradingCommand('tab.next', switchTab(1), hasSeveralTabs);
  useTradingCommand('tab.previous', switchTab(-1), hasSeveralTabs);
  useGoToTab('tab.goTo1', 0);
  useGoToTab('tab.goTo2', 1);
  useGoToTab('tab.goTo3', 2);
  useGoToTab('tab.goTo4', 3);
  useGoToTab('tab.goTo5', 4);
  useGoToTab('tab.goTo6', 5);
  useGoToTab('tab.goTo7', 6);
  useGoToTab('tab.goTo8', 7);
  useTradingCommand('tab.goToLast', () => {
    const last = windowTabs().at(-1);
    if (last) store().setActiveTab(last.tabId);
  });
  useTradingCommand('tab.reopenClosed', () => { store().reopenClosedTab(); }, () => store().closedTabs.length > 0);
}
