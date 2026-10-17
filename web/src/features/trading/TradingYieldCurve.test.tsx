import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { TradingYieldCurve } = await import('./TradingYieldCurve');

const curve = {
  tenors: ['1 Mo', '2 Yr', '10 Yr'], years: [0.0833, 2, 10], source: 'U.S. Department of the Treasury, daily par yield curve rates',
  spreads: { '10Y-2Y': 0.47, '10Y-3M': null },
  curves: [
    { label: 'Latest', date: '2026-10-08', yields: [4.14, 4.75, 5.22] },
    { label: '1 year earlier', date: '2025-10-08', yields: [4.2, 3.6, null] },
  ],
};

describe('yield curve (TVP-10.5)', () => {
  it('draws the curves, lists yields by maturity and charts a maturity’s FRED series', async () => {
    api.GET.mockResolvedValue({ response: new Response(null, { status: 200 }), data: curve });
    const onShowInstrument = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><TradingYieldCurve onShowInstrument={onShowInstrument} /></QueryClientProvider>);
    expect(await screen.findByRole('img', { name: 'Yield curve on 2026-10-08' })).toBeInTheDocument();
    expect(screen.getByText(/10Y–2Y/)).toHaveTextContent('10Y–2Y 0.47% · 10Y–3M —');
    const table = screen.getByRole('table', { name: 'Yields by maturity' });
    expect(within(table).getAllByRole('row')[3]).toHaveTextContent('10 Yr5.22%—');
    fireEvent.click(screen.getByRole('button', { name: 'Chart the 10 Yr yield' }));
    expect(onShowInstrument).toHaveBeenCalledWith('economic:FRED:DGS10');
    fireEvent.change(screen.getByLabelText('Date'), { target: { value: '2026-06-30' } });
    await waitFor(() => expect(api.GET).toHaveBeenLastCalledWith('/api/trading/macro/yield-curve', { params: { query: { on: '2026-06-30' } } }));
  });
});
