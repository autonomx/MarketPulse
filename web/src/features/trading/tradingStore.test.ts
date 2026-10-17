import { beforeEach, describe, expect, it } from 'vitest';
import {
  MAX_CLOSED_TRADING_TABS,
  MAX_TRADING_CHARTS,
  MAX_TRADING_TABS,
  freshTradingSessionState,
  defaultTradingIndicators,
  useTradingStore,
  type TradingChartState,
} from './tradingStore';

function chart(chartId: string, instrumentId: string, interval: string): TradingChartState {
  return {
    chartId,
    instrumentId,
    bindingId: null,
    interval,
    chartType: 'candlestick',
    indicators: defaultTradingIndicators(),
  };
}

beforeEach(() => {
  useTradingStore.setState({
    activeTabId: 'tab-1',
    tabs: [{
      tabId: 'tab-1',
      name: 'Main Session',
      layout: 'auto',
      activeChartId: 'chart-1',
      charts: [chart('chart-1', 'btc', '1m')],
      links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
      panels: { right: true, bottom: true },
    }],
    layout: 'auto',
    activeChartId: 'chart-1',
    charts: [
      chart('chart-1', 'btc', '1m'),
      chart('chart-2', 'eth', '5m'),
      chart('chart-3', 'sol', '15m'),
      chart('chart-4', 'btc', '1h'),
    ],
    links: { instrument: false, interval: false, crosshair: true, visibleRange: false },
    panels: { right: true, bottom: true },
    favoriteInstrumentIds: [],
    closedTabs: [],
    favoriteIntervals: ['1h', '2h', '4h'],
  });
});

describe('Trading multi-chart store', () => {
  it('keeps active chart explicit across grid changes', () => {
    useTradingStore.getState().setActiveChart('chart-3');
    useTradingStore.getState().setLayout('columns-3');
    expect(useTradingStore.getState().activeChartId).toBe('chart-3');
    expect(useTradingStore.getState().layout).toBe('columns-3');
  });

  it('supports one, two, three, four, and more charts', () => {
    useTradingStore.getState().setChartCount(3);
    expect(useTradingStore.getState().charts).toHaveLength(3);

    useTradingStore.getState().setChartCount(7);
    expect(useTradingStore.getState().charts).toHaveLength(7);
    expect(new Set(useTradingStore.getState().charts.map((item) => item.chartId)).size).toBe(7);

    useTradingStore.getState().setChartCount(MAX_TRADING_CHARTS + 20);
    expect(useTradingStore.getState().charts).toHaveLength(MAX_TRADING_CHARTS);
  });

  it('adds and removes the active chart without allowing an empty workspace', () => {
    useTradingStore.getState().setChartCount(1);
    useTradingStore.getState().addChart();
    const added = useTradingStore.getState().activeChartId;
    expect(useTradingStore.getState().charts).toHaveLength(2);

    useTradingStore.getState().removeChart(added);
    expect(useTradingStore.getState().charts).toHaveLength(1);
    expect(useTradingStore.getState().activeChartId).toBe('chart-1');

    useTradingStore.getState().removeChart('chart-1');
    expect(useTradingStore.getState().charts).toHaveLength(1);
  });

  it('updates only one instrument while linking is disabled', () => {
    useTradingStore.getState().updateChart('chart-1', { instrumentId: 'sol' });
    expect(useTradingStore.getState().charts.map((item) => item.instrumentId)).toEqual(['sol', 'eth', 'sol', 'btc']);
  });

  it('propagates linked instrument and interval independently', () => {
    useTradingStore.getState().setLink('instrument', true);
    useTradingStore.getState().updateChart('chart-2', { instrumentId: 'btc' });
    expect(useTradingStore.getState().charts.every((item) => item.instrumentId === 'btc')).toBe(true);
    expect(useTradingStore.getState().charts.map((item) => item.interval)).toEqual(['1m', '5m', '15m', '1h']);

    useTradingStore.getState().setLink('interval', true);
    useTradingStore.getState().updateChart('chart-4', { interval: '2h' });
    expect(useTradingStore.getState().charts.every((item) => item.interval === '2h')).toBe(true);
  });

  it('keeps interval favourites sorted and distinct (TVP-2.5)', () => {
    const store = useTradingStore.getState();
    store.addFavoriteInterval('7m');
    store.addFavoriteInterval('7m');
    store.toggleFavoriteInterval('1d');
    expect(useTradingStore.getState().favoriteIntervals).toEqual(['7m', '1h', '2h', '4h', '1d']);
    useTradingStore.getState().toggleFavoriteInterval('2h');
    expect(useTradingStore.getState().favoriteIntervals).toEqual(['7m', '1h', '4h', '1d']);
  });

  it('copies chart settings with the chart it was added from', () => {
    useTradingStore.getState().updateChart('chart-1', { settings: { extendedHours: false } });
    useTradingStore.getState().setActiveChart('chart-1');
    useTradingStore.getState().addChart();
    const added = useTradingStore.getState().charts.at(-1);
    expect(added?.settings).toEqual({ extendedHours: false });
    expect(added?.settings).not.toBe(useTradingStore.getState().charts[0].settings);
  });

  it('stores panel visibility and canonical instrument favorites', () => {
    useTradingStore.getState().setPanel('right', false);
    useTradingStore.getState().toggleFavoriteInstrument('equity:NASDAQ:AAPL');
    expect(useTradingStore.getState().panels).toEqual({ right: false, bottom: true });
    expect(useTradingStore.getState().favoriteInstrumentIds).toEqual(['equity:NASDAQ:AAPL']);
    useTradingStore.getState().toggleFavoriteInstrument('equity:NASDAQ:AAPL');
    expect(useTradingStore.getState().favoriteInstrumentIds).toEqual([]);
  });

  it('keeps chart sessions independent across tabs', () => {
    useTradingStore.getState().setChartCount(1);
    useTradingStore.getState().updateChart('chart-1', { instrumentId: 'spy', interval: '1d' });
    const secondTabId = useTradingStore.getState().addTab('Swing Research');
    expect(secondTabId).toBeTruthy();
    expect(secondTabId).not.toBe('tab-1');
    expect(useTradingStore.getState().tabs).toHaveLength(2);

    useTradingStore.getState().updateChart(useTradingStore.getState().activeChartId, { instrumentId: 'eth' });
    useTradingStore.getState().setActiveTab('tab-1');
    expect(useTradingStore.getState().tabs[0].name).toBe('Main Session');
    expect(useTradingStore.getState().charts[0].instrumentId).toBe('spy');

    useTradingStore.getState().setActiveTab(secondTabId!);
    expect(useTradingStore.getState().charts[0].instrumentId).toBe('eth');
    useTradingStore.getState().renameTab(secondTabId!, 'ETH Day');
    expect(useTradingStore.getState().tabs[1].name).toBe('ETH Day');
  });

  it('never reuses a closed tab identity', () => {
    useTradingStore.getState().setChartCount(1);
    const firstCreated = useTradingStore.getState().addTab('First');
    expect(firstCreated).toBeTruthy();
    useTradingStore.getState().removeTab(firstCreated!);
    const replacement = useTradingStore.getState().addTab('Replacement');
    expect(replacement).toBeTruthy();
    expect(replacement).not.toBe(firstCreated);
  });

  it('hides selected overlays without deleting them', () => {
    const store = useTradingStore.getState();
    store.toggleIndicatorVisibility('chart-1', 'sma');
    const hidden = useTradingStore.getState().charts[0].indicators.find((item) => item.id === 'sma');
    expect(hidden?.enabled).toBe(true);
    expect(hidden?.visible).toBe(false);

    store.toggleIndicator('chart-1', 'sma');
    const deleted = useTradingStore.getState().charts[0].indicators.find((item) => item.id === 'sma');
    expect(deleted?.enabled).toBe(false);
  });

  it('adds a community indicator to legacy charts when selected', () => {
    useTradingStore.getState().toggleIndicator('chart-1', 'ema-stack');
    const added = useTradingStore.getState().charts[0].indicators.find((item) => item.id === 'ema-stack');
    expect(added).toMatchObject({ id: 'ema-stack', period: 9, enabled: true, visible: true });
  });

  it('reorders enabled secondary indicators without moving overlays', () => {
    const indicators = defaultTradingIndicators().map((indicator) => (
      indicator.id === 'macd' || indicator.id === 'atr' ? { ...indicator, enabled: true } : indicator
    ));
    useTradingStore.getState().setIndicators('chart-1', indicators);

    useTradingStore.getState().moveIndicator('chart-1', 'rsi', 'down');

    expect(useTradingStore.getState().charts[0].indicators.map((item) => item.id)).toEqual([
      'sma', 'ema', 'macd', 'rsi', 'bollinger', 'atr', 'vwap',
    ]);
    expect(useTradingStore.getState().charts[0].indicators.find((item) => item.id === 'sma')?.enabled).toBe(true);
  });
});

describe('Trading multi-chart store', () => {
  it('keeps secondary indicator order bounded at the first and last pane', () => {
    const indicators = defaultTradingIndicators().map((indicator) => (
      indicator.id === 'macd' ? { ...indicator, enabled: true } : indicator
    ));
    useTradingStore.getState().setIndicators('chart-1', indicators);
    const before = useTradingStore.getState().charts[0].indicators.map((item) => item.id);

    useTradingStore.getState().moveIndicator('chart-1', 'rsi', 'up');
    expect(useTradingStore.getState().charts[0].indicators.map((item) => item.id)).toEqual(before);

    useTradingStore.getState().moveIndicator('chart-1', 'macd', 'down');
    expect(useTradingStore.getState().charts[0].indicators.map((item) => item.id)).toEqual(before);
  });
});

describe('closed tab stack (TVP-2.3)', () => {
  const tabIds = () => useTradingStore.getState().tabs.map((tab) => tab.tabId);

  it('reopens closed tabs newest first, in their old place, with their charts', () => {
    const store = useTradingStore.getState;
    const second = store().addTab('Second')!;
    const third = store().addTab('Third')!;
    store().updateChart(store().activeChartId, { interval: '4h' });
    store().setActiveTab(second);
    store().removeTab(second);
    store().removeTab(third);
    expect(tabIds()).toEqual(['tab-1']);
    expect(store().closedTabs.map((entry) => entry.tab.tabId)).toEqual([second, third]);

    expect(store().reopenClosedTab()).toBe(third);
    expect(store().activeTabId).toBe(third);
    expect(store().charts[0].interval).toBe('4h');
    expect(store().reopenClosedTab()).toBe(second);
    expect(tabIds()).toEqual(['tab-1', second, third]);
    expect(store().reopenClosedTab()).toBeNull();
  });

  it('keeps the last ten closed tabs and does nothing when the tab bar is full', () => {
    const store = useTradingStore.getState;
    for (let index = 0; index < MAX_CLOSED_TRADING_TABS + 2; index += 1) {
      const id = store().addTab(`Tab ${index}`)!;
      store().removeTab(id);
    }
    expect(store().closedTabs).toHaveLength(MAX_CLOSED_TRADING_TABS);
    expect(store().closedTabs[0].tab.name).toBe('Tab 2');

    while (store().tabs.length < MAX_TRADING_TABS) store().addTab();
    expect(store().reopenClosedTab()).toBeNull();
    expect(store().closedTabs).toHaveLength(MAX_CLOSED_TRADING_TABS);
  });

  it('starts a loaded workspace without closed tabs from another one', () => {
    const store = useTradingStore.getState;
    const id = store().addTab()!;
    store().removeTab(id);
    useTradingStore.setState(freshTradingSessionState());
    expect(store().closedTabs).toEqual([]);
    expect(store().reopenClosedTab()).toBeNull();
  });
});
