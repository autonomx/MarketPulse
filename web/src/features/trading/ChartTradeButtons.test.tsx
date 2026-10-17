import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ quote: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi: api }));

import { ChartTradeButtons } from './ChartTradeButtons';
import { takePaperTicketPrefill } from './paperTicketRequests';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ChartTradeButtons', () => {
  it('shows bid and ask and fills the ticket with a market order', async () => {
    api.quote.mockResolvedValue({ price: '100', bid: '99.5', ask: '100.5' });
    render(<ChartTradeButtons instrumentId="crypto:BTC" bindingId="feed" lastPrice={100} replayMode={false} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Buy at 100.5' }));
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'buy', orderType: 'market', entry: null, source: 'chart' });
    fireEvent.click(screen.getByRole('button', { name: 'Sell at 99.5' }));
    expect(takePaperTicketPrefill()).toMatchObject({ side: 'sell', orderType: 'market' });
  });

  it('uses the last price in replay, without asking for a live quote', () => {
    render(<ChartTradeButtons instrumentId="crypto:BTC" bindingId="feed" lastPrice={42} replayMode />);
    expect(screen.getByRole('button', { name: 'Buy at 42' })).toBeInTheDocument();
    expect(api.quote).not.toHaveBeenCalled();
  });
});
