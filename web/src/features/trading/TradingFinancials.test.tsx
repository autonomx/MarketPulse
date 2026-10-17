import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { TradingFinancials, compact } = await import('./TradingFinancials');

const DATA = {
  instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', cik: '0000320193', name: 'Apple Inc.', sector: 'Technology', industry: 'Computer Hardware',
  currency: 'USD', price: 230, shares_outstanding: 1.48e10, fetched_at: '2026-08-10T00:00:00Z', source: 'SEC XBRL company facts',
  labels: { revenue: 'Revenue', net_income: 'Net income', equity: "Shareholders' equity", eps_diluted: 'EPS (diluted)' },
  annual: {
    income: [{ end: '2024-09-28', values: { revenue: 391e9, net_income: 93.7e9, eps_diluted: 6.08 } }, { end: '2025-09-27', values: { revenue: 416e9, net_income: 112e9, eps_diluted: 7.46 } }],
    balance: [{ end: '2025-09-27', values: { equity: 73.7e9 } }],
    cash_flow: [],
  },
  quarterly: { income: [{ end: '2026-06-27', values: { revenue: 109.4e9, net_income: -1e9 } }], balance: [], cash_flow: [] },
  ratios: { market_cap: 3.4e12, pe_ratio: 26.4, net_margin: 0.276, revenue_growth: 0.142 },
};

function show(instrumentId = 'equity:NASDAQ:AAPL') {
  api.GET.mockResolvedValue({ response: new Response(null, { status: 200 }), data: DATA });
  return render(<QueryClientProvider client={new QueryClient()}><TradingFinancials instrumentId={instrumentId} /></QueryClientProvider>);
}

describe('financials panel (TVP-10.2)', () => {
  it('formats large numbers compactly', () => {
    expect([compact(3.4e12), compact(416e9), compact(-1.5e6), compact(7.46), compact(null)]).toEqual(['3.40T', '416B', '-1.50M', '7.46', '—']);
  });

  it('shows ratios, then each statement by period, annual or quarterly', async () => {
    show();
    expect(await screen.findByText('Apple Inc.')).toBeInTheDocument();
    expect(api.GET).toHaveBeenCalledWith('/api/trading/fundamentals', { params: { query: { instrument_id: 'equity:NASDAQ:AAPL' } } });
    expect(screen.getByText('P/E (TTM)').nextElementSibling).toHaveTextContent('26.40');
    expect(screen.getByText('Net margin').nextElementSibling).toHaveTextContent('27.6%');
    expect(screen.getByRole('img', { name: 'Revenue and net income by period' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Income statement' }));
    const table = screen.getByRole('table', { name: 'Income statement' });
    expect(table).toHaveTextContent('FY 2024');
    expect(table).toHaveTextContent('416B');
    expect(table).toHaveTextContent('7.46');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Quarterly' }));
    expect(screen.getByRole('table', { name: 'Income statement' })).toHaveTextContent('-1.00B');
  });

  it('says financials are for US stocks', () => {
    api.GET.mockClear();
    show('crypto:BINANCE:spot:BTC-USDT');
    expect(screen.getByText('Financial statements are for US stocks.')).toBeInTheDocument();
    expect(api.GET).not.toHaveBeenCalledWith('/api/trading/fundamentals', expect.anything());
  });
});
