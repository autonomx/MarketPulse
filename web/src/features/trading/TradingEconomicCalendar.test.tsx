import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock('./api/gateway', () => ({ api }));

const { TradingEconomicCalendar, calendarWindow } = await import('./TradingEconomicCalendar');

function show(data: unknown, onOpenSettings = vi.fn()) {
  api.GET.mockResolvedValue({ response: new Response(null, { status: 200 }), data });
  render(<QueryClientProvider client={new QueryClient()}><TradingEconomicCalendar onOpenSettings={onOpenSettings} /></QueryClientProvider>);
  return onOpenSettings;
}

describe('economic calendar (TVP-10.5)', () => {
  it('windows start on Monday', () => {
    const wednesday = new Date('2026-08-12T15:00:00Z');
    expect(calendarWindow('this-week', wednesday)).toEqual({ start: '2026-08-10', end: '2026-08-16' });
    expect(calendarWindow('next-week', wednesday)).toEqual({ start: '2026-08-17', end: '2026-08-23' });
    expect(calendarWindow('last-week', wednesday)).toEqual({ start: '2026-08-03', end: '2026-08-09' });
  });

  it('lists releases by day with their first-published values', async () => {
    show({
      configured: true, start: '2026-08-03', end: '2026-08-16', source: 'FRED release calendar',
      events: [
        { date: '2026-08-07', release_id: 50, name: 'Employment Situation', importance: 'high', link: 'https://fred.stlouisfed.org/release?rid=50', series: 'PAYEMS', unit: 'K jobs', actual: 200, previous: 100 },
        { date: '2026-08-12', release_id: 10, name: 'Consumer Price Index', importance: 'high', link: 'https://fred.stlouisfed.org/release?rid=10', series: 'CPIAUCSL', unit: '% m/m', actual: null, previous: null },
      ],
    });
    const payrolls = await screen.findByRole('link', { name: 'Employment Situation' });
    expect(payrolls).toHaveAttribute('href', 'https://fred.stlouisfed.org/release?rid=50');
    expect(payrolls.closest('tr')).toHaveTextContent('Actual 200Previous 100K jobs');
    expect(screen.getByRole('table', { name: 'Releases on 2026-08-12' })).toHaveTextContent('Actual —');
    expect(api.GET).toHaveBeenCalledWith('/api/trading/economic-calendar', expect.objectContaining({ params: { query: expect.objectContaining({ importance: 'high' }) } }));
  });

  it('asks for a FRED key when there is none', async () => {
    const onOpenSettings = show({ configured: false, start: '2026-08-10', end: '2026-08-16', source: '', events: [] });
    fireEvent.click(await screen.findByRole('button', { name: 'Add it in settings' }));
    expect(onOpenSettings).toHaveBeenCalled();
  });
});
