import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { TradingOptions } = await import('./TradingOptions');

const quote = (symbol: string, bid: number, ask: number, iv: number, delta: number) => ({ symbol, bid, ask, last: null, mark: (bid + ask) / 2, volume: 10, open_interest: 100, iv, delta, gamma: 0.05, theta: -0.05, vega: 0.1, greeks_source: 'model' });
const chain = {
  instrument_id: 'equity:NASDAQ:AAPL', underlying: 'AAPL', expiration: '2026-10-16', underlying_price: 101, years_to_expiry: 7 / 365, rate: 0.04, dividend_yield: 0.004,
  as_of: '2026-10-09T14:00:00Z', source: 'Alpaca options market data (indicative feed)',
  rows: [
    { strike: 100, call: quote('AAPL261016C00100000', 2.0, 2.2, 0.25, 0.6), put: quote('AAPL261016P00100000', 0.9, 1.0, 0.26, -0.4) },
    { strike: 105, call: quote('AAPL261016C00105000', 0.5, 0.6, 0.27, 0.2), put: null },
  ],
};
const ok = (data: unknown) => ({ response: new Response(null, { status: 200 }), data });

beforeEach(() => {
  api.GET.mockReset();
  api.GET.mockImplementation(async (path: string) => (path === '/api/trading/options/expirations'
    ? ok([{ date: '2026-10-16', days: 7, contracts: 4, open_interest: 400 }, { date: '2026-10-23', days: 14, contracts: 2, open_interest: 0 }])
    : ok(chain)));
});

function show(instrumentId = 'equity:NASDAQ:AAPL') {
  render(<QueryClientProvider client={new QueryClient()}><TradingOptions instrumentId={instrumentId} /></QueryClientProvider>);
}

describe('options tool (TVP-10.4)', () => {
  it('shows the nearest chain and builds a bull call spread from it', async () => {
    show();
    const table = await screen.findByRole('table', { name: 'Option chain for 2026-10-16' });
    expect(within(table).getAllByRole('row')[2]).toHaveTextContent('25.0%');
    expect(api.GET).toHaveBeenCalledWith('/api/trading/options/chain', { params: { query: { instrument_id: 'equity:NASDAQ:AAPL', expiration: '2026-10-16' } } });
    expect(screen.getByText(/Pick a bid/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Buy the 100 call at 2.20' }));
    fireEvent.click(screen.getByRole('button', { name: 'Sell the 105 call at 0.50' }));
    const legs = screen.getByRole('table', { name: 'Legs' });
    expect(within(legs).getAllByRole('row')).toHaveLength(3);
    const summary = screen.getByLabelText('Strategy summary');
    expect(summary).toHaveTextContent('Net debit$170');
    expect(summary).toHaveTextContent('Max profit$330');
    expect(summary).toHaveTextContent('Max loss−$170');
    expect(summary).toHaveTextContent('Breakevens101.70');
    expect(screen.getByRole('img', { name: 'Profit and loss by underlying price' })).toBeInTheDocument();
    fireEvent.click(within(legs).getByRole('button', { name: 'Remove AAPL261016C00105000' }));
    expect(screen.getByLabelText('Strategy summary')).toHaveTextContent('Max profitUnlimited');
  });

  it('switches to the volatility smile and other expirations, and says when the symbol has no options', async () => {
    show();
    fireEvent.click(await screen.findByRole('tab', { name: 'Volatility' }));
    expect(await screen.findByRole('img', { name: 'Implied volatility by strike, 2026-10-16' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Expiration' }), { target: { value: '2026-10-23' } });
    expect(api.GET).toHaveBeenLastCalledWith('/api/trading/options/chain', { params: { query: { instrument_id: 'equity:NASDAQ:AAPL', expiration: '2026-10-23' } } });
  });

  it('is for US stocks only', () => {
    show('crypto:BINANCE:spot:BTC-USDT');
    expect(screen.getByText('Option chains are for US stocks and ETFs.')).toBeInTheDocument();
    expect(api.GET).not.toHaveBeenCalled();
  });
});
