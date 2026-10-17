/**
 * Volume Delta and Cumulative Volume Delta (TVP-6.4), from lower-timeframe bars (TVP-0.6), like TradingView's: each
 * lower bar's volume counts as buying when it closes above its open (or, unchanged, above the previous close) and as
 * selling when below; unchanged bars keep the previous direction. Volume Delta is each chart bar's buying minus selling
 * volume; Cumulative Volume Delta adds it up and restarts each anchor period.
 *
 * The lower bars reach back MAX_INTRABAR_BARS from the latest bar, so older chart bars have no value (TradingView has a
 * similar limit). On auto, the latest bars read TradingView's lower interval (1m intraday, 5m daily, 1h above); above
 * 1h, where that reaches only a few dozen bars, the older bars read a coarser one (5m, 1h, 1d) so a few hundred bars
 * have a value: two requests. On a 1m chart each bar is its own intrabar (no seconds data). Drawn as candles, like
 * TradingView's. Computed on the main thread after the bars load, like the external data.
 */
import { autoIntrabarInterval, groupIntrabars, isIntrabarInterval, loadIntrabars, MAX_INTRABAR_BARS, tradingViewIntrabarInterval } from '../intrabarData';
import { tradingIntervalDurationMs } from '../tradingIntervals';
import type { MarketBar } from '../tradingTypes';
import type { CoreIndicatorInstance, IndicatorOutput } from './coreIndicators';
import { sessionClock, sessionPeriods, UTC_SESSION, type TradingSessionSpec } from './tradingSessions';

export const INTRABAR_INDICATOR_IDS = ['tv-volume-delta', 'tv-cumulative-volume-delta'] as const;
export const INTRABAR_REQUIREMENT = 'Reads lower-timeframe bars, up to 5,000 back from the latest bar; older chart bars have no value.';
/** Lower intervals offered besides auto, finest first. */
export const INTRABAR_LOWER_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const;

export function isIntrabarIndicatorId(id: string): boolean {
  return (INTRABAR_INDICATOR_IDS as readonly string[]).includes(id);
}

/** The one line each draws (the server keys it the same, `indicators/intrabar.py`): its candles' close. */
export function intrabarOutputLine(id: string): { key: string; title: string } | null {
  if (id === 'tv-volume-delta') return { key: `${id}:delta`, title: 'Volume Delta' };
  if (id === 'tv-cumulative-volume-delta') return { key: `${id}:cvd`, title: 'CVD' };
  return null;
}

export type BarDelta = { delta: number; max: number; min: number };

/** Each chart bar's volume delta from its lower bars, by the chart bar's start time (ms). */
export function intrabarDeltas(chartBars: readonly MarketBar[], lowerBars: readonly MarketBar[], interval: string): Map<number, BarDelta> {
  const deltas = new Map<number, BarDelta>();
  let previousClose: number | null = null;
  // TradingView starts as buying (`var isBuyVolume = true`).
  let direction = 1;
  for (const [start, group] of groupIntrabars(chartBars, lowerBars, interval)) {
    let running = 0;
    let max = 0;
    let min = 0;
    for (const bar of group) {
      const open = Number(bar.open);
      const close = Number(bar.close);
      const volume = Number(bar.volume) || 0;
      if (close > open) direction = 1;
      else if (close < open) direction = -1;
      else if (previousClose !== null && close > previousClose) direction = 1;
      else if (previousClose !== null && close < previousClose) direction = -1;
      running += direction * volume;
      max = Math.max(max, running);
      min = Math.min(min, running);
      previousClose = close;
    }
    deltas.set(start, { delta: running, max, min });
  }
  return deltas;
}

const UP = '#089981';
const DOWN = '#f23645';

/** Volume Delta, as TradingView draws it: a candle from 0 to the bar's delta, its wicks the running delta's extremes. */
export function volumeDeltaPoints(bars: readonly MarketBar[], deltas: ReadonlyMap<number, BarDelta>): IndicatorOutput['points'] {
  return bars.flatMap((bar) => {
    const value = deltas.get(Date.parse(bar.start_time));
    return value ? [{ time: bar.start_time, value: value.delta, open: 0, high: value.max, low: value.min, color: value.delta >= 0 ? UP : DOWN }] : [];
  });
}

/** Cumulative Volume Delta: the deltas added up within each anchor period (session day, week or month). */
export function cumulativeVolumeDeltaPoints(
  bars: readonly MarketBar[],
  deltas: ReadonlyMap<number, BarDelta>,
  anchor: 'D' | 'W' | 'M',
  session: TradingSessionSpec = UTC_SESSION,
): IndicatorOutput['points'] {
  const times = bars.map((bar) => Date.parse(bar.start_time));
  const { key } = sessionPeriods(sessionClock(bars, times, session), anchor, session);
  const points: IndicatorOutput['points'] = [];
  let total = 0;
  bars.forEach((bar, index) => {
    if (index === 0 || key[index] !== key[index - 1]) total = 0;
    const value = deltas.get(times[index]);
    if (!value) return;
    const open = total;
    total += value.delta;
    // TradingView's CVD candles: from the total before the bar to after it, the wicks its running extremes; coloured by
    // this bar's delta (close above open), not the total's sign.
    points.push({ time: bar.start_time, value: total, open, high: open + value.max, low: open + value.min, color: value.delta >= 0 ? UP : DOWN });
  });
  return points;
}

/** The lower interval an instance reads: its own when it fits the chart interval, else auto; null reads the chart's own bars. */
export function intrabarLowerInterval(indicator: CoreIndicatorInstance, interval: string): string | null {
  const chosen = indicator.params?.lowerInterval;
  if (typeof chosen === 'string' && chosen !== 'auto' && isIntrabarInterval(interval, chosen)) return chosen;
  const auto = autoIntrabarInterval(interval);
  return auto && isIntrabarInterval(interval, auto) ? auto : null;
}

/** The chart's feed and session, and the replay clock when the chart is in replay (no lower bar after it is used). */
export type IntrabarIndicatorContext = { session?: TradingSessionSpec; bindingId?: string | null; clock?: number | null };

/**
 * The lower bars for the chart bars, and the earliest chart bar start they fully cover.
 *
 * Live, the closed bars' lower bars load once per chart bar (the range ends where the forming bar starts), and the
 * forming bar's are loaded again once per lower interval. In replay the range is quantized, so playback asks once
 * per quarter of the range rather than every step, and lower bars after the clock are dropped.
 */
async function lowerBarsFor(
  bars: readonly MarketBar[], instrumentId: string, interval: string, lowerInterval: string, context: IntrabarIndicatorContext, withForming = true,
): Promise<{ lower: MarketBar[]; from: number } | null> {
  const lowerMs = tradingIntervalDurationMs(lowerInterval);
  const last = bars[bars.length - 1];
  if (lowerMs === null || !last) return null;
  const span = MAX_INTRABAR_BARS * lowerMs;
  const request = { instrumentId, bindingId: context.bindingId ?? null, interval, lowerInterval };
  const lastStart = Date.parse(last.start_time);
  const lastEnd = Date.parse(last.end_time);
  if (context.clock !== undefined && context.clock !== null) {
    const clock = context.clock;
    const quantum = span / 4;
    const end = Math.ceil(clock / quantum) * quantum;
    const response = await loadIntrabars({ ...request, start: end - span, end });
    return { lower: (response.bars as MarketBar[]).filter((bar) => Date.parse(bar.end_time) <= clock), from: end - span };
  }
  const forming = !last.is_final && withForming;
  const closedEnd = !last.is_final ? lastStart : lastEnd;
  const [closed, open] = await Promise.all([
    loadIntrabars({ ...request, start: closedEnd - span, end: closedEnd }),
    forming ? loadIntrabars({ ...request, start: lastStart, end: lastEnd }, { maxAgeMs: lowerMs }) : null,
  ]);
  return { lower: [...(closed.bars as MarketBar[]), ...((open?.bars ?? []) as MarketBar[])], from: closedEnd - span };
}

/** The outputs of Volume Delta or Cumulative Volume Delta for the chart's bars; none while the lower bars can't load. */
export async function calculateIntrabarIndicatorOutputs(
  bars: readonly MarketBar[],
  indicator: CoreIndicatorInstance,
  context: IntrabarIndicatorContext = {},
): Promise<IndicatorOutput[]> {
  const id = String(indicator.id);
  const interval = bars[0]?.interval;
  const instrumentId = bars[0]?.instrument_id;
  if (!isIntrabarIndicatorId(id) || !interval || !instrumentId) return [];
  const lowerInterval = intrabarLowerInterval(indicator, interval);
  // On auto above 1h, the latest bars read TradingView's finer interval and the older ones the coarse one.
  const auto = typeof indicator.params?.lowerInterval !== 'string' || indicator.params.lowerInterval === 'auto';
  const fineInterval = auto ? tradingViewIntrabarInterval(interval) : null;
  const mixed = fineInterval !== null && lowerInterval !== null && fineInterval !== lowerInterval && isIntrabarInterval(interval, fineInterval);
  // Without a lower interval (a 1m chart: the providers have no seconds), each chart bar is its own intrabar.
  const [loaded, fine] = await Promise.all([
    lowerInterval
      ? lowerBarsFor(bars, instrumentId, interval, lowerInterval, context, !mixed).catch(() => null)
      : { lower: [...bars], from: Number.NEGATIVE_INFINITY },
    mixed ? lowerBarsFor(bars, instrumentId, interval, fineInterval, context).catch(() => null) : null,
  ]);
  if (!loaded) return [];
  // Only chart bars the lower bars fully cover get a value.
  const covered = bars.filter((bar) => Date.parse(bar.start_time) >= loaded.from);
  const deltas = intrabarDeltas(covered, loaded.lower, interval);
  if (fine && fine.lower.length > 0) {
    // The fine bars cover from where the provider's history of them starts, if later than asked (Yahoo keeps a week of 1m).
    const fineFrom = Math.max(fine.from, Math.min(...fine.lower.map((bar) => Date.parse(bar.start_time))));
    const fineCovered = bars.filter((bar) => Date.parse(bar.start_time) >= fineFrom);
    for (const [start, delta] of intrabarDeltas(fineCovered, fine.lower, interval)) deltas.set(start, delta);
  }
  const cumulative = id === 'tv-cumulative-volume-delta';
  const anchor = indicator.params?.anchor === 'W' || indicator.params?.anchor === 'M' ? indicator.params.anchor : 'D';
  const key = intrabarOutputLine(id)!.key;
  const points = cumulative ? cumulativeVolumeDeltaPoints(bars, deltas, anchor, context.session) : volumeDeltaPoints(bars, deltas);
  const output: IndicatorOutput = {
    key,
    title: `${cumulative ? 'CVD' : 'Volume Delta'} (${mixed && fine ? `${fineInterval}, earlier ${lowerInterval}` : lowerInterval ?? 'chart'})`,
    pane: 1,
    kind: 'candles',
    points,
    visible: indicator.style?.plots?.[key] !== false,
    color: indicator.style?.colors?.[key] ?? UP,
    lineStyle: indicator.style?.lineStyles?.[key] ?? 'solid',
    lineWidth: indicator.style?.lineWidth ?? 2,
    precision: indicator.style?.precision ?? 0,
    labelsOnPriceScale: indicator.style?.labelsOnPriceScale ?? true,
    valuesInStatusLine: indicator.style?.valuesInStatusLine ?? true,
    inputsInStatusLine: false,
  };
  return output.visible === false || points.length === 0 ? [] : [output];
}
