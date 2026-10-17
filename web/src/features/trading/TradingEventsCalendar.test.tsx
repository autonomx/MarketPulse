import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));
const tradingApi = vi.hoisted(() => ({ documents: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi }));

const { TradingEventsCalendar } = await import('./TradingEventsCalendar');

const ok = (data: unknown) => ({ response: new Response(null, { status: 200 }), data });

beforeEach(() => {
  api.GET.mockReset();
  tradingApi.documents.mockReset();
  tradingApi.documents.mockResolvedValue([
    { record_id: 'tech', payload: { schemaVersion: 2, name: 'Tech', items: [{ type: 'symbol', instrumentId: 'equity:NASDAQ:NVDA' }, { type: 'symbol', instrumentId: 'crypto:BINANCE:spot:BTC-USDT' }] } },
  ]);
});

function show(onShowInstrument = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TradingEventsCalendar chartInstrumentIds={['equity:NASDAQ:AAPL', 'equity:NASDAQ:AAPL', 'forex:OANDA:EUR-USD']} onShowInstrument={onShowInstrument} />
    </QueryClientProvider>,
  );
  return onShowInstrument;
}

describe('earnings and dividends calendar (TVP-10.1)', () => {
  it('lists the open charts’ stocks’ events by day and opens a symbol on the chart', async () => {
    api.GET.mockResolvedValue(ok({
      start: '2026-10-05', end: '2026-10-11', pending: [],
      events: [
        { instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', kind: 'earnings', date: '2026-10-08', estimated: false, timing: 'after_close', fiscal_period: 'Q4 FY2026', link: 'https://www.sec.gov/x.htm', special: false },
        { instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', kind: 'dividend', date: '2026-10-09', estimated: false, amount: 0.27, special: false },
      ],
    }));
    const onShowInstrument = show();
    const filing = await screen.findByRole('link', { name: 'Earnings Q4 FY2026 · Oct 8, 2026, after the close' });
    expect(filing).toHaveAttribute('href', 'https://www.sec.gov/x.htm');
    expect(screen.getByRole('table', { name: 'Events on 2026-10-09' })).toHaveTextContent('Dividend $0.27');
    expect(api.GET).toHaveBeenCalledWith('/api/trading/corporate-events/calendar', expect.objectContaining({
      params: { query: expect.objectContaining({ instrument_id: ['equity:NASDAQ:AAPL'], kind: ['earnings', 'dividend', 'split'] }) },
    }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Show AAPL on the chart' })[0]);
    expect(onShowInstrument).toHaveBeenCalledWith('equity:NASDAQ:AAPL');
  });

  it('switches to a watchlist’s stocks and filters by kind', async () => {
    api.GET.mockResolvedValue(ok({ start: '2026-10-05', end: '2026-10-11', pending: ['equity:NASDAQ:NVDA'], events: [] }));
    show();
    await screen.findByRole('option', { name: 'Watchlist: Tech' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Stocks' }), { target: { value: 'tech' } });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Split' }));
    await waitFor(() => expect(api.GET).toHaveBeenLastCalledWith('/api/trading/corporate-events/calendar', expect.objectContaining({
      params: { query: expect.objectContaining({ instrument_id: ['equity:NASDAQ:NVDA'], kind: ['earnings', 'dividend'] }) },
    })));
    expect(await screen.findByText('Loading 1 more stock…')).toBeInTheDocument();
  });
});
