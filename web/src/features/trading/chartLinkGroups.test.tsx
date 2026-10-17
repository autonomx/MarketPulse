import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ChartLinkGroupButton } from './ChartLinkGroupButton';
import { parseTradingWorkspace, serializeTradingWorkspace } from './persistence/workspaceDocument';
import { defaultTradingIndicators, useTradingStore, type TradingChartState, type TradingTabState } from './tradingStore';

function chart(chartId: string, instrumentId: string, linkGroup?: TradingChartState['linkGroup']): TradingChartState {
  return { chartId, instrumentId, bindingId: 'feed', interval: '1h', chartType: 'candlestick', indicators: defaultTradingIndicators(), ...(linkGroup ? { linkGroup } : {}) };
}

const links = { instrument: false, interval: false, crosshair: true, visibleRange: false };
const panels = { right: true, bottom: true };
const tab = (tabId: string, charts: TradingChartState[]): TradingTabState => ({ tabId, name: tabId, layout: 'auto', activeChartId: charts[0].chartId, charts, links, panels });

beforeEach(() => {
  const active = [chart('a1', 'btc', 'red'), chart('a2', 'eth', 'red'), chart('a3', 'sol'), chart('a4', 'ada', 'blue')];
  useTradingStore.setState({
    activeTabId: 'tab-1',
    tabs: [tab('tab-1', active), tab('tab-2', [chart('b1', 'xrp', 'red'), chart('b2', 'dot', 'blue')])],
    charts: active,
    activeChartId: 'a1',
    links,
    panels,
  });
});
afterEach(cleanup);

const symbols = () => {
  const state = useTradingStore.getState();
  return {
    active: Object.fromEntries(state.charts.map((item) => [item.chartId, item.instrumentId])),
    other: Object.fromEntries(state.tabs.find((item) => item.tabId === 'tab-2')!.charts.map((item) => [item.chartId, item.instrumentId])),
  };
};

describe('colour link groups (TVP-4.1)', () => {
  it('carries a symbol change to the group only, in every tab', () => {
    act(() => useTradingStore.getState().updateChart('a1', { instrumentId: 'doge' }));
    expect(symbols()).toEqual({ active: { a1: 'doge', a2: 'doge', a3: 'sol', a4: 'ada' }, other: { b1: 'doge', b2: 'dot' } });
    // Members resolve their own binding again.
    expect(useTradingStore.getState().charts.find((item) => item.chartId === 'a2')?.bindingId).toBeNull();
    // A chart outside any group changes alone.
    act(() => useTradingStore.getState().updateChart('a3', { instrumentId: 'avax' }));
    expect(symbols().active).toEqual({ a1: 'doge', a2: 'doge', a3: 'avax', a4: 'ada' });
    // An interval change is not a symbol change.
    act(() => useTradingStore.getState().updateChart('a1', { interval: '5m' }));
    expect(useTradingStore.getState().charts.find((item) => item.chartId === 'a2')?.interval).toBe('1h');
  });

  it('joins a group with its symbol, and leaves it', () => {
    act(() => useTradingStore.getState().setChartLinkGroup('a3', 'blue'));
    expect(symbols().active.a3).toBe('ada');
    act(() => useTradingStore.getState().setChartLinkGroup('a3', null));
    expect(useTradingStore.getState().charts.find((item) => item.chartId === 'a3')?.linkGroup).toBeUndefined();
    // The first chart in a group keeps its own symbol.
    act(() => useTradingStore.getState().setChartLinkGroup('a3', 'green'));
    expect(symbols().active.a3).toBe('ada');
  });

  it('is saved with the chart; an unknown colour is dropped', () => {
    const document = serializeTradingWorkspace({ name: 'Desk', layout: 'auto', activeChartId: 'a1', charts: [chart('a1', 'btc', 'red'), chart('a2', 'eth')], links, panels, favoriteInstrumentIds: [] });
    expect(parseTradingWorkspace(document)?.charts?.[0].linkGroup).toBe('red');
    const unknown = { ...document, charts: [{ ...document.charts[0], linkGroup: 'magenta' }, document.charts[1]] };
    expect(parseTradingWorkspace(unknown)?.charts?.[0].linkGroup).toBeUndefined();
  });

  it('meets the tab-wide instrument link both ways, and keeps the active tab entry in step', () => {
    // Tab 1 linked: a1's change reaches every chart in it, so the blue group goes to tab 2 too.
    act(() => useTradingStore.getState().setLink('instrument', true));
    act(() => useTradingStore.getState().updateChart('a3', { instrumentId: 'near' }));
    expect(symbols()).toEqual({ active: { a1: 'near', a2: 'near', a3: 'near', a4: 'near' }, other: { b1: 'near', b2: 'near' } });
    const state = useTradingStore.getState();
    expect(state.tabs.find((item) => item.tabId === 'tab-1')?.charts.map((item) => item.instrumentId)).toEqual(state.charts.map((item) => item.instrumentId));
  });

  it('carries a group change through a linked inactive tab to all its charts', () => {
    useTradingStore.setState((state) => ({
      tabs: state.tabs.map((item) => item.tabId === 'tab-2' ? { ...item, links: { ...item.links, instrument: true }, charts: [...item.charts, chart('b3', 'ltc')] } : item),
    }));
    act(() => useTradingStore.getState().updateChart('a1', { instrumentId: 'doge' }));
    // b1 (red) takes it; tab 2 is linked, so b2 and b3 follow, and b2's blue group reaches a4.
    expect(symbols()).toEqual({ active: { a1: 'doge', a2: 'doge', a3: 'sol', a4: 'doge' }, other: { b1: 'doge', b2: 'doge', b3: 'doge' } });
  });

  it('a reopened tab takes its groups current symbols', () => {
    act(() => useTradingStore.getState().setActiveTab('tab-2'));
    act(() => useTradingStore.getState().removeTab('tab-2'));
    act(() => useTradingStore.getState().updateChart('a1', { instrumentId: 'doge' }));
    act(() => { useTradingStore.getState().reopenClosedTab(); });
    expect(useTradingStore.getState().charts.map((item) => [item.chartId, item.instrumentId])).toEqual([['b1', 'doge'], ['b2', 'ada']]);
  });

  it('picks the group from the chart header', () => {
    render(<ChartLinkGroupButton chartId="a3" group={undefined} />);
    fireEvent.click(screen.getByRole('button', { name: 'No link group: change' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Red' }));
    expect(useTradingStore.getState().charts.find((item) => item.chartId === 'a3')).toMatchObject({ linkGroup: 'red', instrumentId: 'btc' });
  });

  it('closes its menu with Escape or a press outside, and moves with the arrow keys', () => {
    render(<><ChartLinkGroupButton chartId="a3" group="green" /><p>outside</p></>);
    const button = screen.getByRole('button', { name: 'Green link group: change' });
    fireEvent.click(button);
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Green' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Blue' }));
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    fireEvent.pointerDown(screen.getByText('outside'));
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
