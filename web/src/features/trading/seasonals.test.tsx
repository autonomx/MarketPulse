import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MarketBar } from './tradingTypes';

const tradingApi = vi.hoisted(() => ({ bars: vi.fn() }));
vi.mock('./tradingApi', () => ({ tradingApi }));

const { averageCurve, dayOfYear, isDailyHistory, monthlyReturns, monthlySummary, yearlyCurves } = await import('./seasonals');
const { TradingSeasonals } = await import('./TradingSeasonals');

/** Daily bars from 2023-01-02 to 2025-06-30: up 10% a year in a straight line from each year's start. */
function history(): MarketBar[] {
  const bars: MarketBar[] = [];
  for (let time = Date.UTC(2023, 0, 2); time <= Date.UTC(2025, 5, 30); time += 86_400_000) {
    const date = new Date(time);
    const year = date.getUTCFullYear();
    const fraction = dayOfYear(time) / 365;
    const close = 100 * 1.1 ** (year - 2023) * (1 + 0.1 * fraction);
    bars.push({ instrument_id: 'X', interval: '1d', start_time: date.toISOString(), end_time: date.toISOString(), open: String(close), high: String(close), low: String(close), close: String(close), volume: '1', is_final: true, provider: 'f', received_at: '' } as MarketBar);
  }
  return bars;
}

describe('seasonals (TVP-10.3)', () => {
  it('draws each year from its first close on one January–December axis', () => {
    const bars = history();
    expect(isDailyHistory(bars)).toBe(true);
    expect(dayOfYear(Date.UTC(2024, 2, 1))).toBe(dayOfYear(Date.UTC(2023, 2, 1)));
    const curves = yearlyCurves(bars, 5);
    expect(curves.map((curve) => [curve.year, curve.complete])).toEqual([[2025, false], [2024, true], [2023, true]]);
    expect(curves[1].points[0]).toEqual({ day: 0, change: 0 });
    expect(curves[1].points.at(-1)!.change).toBeCloseTo(((1 + 0.1 * 365 / 365) / 1 - 1) * 100, 0);
    const average = averageCurve(curves.filter((curve) => curve.year !== 2025));
    expect(average).toHaveLength(366);
    expect(average[182].change).toBeCloseTo(curves[1].points.find((point) => point.day === 182)!.change, 1);
    expect(yearlyCurves(bars, 2).map((curve) => curve.year)).toEqual([2025, 2024]);
  });

  it('tabulates monthly returns and their averages', () => {
    const rows = monthlyReturns(history(), 3);
    expect(rows.map((row) => row.year)).toEqual([2025, 2024, 2023]);
    expect(rows[0].months.slice(6)).toEqual([null, null, null, null, null, null]);
    // January of the first year has no previous month.
    expect(rows[2].months[0]).toBeNull();
    expect(rows[1].months[5]).toBeGreaterThan(0);
    const summary = monthlySummary(rows);
    expect(summary[1].up).toBe(1);
    expect(summary[11].average).not.toBeNull();
  });

  it('shows the curves and the table for the active symbol', async () => {
    tradingApi.bars.mockResolvedValue({ bars: history() });
    render(<QueryClientProvider client={new QueryClient()}><TradingSeasonals instrumentId="crypto:X" bindingId={null} /></QueryClientProvider>);
    expect(await screen.findByRole('img', { name: 'Yearly performance, 3 years' })).toBeInTheDocument();
    expect(tradingApi.bars).toHaveBeenCalledWith('crypto:X', '1d', 5_000, null);
    expect(screen.getByRole('list', { name: 'Years' })).toHaveTextContent('2025:');
    expect(screen.getByRole('list', { name: 'Years' })).toHaveTextContent('so far');
    expect(screen.getByRole('table', { name: 'Monthly returns' })).toHaveTextContent('Average');
  });
});
