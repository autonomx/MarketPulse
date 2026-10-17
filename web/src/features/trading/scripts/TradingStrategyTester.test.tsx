import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { MarketBar } from '../tradingTypes';

const tradingApi = vi.hoisted(() => ({ document: vi.fn() }));
const scriptsApi = vi.hoisted(() => ({ run: vi.fn(), backtest: vi.fn() }));
vi.mock('../tradingApi', () => ({ tradingApi }));
vi.mock('./scriptsApi', () => ({ scriptsApi }));

const { calculateScriptIndicatorOutputs, forgetScriptSource, scriptIndicatorInstance } = await import('./scriptIndicators');
const { TradingStrategyTester } = await import('./TradingStrategyTester');

const START = Date.parse('2026-08-03T14:00:00Z');
const bars = Array.from({ length: 4 }, (_, index) => ({
  instrument_id: 'equity:NASDAQ:AAPL', interval: '1h', start_time: new Date(START + index * 3_600_000).toISOString(),
  end_time: new Date(START + (index + 1) * 3_600_000).toISOString(), open: '100', high: '101', low: '99', close: '100', volume: '1',
  is_final: true, provider: 'fixture', received_at: '',
}) as MarketBar);
const times = bars.map((bar) => bar.start_time);

function trade(number: number, profit: number, open = false) {
  return {
    number, entry_id: 'L', direction: 'long', qty: 2, entry_bar: 1, entry_time: START + 3_600_000, entry_price: 100, entry_comment: null,
    exit_id: open ? null : 'X', exit_bar: open ? null : 2, exit_time: open ? null : START + 7_200_000, exit_price: open ? null : 104, exit_comment: null,
    profit, profit_percent: 4, cum_profit: profit, runup: 9, drawdown: -1, bars: 1, commission: 0,
  };
}

const summary = (net: number) => ({
  net_profit: net, net_profit_percent: 0.08, gross_profit: net, gross_loss: 0, profit_factor: null, open_pl: 0, total_closed_trades: 1,
  total_open_trades: 1, winning_trades: 1, losing_trades: 0, even_trades: 0, percent_profitable: 100, avg_trade: net, avg_trade_percent: 4,
  avg_winning_trade: net, avg_losing_trade: null, ratio_avg_win_loss: null, largest_winning_trade: net, largest_losing_trade: null,
  avg_bars_in_trades: 1, avg_bars_in_winning_trades: 1, avg_bars_in_losing_trades: null, max_drawdown: 3, max_drawdown_percent: 0.03,
  buy_hold_return: 0, buy_hold_return_percent: 0, commission_paid: 0, max_contracts_held: 2, sharpe_ratio: null, sortino_ratio: null,
});

function report(net: number) {
  return {
    settings: { initial_capital: 10_000, pyramiding: 0 }, summary: { all: summary(net), long: summary(net), short: summary(0) },
    trades: [trade(1, net)], trades_total: 1, open_trades: [trade(2, 0, true)],
    fills: [{ bar: 1, time: START, price: 100, qty: 2, side: 'buy', id: 'L', comment: null, position: 2 }, { bar: 2, time: START, price: 104, qty: 2, side: 'sell', id: 'X', comment: null, position: 0 }],
    equity: [10_000, 10_000, 10_008, 10_008], drawdown: [0, -3, 0, 0], buy_hold: [10_000, 10_000, 10_000, 10_000],
  };
}

const empty = { declaration: { kind: 'strategy', overlay: true }, inputs: [], plots: [], hlines: [], fills: [], drawings: [], alerts: [], logs: [], profile: [], bars: 4, seconds: 0.01 };

describe('the strategy tester (TVP-11.5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetScriptSource('sstrat');
    tradingApi.document.mockResolvedValue({ record_id: 'sstrat', revision: 1, payload: { name: 'Cross', source: 'strategy("Cross")' } });
  });

  it('says how to add a strategy when the chart has none', () => {
    render(<TradingStrategyTester indicators={[]} instrumentId="equity:NASDAQ:AAPL" bindingId={null} interval="1h" />);
    expect(screen.getByText('No strategy on this chart')).toBeInTheDocument();
  });

  it('shows the chart run backtest, draws the fills and runs a deep backtest', async () => {
    scriptsApi.run.mockResolvedValue({ times, error: null, result: { ...empty, strategy: report(8) } });
    const instance = scriptIndicatorInstance('sstrat', 'Cross', true, 1);
    const outputs = await calculateScriptIndicatorOutputs(bars, instance);
    const buys = outputs.find((output) => output.key === 'script-sstrat:buys');
    expect(buys).toMatchObject({ pane: 0, render: 'markers', marker: 'arrowUp', markerPosition: 'belowBar' });
    expect(buys?.points).toEqual([{ time: times[1], value: 100, label: 'L +2' }]);
    expect(outputs.find((output) => output.key === 'script-sstrat:sells')?.points[0].label).toBe('X −2');

    render(<TradingStrategyTester indicators={[instance]} instrumentId="equity:NASDAQ:AAPL" bindingId={null} interval="1h" />);
    expect(screen.getByRole('status')).toHaveTextContent('Chart bars: 4 bars');
    expect(screen.getByText('Net profit').nextElementSibling).toHaveTextContent('8');
    expect(screen.getByRole('img', { name: 'Equity and buy and hold' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'List of trades' }));
    const rows = screen.getByRole('table', { name: 'List of trades' }).querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Long (open)');
    fireEvent.click(screen.getByRole('tab', { name: 'Performance summary' }));
    expect(screen.getByRole('table', { name: 'Performance summary' })).toHaveTextContent('Percent profitable100.00%100.00%');

    scriptsApi.backtest.mockResolvedValue({ times: [...times, ...times], error: null, result: { ...empty, strategy: report(50) } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Deep backtest' })));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Deep backtest: 8 bars'));
    expect(scriptsApi.backtest).toHaveBeenCalledWith({ source: 'strategy("Cross")', instrumentId: 'equity:NASDAQ:AAPL', bindingId: null, interval: '1h', inputs: {} });
    fireEvent.click(screen.getByRole('button', { name: 'Chart bars' }));
    expect(screen.getByRole('status')).toHaveTextContent('Chart bars: 4 bars');
  });
});
