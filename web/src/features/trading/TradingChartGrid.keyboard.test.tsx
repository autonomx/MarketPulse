import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TradingChartGrid } from './TradingChartGrid';
import { useTradingStore } from './tradingStore';
import { useTradingCommandDispatcher } from './commands/useTradingCommands';

vi.mock('./TradingChartPanel', () => ({
  TradingChartPanel: ({ chartId, chartFocusMode }: { chartId: string; chartFocusMode: boolean }) => (
    <div data-testid="chart" data-chart-id={chartId} data-focus={String(chartFocusMode)} />
  ),
}));

const initialStore = useTradingStore.getState();

function Grid() {
  useTradingCommandDispatcher();
  return <TradingChartGrid onOpenSymbolSearch={vi.fn()} onOpenPineScript={vi.fn()} />;
}

const visibleCharts = () => screen.getAllByTestId('chart').map((chart) => chart.dataset.chartId);
const altEnter = () => act(() => { fireEvent.keyDown(document.body, { key: 'Enter', code: 'Enter', altKey: true }); });

afterEach(() => {
  act(() => useTradingStore.setState(initialStore, true));
});

describe('TradingChartGrid Alt+Enter (TVP-2.3)', () => {
  it('maximises the active chart, follows chart switches and restores the layout', () => {
    act(() => useTradingStore.getState().setChartCount(3));
    const [first, second] = useTradingStore.getState().charts.map((chart) => chart.chartId);
    act(() => useTradingStore.getState().setActiveChart(second));
    render(<Grid />);
    expect(visibleCharts()).toHaveLength(3);

    altEnter();
    expect(visibleCharts()).toEqual([second]);
    expect(screen.getByTestId('chart')).toHaveAttribute('data-focus', 'true');

    act(() => useTradingStore.getState().setActiveChart(first));
    expect(visibleCharts()).toEqual([first]);

    altEnter();
    expect(visibleCharts()).toHaveLength(3);
  });

  it('stays maximised when a dialog has already handled Escape', () => {
    act(() => useTradingStore.getState().setChartCount(2));
    render(<Grid />);
    altEnter();
    expect(visibleCharts()).toHaveLength(1);
    const handled = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    handled.preventDefault();
    act(() => { window.dispatchEvent(handled); });
    expect(visibleCharts()).toHaveLength(1);
    act(() => { fireEvent.keyDown(document.body, { key: 'Escape' }); });
    expect(visibleCharts()).toHaveLength(2);
  });
});
