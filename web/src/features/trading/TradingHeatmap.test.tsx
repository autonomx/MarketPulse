import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { changeColor, squarify } = await import('./treemap');
const { TradingHeatmap } = await import('./TradingHeatmap');

describe('treemap layout (TVP-9.3)', () => {
  it('fills the area with rectangles whose areas follow the sizes', () => {
    const area = { x: 0, y: 0, width: 600, height: 400 };
    const sizes = [6, 6, 4, 3, 2, 2, 1, 0];
    const rects = squarify(sizes, area);
    expect(rects[7]).toBeNull();
    const total = sizes.reduce((sum, size) => sum + size, 0);
    rects.slice(0, 7).forEach((rect, index) => {
      expect(rect!.width * rect!.height).toBeCloseTo((sizes[index] / total) * 600 * 400, 3);
      expect(rect!.x).toBeGreaterThanOrEqual(-1e-9);
      expect(rect!.x + rect!.width).toBeLessThanOrEqual(600 + 1e-6);
      expect(rect!.y + rect!.height).toBeLessThanOrEqual(400 + 1e-6);
    });
    // Squarified: no sliver thinner than a tenth of its length here.
    for (const rect of rects.slice(0, 7)) expect(Math.min(rect!.width, rect!.height) / Math.max(rect!.width, rect!.height)).toBeGreaterThan(0.1);
  });

  it('colours by change: green up, red down, grey flat', () => {
    expect(changeColor(3)).toBe('rgb(8, 153, 129)');
    expect(changeColor(-3)).toBe('rgb(242, 54, 69)');
    expect(changeColor(0.01)).toBe('#5d606b');
  });
});

describe('heatmap panel', () => {
  it('draws the tiles grouped by sector and shows a clicked symbol on the chart', async () => {
    api.GET.mockResolvedValue({
      response: new Response(null, { status: 200 }),
      data: {
        market: 'stocks', size_by: 'market_cap', unclassified: 1, as_of: 0,
        tiles: [
          { instrument_id: 'equity:NASDAQ:AAPL', symbol: 'AAPL', name: 'Apple', group: 'Technology', industry: '', change_percent: 1.2, size: 3e12, price: 200, market_cap: 3e12, dollar_volume: 1e10 },
          { instrument_id: 'equity:NASDAQ:MSFT', symbol: 'MSFT', name: 'Microsoft', group: 'Technology', industry: '', change_percent: -0.5, size: 2.5e12, price: 400, market_cap: 2.5e12, dollar_volume: 8e9 },
          { instrument_id: 'equity:NYSE:XOM', symbol: 'XOM', name: 'Exxon', group: 'Energy', industry: '', change_percent: -2, size: 4e11, price: 100, market_cap: 4e11, dollar_volume: 2e9 },
        ],
      },
    });
    const onShowInstrument = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><TradingHeatmap onShowInstrument={onShowInstrument} /></QueryClientProvider>);
    expect(await screen.findByRole('img', { name: 'US stock heatmap, 3 symbols' })).toBeInTheDocument();
    expect(api.GET).toHaveBeenCalledWith('/api/trading/heatmaps/stocks', { params: { query: { size_by: 'market_cap', limit: 300 } } });
    expect(screen.getByText(/Technology \+0\.\d+%/)).toBeInTheDocument();
    expect(screen.getByText('1 stock is still being classified by sector.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'XOM -2.00%' }));
    expect(onShowInstrument).toHaveBeenCalledWith('equity:NYSE:XOM');
  });
});
