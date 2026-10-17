import type { MarketBar } from './tradingTypes';
import { tradingIntervalMinutes } from './tradingIntervals';

export function percentChangeFromBars(
  currentPrice: string | number | null | undefined,
  bars: readonly MarketBar[],
): number | null {
  const currentIntervalOpen = Number(bars.at(-1)?.open);
  const current = Number(currentPrice ?? bars.at(-1)?.close);
  if (!Number.isFinite(currentIntervalOpen) || currentIntervalOpen <= 0 || !Number.isFinite(current)) return null;
  return ((current - currentIntervalOpen) / currentIntervalOpen) * 100;
}

/** The bars that make up the latest `targetInterval`, built from a lower interval. */
function lookbackBars(bars: readonly MarketBar[], targetInterval: string): readonly MarketBar[] {
  const targetMinutes = tradingIntervalMinutes(targetInterval);
  const latest = bars.at(-1);
  if (targetMinutes == null || !latest) return [];
  const latestStart = Date.parse(latest.start_time);
  if (!Number.isFinite(latestStart)) return [];
  const cutoff = latestStart - targetMinutes * 60_000;
  return bars.filter((bar) => Date.parse(bar.start_time) >= cutoff);
}

export function percentChangeFromLookback(
  currentPrice: string | number | null | undefined,
  bars: readonly MarketBar[],
  targetInterval: string,
): number | null {
  const reference = lookbackBars(bars, targetInterval)[0];
  const currentIntervalOpen = Number(reference?.open);
  const current = Number(currentPrice ?? bars.at(-1)?.close);
  if (!Number.isFinite(currentIntervalOpen) || currentIntervalOpen <= 0 || !Number.isFinite(current)) return null;
  return ((current - currentIntervalOpen) / currentIntervalOpen) * 100;
}

export type IntervalBarStats = {
  high: number | null;
  low: number | null;
  volume: number | null;
  /**
   * Volume of the latest bar over the mean of the bars before it. The latest
   * bar is usually still forming, so the ratio grows through the bar.
   */
  relativeVolume: number | null;
  /** The latest close when the latest bar is from a pre- or post-market session. */
  extendedPrice: string | null;
};

const EMPTY_STATS: IntervalBarStats = { high: null, low: null, volume: null, relativeVolume: null, extendedPrice: null };

function finiteValues(values: ReadonlyArray<string | number | null | undefined>): number[] {
  return values.map(Number).filter((value) => Number.isFinite(value));
}

/**
 * High, low and volume of the latest interval. With `targetInterval`, the
 * bars are a lower interval and the latest `targetInterval` is aggregated from
 * them; relative volume needs native bars and is left empty then.
 */
export function intervalBarStats(bars: readonly MarketBar[], targetInterval?: string): IntervalBarStats {
  const latest = bars.at(-1);
  const window = targetInterval ? lookbackBars(bars, targetInterval) : latest ? [latest] : [];
  if (!latest || window.length === 0) return EMPTY_STATS;
  const highs = finiteValues(window.map((bar) => bar.high));
  const lows = finiteValues(window.map((bar) => bar.low));
  const volumes = finiteValues(window.map((bar) => bar.volume));
  const volume = volumes.length ? volumes.reduce((sum, value) => sum + value, 0) : null;
  const priorVolumes = targetInterval ? [] : finiteValues(bars.slice(0, -1).map((bar) => bar.volume));
  const averageVolume = priorVolumes.length
    ? priorVolumes.reduce((sum, value) => sum + value, 0) / priorVolumes.length
    : 0;
  return {
    high: highs.length ? Math.max(...highs) : null,
    low: lows.length ? Math.min(...lows) : null,
    volume,
    relativeVolume: volume != null && averageVolume > 0 ? volume / averageVolume : null,
    extendedPrice: latest.session?.startsWith('extended') ? String(latest.close) : null,
  };
}
