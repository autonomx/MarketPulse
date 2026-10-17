import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));
const tradingApi = vi.hoisted(() => ({ documents: vi.fn(), bars: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi }));
vi.mock('./TradingNewsPanel', () => ({ TradingNewsPanel: ({ instrumentId }: { instrumentId: string }) => <p>News for {instrumentId}</p> }));

const { TradingAdvancedView } = await import('./TradingAdvancedView');

const ok = (data: unknown) => ({ response: new Response(null, { status: 200 }), data });
const bars = Array.from({ length: 30 }, (_, index) => ({
  start_time: new Date(Date.parse('2026-09-01T04:00:00Z') + index * 86_400_000).toISOString(),
  open: '100', high: String(101 + index), low: String(99 + index), close: String(100 + index), volume: '1000',
}));

beforeEach(() => {
  api.GET.mockReset();
  tradingApi.bars.mockReset();
  tradingApi.documents.mockReset();
  tradingApi.bars.mockResolvedValue({ bars });
  tradingApi.documents.mockResolvedValue([{ record_id: 'tech', payload: { schemaVersion: 2, name: 'Tech', items: [{ type: 'symbol', instrumentId: 'equity:NASDAQ:NVDA' }] } }]);
  api.GET.mockImplementation(async (path: string) => {
    if (path === '/api/trading/fundamentals') {
      return ok({
        instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', cik: '1', name: 'Apple Inc.', sector: 'Technology', industry: 'Computer Hardware',
        currency: 'USD', price: 129, shares_outstanding: 15e9, annual: {}, labels: {}, fetched_at: '2026-10-01T00:00:00Z', source: 'SEC',
        quarterly: { income: [{ end: '2026-06-27', values: { eps_diluted: 1.62 } }] },
        ratios: { market_cap: 1.9e12, pe_ratio: 30.5, eps_ttm: 6.4, revenue_ttm: 4e11 },
      });
    }
    return ok({
      instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', sources: [], events: [
        { kind: 'dividend', date: '2026-08-11', estimated: false, amount: 0.27, special: false },
        { kind: 'earnings', date: '2026-07-30', estimated: false, timing: 'after_close', fiscal_period: 'Q3 FY2026', link: 'https://www.sec.gov/a.htm', special: false },
        { kind: 'earnings', date: '2026-10-29', estimated: true, timing: 'after_close', fiscal_period: 'Q4 FY2026', special: false },
      ],
    });
  });
});

function show(onShowInstrument = vi.fn(), initialWatchlistId: string | null = null) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TradingAdvancedView chartInstrumentIds={['equity:NASDAQ:AAPL', 'crypto:BINANCE:spot:BTC-USDT']} initialWatchlistId={initialWatchlistId} onShowInstrument={onShowInstrument} />
    </QueryClientProvider>,
  );
  return onShowInstrument;
}

describe('advanced view (TVP-5.4)', () => {
  it('shows the first symbol’s overview, earnings with reported EPS, dividends and news', async () => {
    show();
    expect(await screen.findByText('Apple Inc. · Technology · Computer Hardware')).toBeInTheDocument();
    const keyStats = screen.getByLabelText('Key statistics');
    expect(keyStats).toHaveTextContent('Market cap1.90T');
    expect(keyStats).toHaveTextContent('P/E (TTM)30.50');
    expect(keyStats).toHaveTextContent('Next earningsEstimated earnings Q4 FY2026 · Oct 29, 2026, after the close');
    fireEvent.click(screen.getByRole('tab', { name: 'Earnings' }));
    const reports = screen.getByRole('table', { name: 'Earnings reports' });
    expect(within(reports).getByRole('link', { name: 'Earnings Q3 FY2026 · Jul 30, 2026, after the close' }).closest('tr')).toHaveTextContent('1.62');
    fireEvent.click(screen.getByRole('tab', { name: 'Dividends' }));
    expect(screen.getByRole('list', { name: 'Dividends and splits' })).toHaveTextContent('Dividend $0.27 · ex-date Aug 11, 2026');
    fireEvent.click(screen.getByRole('tab', { name: 'News' }));
    expect(screen.getByText('News for equity:NASDAQ:AAPL')).toBeInTheDocument();
  });

  it('offers no company tabs for a non-stock, switches lists, and opens a symbol on the chart', async () => {
    const onShowInstrument = show(vi.fn(), null);
    fireEvent.click(await screen.findByRole('button', { name: 'BTC-USDT' }));
    expect(screen.getByRole('tablist', { name: 'Symbol details' })).toHaveTextContent('OverviewPerformanceNews');
    fireEvent.click(screen.getByRole('tab', { name: 'Performance' }));
    expect(await screen.findByRole('list', { name: 'Performance' })).toHaveTextContent('1W');
    fireEvent.click(screen.getByRole('button', { name: 'Show BTC-USDT on the chart' }));
    expect(onShowInstrument).toHaveBeenCalledWith('crypto:BINANCE:spot:BTC-USDT');
    await screen.findByRole('option', { name: 'Watchlist: Tech' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Symbols from' }), { target: { value: 'tech' } });
    expect(screen.getByRole('button', { name: 'NVDA' })).toHaveAttribute('aria-pressed', 'true');
  });
});
