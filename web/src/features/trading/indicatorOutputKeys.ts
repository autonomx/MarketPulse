// The output lines an indicator instance draws, computed as the chart computes
// them (indicatorScheduler.ts) on a synthetic series. The screener's indicator
// rules (TVP-9.1) and the alert contract (TVP-1.3) use it to offer exactly the
// lines the server registry produces for those inputs.
import { indicatorOutputs, type CoreIndicatorInstance, type IndicatorOutput } from './indicators/coreIndicators';
import { calculateTradingViewBuiltInOutputs, isTradingViewBuiltInId } from './indicators/tradingViewBuiltIns';
import type { MarketBar } from './tradingTypes';

/** A smooth, rising series long enough for every indicator to warm up. */
export function syntheticBars(count = 400): MarketBar[] {
  const start = Date.parse('2026-01-02T14:30:00Z');
  return Array.from({ length: count }, (_, index) => {
    const close = 100 + 10 * Math.sin(index / 9) + index * 0.05;
    const time = new Date(start + index * 60_000).toISOString();
    return {
      instrument_id: 'equity:NASDAQ:TEST', interval: '1m', start_time: time, end_time: new Date(start + (index + 1) * 60_000).toISOString(),
      open: String(close - 0.4), high: String(close + 1), low: String(close - 1), close: String(close), volume: String(1000 + (index % 17) * 50),
      is_final: true, provider: 'fixture', received_at: time,
    } as MarketBar;
  });
}

let cachedBars: MarketBar[] | null = null;

export function indicatorOutputsFor(instance: CoreIndicatorInstance, bars: readonly MarketBar[] = (cachedBars ??= syntheticBars())): IndicatorOutput[] {
  const id = String(instance.id);
  return isTradingViewBuiltInId(id)
    ? calculateTradingViewBuiltInOutputs(bars, { ...instance, id }, {}) as IndicatorOutput[]
    : indicatorOutputs(bars, instance);
}
