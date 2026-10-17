import { afterEach, describe, expect, it, vi } from 'vitest';
import { comparisonBars } from './tradingChartPanelModel';
import { tradingApi } from './tradingApi';
import type { BarsResponse } from './tradingTypes';

describe('comparison bars', () => {
  afterEach(() => vi.restoreAllMocks());

  it('asks for the same clock-aligned buckets as the main chart', async () => {
    const bars = vi.spyOn(tradingApi, 'bars').mockResolvedValue({ bars: [], instrument: { instrument_id: 'equity:NASDAQ:AAPL' } } as unknown as BarsResponse);
    await comparisonBars('equity:NASDAQ:AAPL', '7m', 500, false);
    expect(bars).toHaveBeenCalledWith('equity:NASDAQ:AAPL', '7m', 500, undefined, { alignment: 'clock', extendedHours: false });
  });
});
