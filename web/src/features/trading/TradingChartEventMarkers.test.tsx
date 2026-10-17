import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TradingChartAdapter } from './chart/chartAdapter';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { TradingChartEventMarkers } = await import('./TradingChartEventMarkers');

afterEach(() => vi.restoreAllMocks());

const times = ['2026-07-29T04:00:00Z', '2026-07-30T04:00:00Z', '2026-07-31T04:00:00Z'];
const adapter = {
  drawingBars: () => ({
    length: times.length,
    at: (index: number) => (times[index] ? { time: times[index] } : undefined),
    indexAtOrBefore: (time: string) => times.filter((bar) => Date.parse(bar) <= Date.parse(time)).length - 1,
  }),
  barTimeToCoordinate: (time: string) => 100 + times.indexOf(time) * 10,
  timeToCoordinate: () => 5_000,
  indicatorPlotWidth: () => 800,
  mainPaneHeight: () => 300,
  onViewportChange: () => () => undefined,
} as unknown as TradingChartAdapter;

describe('chart event markers (TVP-10.1)', () => {
  it('marks earnings and dividends on their bars, shows details on hover and opens the filing', async () => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { callback(0); return 1; });
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    api.GET.mockResolvedValue({ response: new Response(null, { status: 200 }), data: {
      instrument_id: 'equity:NASDAQ:AAPL', ticker: 'AAPL', sources: [], events: [
        { kind: 'earnings', date: '2026-07-30', estimated: false, timing: 'after_close', fiscal_period: 'Q3 FY2026', link: 'https://www.sec.gov/a.htm', special: false },
        { kind: 'dividend', date: '2026-07-31', estimated: false, amount: 0.26, special: false },
        { kind: 'earnings', date: '2026-10-29', estimated: true, special: false }, // upcoming, beyond the plot here
      ],
    } });
    render(<QueryClientProvider client={new QueryClient()}><TradingChartEventMarkers adapter={adapter} instrumentId="equity:NASDAQ:AAPL" interval="1d" replayMode={false} barsRevision={1} /></QueryClientProvider>);
    const earnings = await screen.findByRole('button', { name: 'Earnings Q3 FY2026 · Jul 30, 2026, after the close' });
    expect(earnings).toHaveTextContent('E');
    expect(earnings).toHaveStyle({ left: '102px' });
    expect(screen.getByRole('button', { name: /^Dividend \$0.26/ })).toHaveTextContent('D');
    expect(screen.queryByRole('button', { name: /Estimated/ })).toBeNull();
    fireEvent.pointerEnter(earnings);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Click to open the SEC filing.');
    fireEvent.click(earnings);
    expect(open).toHaveBeenCalledWith('https://www.sec.gov/a.htm', '_blank', 'noopener,noreferrer');
  });

  it('asks for nothing on a chart that isn’t a stock', () => {
    api.GET.mockClear();
    render(<QueryClientProvider client={new QueryClient()}><TradingChartEventMarkers adapter={adapter} instrumentId="crypto:BINANCE:spot:BTC-USDT" interval="1d" replayMode={false} barsRevision={1} /></QueryClientProvider>);
    expect(api.GET).not.toHaveBeenCalled();
  });
});
