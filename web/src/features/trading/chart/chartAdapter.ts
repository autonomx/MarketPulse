import {
  AreaSeries,
  BarSeries,
  BaselineSeries,
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineType,
  LineStyle,
  PriceScaleMode,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type ISeriesPrimitive,
  type LineData,
  type Logical,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from 'lightweight-charts';
import { FillBandPrimitive } from './fillBandPrimitive';
import type { DrawingPoint } from '../drawings/drawingCommands';
import { barIndexAtOrBefore, barIndexForTime, createBarTimeline, intervalStepMs, timeForBarIndex, type BarTimeline } from '../drawings/tools/barTimeline';
import type { DrawingBar, DrawingBarSeries } from '../drawings/tools/types';
import { indicatorOutputs, indicatorPaneScale, type CoreIndicatorId, type CoreIndicatorInstance, type IndicatorOutput } from '../indicators/coreIndicators';
import type { MarketBar } from '../tradingTypes';
import type { TradingComparisonPlacement } from '../tradingComparisons';
import { formatTradingChartTick, formatTradingChartTime } from '../tradingTime';

export type TradingChartTypeGroup = 'candles' | 'lines' | 'areas' | 'columns' | 'profiles' | 'specialty';
export const TRADING_CHART_TYPE_GROUPS: readonly { id: TradingChartTypeGroup; label: string }[] = [
  { id: 'candles', label: 'Candles' },
  { id: 'lines', label: 'Lines' },
  { id: 'areas', label: 'Areas' },
  { id: 'columns', label: 'Columns' },
  { id: 'profiles', label: 'Volume and profile' },
  { id: 'specialty', label: 'Specialty charts' },
] as const;
export const TRADING_CHART_TYPE_OPTIONS = [
  { value: 'bar', label: 'Bars', group: 'candles', icon: 'bars' },
  { value: 'candlestick', label: 'Candles', group: 'candles', icon: 'candles' },
  { value: 'hollow-candles', label: 'Hollow candles', group: 'candles', icon: 'hollow-candles' },
  { value: 'volume-candles', label: 'Volume candles', group: 'candles', icon: 'volume-candles' },
  { value: 'line', label: 'Line', group: 'lines', icon: 'line' },
  { value: 'line-with-markers', label: 'Line with markers', group: 'lines', icon: 'line-with-markers' },
  { value: 'step-line', label: 'Step line', group: 'lines', icon: 'step-line' },
  { value: 'area', label: 'Area', group: 'areas', icon: 'area' },
  { value: 'hlc-area', label: 'HLC area', group: 'areas', icon: 'hlc-area' },
  { value: 'baseline', label: 'Baseline', group: 'areas', icon: 'baseline' },
  { value: 'columns', label: 'Columns', group: 'columns', icon: 'columns' },
  { value: 'high-low', label: 'High-low', group: 'columns', icon: 'high-low' },
  { value: 'volume-footprint', label: 'Volume footprint', group: 'profiles', icon: 'volume-footprint' },
  { value: 'time-price-opportunity', label: 'Time price opportunity', group: 'profiles', icon: 'time-price-opportunity' },
  { value: 'session-volume-profile', label: 'Session volume profile', group: 'profiles', icon: 'session-volume-profile' },
  { value: 'heikin-ashi', label: 'Heikin Ashi', group: 'specialty', icon: 'heikin-ashi' },
  { value: 'renko', label: 'Renko', group: 'specialty', icon: 'renko' },
  { value: 'line-break', label: 'Line break', group: 'specialty', icon: 'line-break' },
  { value: 'kagi', label: 'Kagi', group: 'specialty', icon: 'kagi' },
  { value: 'point-figure', label: 'Point & figure', group: 'specialty', icon: 'point-figure' },
  { value: 'range', label: 'Range', group: 'specialty', icon: 'range' },
] as const;
export type TradingChartType = typeof TRADING_CHART_TYPE_OPTIONS[number]['value'];
export type TradingChartAppearance = 'light' | 'dark';
/** `pointer`: the price is the pointer's, in the main pane (otherwise the bar's close under it). */
export type TradingCrosshairPoint = { time: Time; price: number; pointer?: boolean };
export type TradingVisibleRange = { from: Time; to: Time };
export type DrawingCoordinate = { x: number; y: number };
export type TradingIndicatorPaneGeometry = {
  id: string;
  paneIndex: number;
  top: number;
  height: number;
};
export type TradingIndicatorSelection = {
  id: CoreIndicatorId;
  x: number;
  y: number;
};
export type TradingPriceScaleSide = 'left' | 'right';
export type TradingPaneHit = { kind: 'main' } | { kind: 'indicator'; id: CoreIndicatorId };
export type TradingSessionPriceLine = { price: number; title: string; color: string };
export type TradingLastPriceLabelPosition = {
  y: number;
  color: string;
  side: TradingPriceScaleSide;
  scaleWidth: number;
  paneHeight: number;
};
export type TradingComparisonData = {
  instrumentId: string;
  label: string;
  placement: TradingComparisonPlacement;
  color: string;
  visible: boolean;
  bars: readonly MarketBar[];
};
export const DEFAULT_TRADING_RIGHT_OFFSET = 10;
const INDICATOR_CANDLE_UP = '#089981';
const INDICATOR_CANDLE_DOWN = '#f23645';
type PriceSeries =
  | ISeriesApi<'Candlestick'>
  | ISeriesApi<'Bar'>
  | ISeriesApi<'Line'>
  | ISeriesApi<'Area'>
  | ISeriesApi<'Baseline'>
  | ISeriesApi<'Histogram'>;
type IndicatorSeries = ISeriesApi<'Line'> | ISeriesApi<'Histogram'> | ISeriesApi<'Candlestick'> | ISeriesApi<'Bar'>;
type LogicalRange = { from: number; to: number };

export function constrainZoomOutRange(
  nextRange: LogicalRange,
  maxRange: LogicalRange | null,
  anchor: number,
): LogicalRange {
  if (!maxRange) return nextRange;
  const maxWidth = maxRange.to - maxRange.from;
  const nextWidth = nextRange.to - nextRange.from;
  if (maxWidth <= 0 || nextWidth <= maxWidth) return nextRange;
  const anchorRatio = (anchor - nextRange.from) / nextWidth;
  const from = anchor - maxWidth * anchorRatio;
  return { from, to: from + maxWidth };
}

function timestamp(value: string): UTCTimestamp {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) throw new Error(`Invalid Trading timestamp: ${value}`);
  return Math.floor(milliseconds / 1_000) as UTCTimestamp;
}

export function normalizeChartBars(bars: readonly MarketBar[]): MarketBar[] {
  const byTimestamp = new Map<number, { bar: MarketBar; revision: number; index: number }>();
  bars.forEach((bar, index) => {
    const milliseconds = Date.parse(bar.start_time);
    if (!Number.isFinite(milliseconds)) return;
    const time = Math.floor(milliseconds / 1_000);
    const revision = Number(bar.ingestion_revision);
    const existing = byTimestamp.get(time);
    if (!existing || revision > existing.revision || (revision === existing.revision && index > existing.index)) {
      byTimestamp.set(time, { bar, revision, index });
    }
  });
  return [...byTimestamp.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, value]) => value.bar);
}

/** Merge a stream update using the same epoch-second identity as chart series. */
export function upsertChartBar(bars: readonly MarketBar[], bar: MarketBar): MarketBar[] {
  const time = timestamp(bar.start_time);
  let left = 0;
  let right = bars.length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (timestamp(bars[middle].start_time) < time) left = middle + 1;
    else right = middle;
  }
  const existing = bars[left];
  const sameTime = existing && timestamp(existing.start_time) === time;
  if (sameTime && bar.ingestion_revision < existing.ingestion_revision) return [...bars];
  return [...bars.slice(0, left), bar, ...bars.slice(left + (sameTime ? 1 : 0))];
}

function closeAtOrBefore(bars: readonly MarketBar[], targetTime: string | null): number {
  if (bars.length === 0) return 0;
  const target = targetTime === null ? Number.POSITIVE_INFINITY : Date.parse(targetTime);
  let candidate = Number(bars[0].close);
  for (const bar of bars) {
    const time = Date.parse(bar.start_time);
    if (!Number.isFinite(time) || time > target) break;
    const close = Number(bar.close);
    if (Number.isFinite(close)) candidate = close;
  }
  return candidate;
}

/**
 * A comparison series on the main series' bar times, as TradingView plots compare symbols: at each main bar
 * time from the comparison's first point to its last, the comparison's latest value at or before that time.
 * Times only the comparison has are dropped, so comparisons never add points to the chart's time scale and a
 * drawing's line and its alert share one bar index. `points` and `times` are ascending.
 */
export function alignSeriesToTimes(
  points: readonly { time: UTCTimestamp; value: number }[],
  times: readonly UTCTimestamp[],
): { time: UTCTimestamp; value: number }[] {
  if (points.length === 0) return [];
  const first = points[0].time;
  const last = points[points.length - 1].time;
  const aligned: { time: UTCTimestamp; value: number }[] = [];
  let next = 0;
  for (const time of times) {
    if (time < first) continue;
    if (time > last) break;
    while (next + 1 < points.length && points[next + 1].time <= time) next += 1;
    aligned.push({ time, value: points[next].value });
  }
  return aligned;
}

/**
 * An indicator's points on the chart's bars, so data on its own clock (Binance funding every 8 h, open interest
 * periods) adds no time points: each bar shows the latest value during it (before the next bar starts; the last bar
 * runs one interval, `stepSeconds`), from the first bar with data to the bar of the last point. Points beyond the
 * last bar's interval (indicators that plot ahead) are kept. Outputs already on bar times are unchanged.
 */
export function alignIndicatorPoints(
  points: readonly { time: UTCTimestamp; value: number }[],
  times: readonly UTCTimestamp[],
  stepSeconds: number | null,
): { time: UTCTimestamp; value: number }[] {
  const last = times.at(-1);
  if (last === undefined || points.length === 0) return [...points];
  const end = stepSeconds === null ? Number.POSITIVE_INFINITY : last + stepSeconds;
  const inBars = points.filter((point) => point.time < end);
  const ahead = points.filter((point) => point.time >= end);
  const known = new Set<number>(times);
  if (inBars.every((point) => known.has(point.time))) return [...points];
  const latest = inBars.at(-1)?.time ?? Number.NEGATIVE_INFINITY;
  const aligned: { time: UTCTimestamp; value: number }[] = [];
  let next = 0;
  let value: number | null = null;
  for (const [index, time] of times.entries()) {
    const windowEnd = times[index + 1] ?? end;
    while (next < inBars.length && inBars[next].time < windowEnd) value = inBars[next++].value;
    if (value !== null && latest >= time) aligned.push({ time, value });
  }
  return [...aligned, ...ahead];
}

function visiblePrimaryPriceRange(
  bars: readonly MarketBar[],
  logicalRange: LogicalRange | null,
  multiplier: number,
): { min: number; max: number; fromTime: number; toTime: number } {
  if (bars.length === 0) return { min: 0, max: 1, fromTime: Number.NEGATIVE_INFINITY, toTime: Number.POSITIVE_INFINITY };
  const fromIndex = Math.max(0, Math.floor(logicalRange?.from ?? 0));
  const toIndex = Math.min(bars.length - 1, Math.ceil(logicalRange?.to ?? bars.length - 1));
  const visibleBars = bars.slice(fromIndex, Math.max(fromIndex + 1, toIndex + 1));
  const prices = visibleBars.flatMap((bar) => [bar.open, bar.high, bar.low, bar.close]
    .map((value) => Number(value) * multiplier)
    .filter((value) => Number.isFinite(value)));
  if (prices.length === 0) return { min: 0, max: 1, fromTime: Number.NEGATIVE_INFINITY, toTime: Number.POSITIVE_INFINITY };
  const rawMin = Math.min(...prices);
  const rawMax = Math.max(...prices);
  const padding = Math.max((rawMax - rawMin) * 0.05, Math.abs(rawMax || rawMin || 1) * 0.001, Number.EPSILON);
  return {
    min: rawMin - padding,
    max: rawMax + padding,
    fromTime: Date.parse(visibleBars[0]?.start_time ?? bars[0].start_time),
    toTime: Date.parse(visibleBars.at(-1)?.start_time ?? bars.at(-1)!.start_time),
  };
}

/**
 * Time <-> bar index over one bar sequence, by the `barTimeline.ts` spec:
 * times between bars interpolate between them, past the data they step by
 * the interval's duration; the two lookups are exact inverses.
 */
export class DrawingTimeIndex {
  readonly timeline: BarTimeline;

  /** `step` defaults to the bars' interval duration (`bars[0].interval`). */
  constructor(bars: readonly Pick<MarketBar, 'start_time'>[], step?: number | null) {
    const interval = (bars[0] as Partial<MarketBar> | undefined)?.interval;
    this.timeline = createBarTimeline(
      bars.map((bar) => Date.parse(bar.start_time)),
      step !== undefined ? step : interval ? intervalStepMs(interval) : null,
    );
  }

  /** A time index over raw time points (UTC milliseconds), e.g. every point the chart plots. */
  static fromTimes(times: Iterable<number>, step: number | null): DrawingTimeIndex {
    const index = new DrawingTimeIndex([], step);
    (index as { timeline: BarTimeline }).timeline = createBarTimeline(times, step);
    return index;
  }

  get length(): number {
    return this.timeline.times.length;
  }

  timeForLogicalIndex(logical: number): string | null {
    const time = timeForBarIndex(this.timeline, logical);
    return time === null ? null : new Date(time).toISOString();
  }

  logicalIndexForTime(value: string): number | null {
    return barIndexForTime(this.timeline, Date.parse(value));
  }

  /** Index of the last bar at or before `value`; -1 before the first bar or for invalid times. */
  indexAtOrBefore(value: string): number {
    const time = Date.parse(value);
    return Number.isFinite(time) ? barIndexAtOrBefore(this.timeline, time) : -1;
  }

  /** The time `count` bars after `value` (negative counts go back). */
  timeAfterBars(value: string, count: number): string | null {
    const logical = this.logicalIndexForTime(value);
    return logical === null ? null : this.timeForLogicalIndex(logical + count);
  }
}

export function drawingTimeForLogicalIndex(logical: number, bars: readonly MarketBar[]): string | null {
  return new DrawingTimeIndex(bars).timeForLogicalIndex(logical);
}

export function drawingLogicalIndexForTime(value: string, bars: readonly MarketBar[]): number | null {
  return new DrawingTimeIndex(bars).logicalIndexForTime(value);
}

export function candlestickData(bar: MarketBar, multiplier = 1): CandlestickData<UTCTimestamp> {
  return { time: timestamp(bar.start_time), open: Number(bar.open) * multiplier, high: Number(bar.high) * multiplier, low: Number(bar.low) * multiplier, close: Number(bar.close) * multiplier };
}
export function lineData(bar: MarketBar, multiplier = 1): LineData<UTCTimestamp> { return { time: timestamp(bar.start_time), value: Number(bar.close) * multiplier }; }
function columnData(bar: MarketBar, multiplier = 1): HistogramData<UTCTimestamp> {
  return {
    time: timestamp(bar.start_time),
    value: Number(bar.close) * multiplier,
    color: Number(bar.close) >= Number(bar.open) ? 'rgba(32,201,151,.72)' : 'rgba(255,107,107,.72)',
  };
}
function volumeCandleData(bar: MarketBar, bars: readonly MarketBar[], multiplier = 1): CandlestickData<UTCTimestamp> {
  const volume = Number(bar.volume);
  const maximum = Math.max(...bars.map((item) => Number(item.volume)), volume, 1);
  const intensity = Math.min(1, Math.max(0, volume / maximum));
  const alpha = (0.35 + intensity * 0.6).toFixed(2);
  const color = Number(bar.close) >= Number(bar.open)
    ? `rgba(32,201,151,${alpha})`
    : `rgba(255,107,107,${alpha})`;
  return { ...candlestickData(bar, multiplier), color };
}
export function volumeData(bar: MarketBar): HistogramData<UTCTimestamp> {
  return { time: timestamp(bar.start_time), value: Number(bar.volume), color: Number(bar.close) >= Number(bar.open) ? 'rgba(32,201,151,.45)' : 'rgba(255,107,107,.42)' };
}

/** Chart types whose bars are built from price moves, not time (each plots its own bar times). */
const SYNTHETIC_BAR_CHART_TYPES: ReadonlySet<TradingChartType> = new Set(['renko', 'range', 'line-break', 'kagi', 'point-figure']);

function isCandlestickType(type: TradingChartType): boolean {
  return type === 'candlestick'
    || type === 'hollow-candles'
    || type === 'volume-candles'
    || type === 'heikin-ashi'
    || type === 'renko'
    || type === 'line-break'
    || type === 'range';
}

function isBarType(type: TradingChartType): boolean {
  return type === 'bar' || type === 'high-low';
}

function isLineType(type: TradingChartType): boolean {
  return type === 'line' || type === 'line-with-markers' || type === 'step-line' || type === 'kagi' || type === 'point-figure';
}

function isAreaType(type: TradingChartType): boolean {
  return type === 'area' || type === 'hlc-area';
}

function isColumnType(type: TradingChartType): boolean {
  return type === 'columns'
    || type === 'volume-footprint'
    || type === 'time-price-opportunity'
    || type === 'session-volume-profile';
}

function isVolumeColumnType(type: TradingChartType): boolean {
  return type === 'volume-footprint' || type === 'session-volume-profile';
}

function syntheticBar(source: MarketBar, open: number, high: number, low: number, close: number, startTime: string): MarketBar {
  const duration = Math.max(1_000, Date.parse(source.end_time) - Date.parse(source.start_time));
  return {
    ...source,
    start_time: startTime,
    end_time: new Date(Date.parse(startTime) + duration).toISOString(),
    open: String(open),
    high: String(high),
    low: String(low),
    close: String(close),
  };
}

export function heikinAshiBars(bars: readonly MarketBar[]): MarketBar[] {
  let previousOpen: number | null = null;
  let previousClose: number | null = null;
  return bars.map((bar) => {
    const open = Number(bar.open);
    const high = Number(bar.high);
    const low = Number(bar.low);
    const close = Number(bar.close);
    const nextClose = (open + high + low + close) / 4;
    const nextOpen = previousOpen === null || previousClose === null
      ? (open + close) / 2
      : (previousOpen + previousClose) / 2;
    const nextHigh = Math.max(high, nextOpen, nextClose);
    const nextLow = Math.min(low, nextOpen, nextClose);
    previousOpen = nextOpen;
    previousClose = nextClose;
    return {
      ...bar,
      open: String(nextOpen),
      high: String(nextHigh),
      low: String(nextLow),
      close: String(nextClose),
    };
  });
}

function typicalRange(bars: readonly MarketBar[]): number {
  const ranges = bars.map((bar) => Math.abs(Number(bar.high) - Number(bar.low))).filter((range) => Number.isFinite(range) && range > 0).sort((left, right) => left - right);
  const median = ranges[Math.floor(ranges.length / 2)] ?? Math.abs(Number(bars[0]?.close ?? 1)) * 0.01;
  return Math.max(median, Math.abs(Number(bars[0]?.close ?? 1)) * 0.0001, Number.EPSILON);
}

export function renkoBars(bars: readonly MarketBar[]): MarketBar[] {
  if (bars.length === 0) return [];
  const brickSize = typicalRange(bars);
  let anchor = Number(bars[0].close);
  let lastTimestamp = Number.NEGATIVE_INFINITY;
  const output: MarketBar[] = [];
  for (const source of bars) {
    const close = Number(source.close);
    while (close - anchor >= brickSize || anchor - close >= brickSize) {
      const direction = close > anchor ? 1 : -1;
      const next = anchor + direction * brickSize;
      const start = Math.max(Date.parse(source.start_time), Number.isFinite(lastTimestamp) ? lastTimestamp + 1_000 : Date.parse(source.start_time));
      const open = anchor;
      output.push(syntheticBar(source, open, Math.max(open, next), Math.min(open, next), next, new Date(start).toISOString()));
      anchor = next;
      lastTimestamp = start;
    }
  }
  return output.length > 0 ? output : [syntheticBar(bars[0], anchor, anchor, anchor, anchor, bars[0].start_time)];
}

export function rangeBars(bars: readonly MarketBar[]): MarketBar[] {
  if (bars.length === 0) return [];
  const rangeSize = typicalRange(bars);
  let anchor = Number(bars[0].close);
  let lastTimestamp = Number.NEGATIVE_INFINITY;
  const output: MarketBar[] = [];
  for (const source of bars) {
    const high = Number(source.high);
    const low = Number(source.low);
    const extremes = high - anchor >= rangeSize ? 1 : anchor - low >= rangeSize ? -1 : 0;
    if (extremes === 0) continue;
    const next = anchor + extremes * rangeSize;
    const start = Math.max(Date.parse(source.start_time), Number.isFinite(lastTimestamp) ? lastTimestamp + 1_000 : Date.parse(source.start_time));
    output.push(syntheticBar(source, anchor, Math.max(anchor, next), Math.min(anchor, next), next, new Date(start).toISOString()));
    anchor = next;
    lastTimestamp = start;
  }
  return output.length > 0 ? output : [syntheticBar(bars[0], anchor, anchor, anchor, anchor, bars[0].start_time)];
}

export function lineBreakBars(bars: readonly MarketBar[]): MarketBar[] {
  if (bars.length === 0) return [];
  const output: MarketBar[] = [];
  const closes: number[] = [];
  for (const source of bars) {
    const close = Number(source.close);
    const previous = closes.slice(-3);
    const accepted = previous.length < 3 || close > Math.max(...previous) || close < Math.min(...previous);
    if (!accepted) continue;
    const open = closes.at(-1) ?? close;
    output.push(syntheticBar(source, open, Math.max(open, close), Math.min(open, close), close, source.start_time));
    closes.push(close);
  }
  return output.length > 0 ? output : [syntheticBar(bars[0], Number(bars[0].close), Number(bars[0].close), Number(bars[0].close), Number(bars[0].close), bars[0].start_time)];
}

export function reversalBars(bars: readonly MarketBar[]): MarketBar[] {
  if (bars.length === 0) return [];
  const reversal = typicalRange(bars);
  let anchor = Number(bars[0].close);
  let direction = 0;
  const output: MarketBar[] = [];
  for (const source of bars) {
    const close = Number(source.close);
    const delta = close - anchor;
    if (Math.abs(delta) < reversal) continue;
    const nextDirection = delta > 0 ? 1 : -1;
    if (direction !== 0 && nextDirection !== direction && Math.abs(delta) < reversal * 2) continue;
    output.push(syntheticBar(source, anchor, Math.max(anchor, close), Math.min(anchor, close), close, source.start_time));
    anchor = close;
    direction = nextDirection;
  }
  return output.length > 0 ? output : [syntheticBar(bars[0], anchor, anchor, anchor, anchor, bars[0].start_time)];
}

function displayBars(bars: readonly MarketBar[], type: TradingChartType): readonly MarketBar[] {
  if (type === 'heikin-ashi') return heikinAshiBars(bars);
  if (type === 'renko') return renkoBars(bars);
  if (type === 'range') return rangeBars(bars);
  if (type === 'line-break') return lineBreakBars(bars);
  if (type === 'kagi' || type === 'point-figure') return reversalBars(bars);
  return bars;
}

function indicatorColor(output: IndicatorOutput): string {
  if (output.key.startsWith('bull-market-band:sma')) return '#ff6b6b';
  if (output.key.startsWith('bull-market-band:ema')) return '#40ad50';
  if (output.key.includes('support') || output.key.includes('value-area-low')) return '#20c997';
  if (output.key.includes('resistance') || output.key.includes('value-area-high')) return '#ff6b6b';
  if (output.key.includes('poc')) return '#ffd43b';
  if (output.key.includes('upper')) return '#74c0fc';
  if (output.key.includes('lower')) return '#74c0fc';
  if (output.key.includes('middle')) return '#a5d8ff';
  if (output.key.includes('signal')) return '#ff922b';
  if (output.key.includes('histogram')) return '#20c997';
  if (output.key.startsWith('atr')) return '#ffa94d';
  if (output.key.startsWith('vwap')) return '#ffd43b';
  if (output.key.startsWith('sma')) return '#ffd43b';
  if (output.key.startsWith('ema')) return '#e599f7';
  return '#5c7cfa';
}

/** The pane an output is drawn in: null for the price pane, else its indicator's (or its source's) pane id. */
export function indicatorPaneId(output: Pick<IndicatorOutput, 'pane' | 'paneOf' | 'key'>): string | null {
  if (output.pane === 0) return null;
  // An indicator on another indicator shares its source's pane (TVP-6.5).
  return output.paneOf ?? output.key.split(':', 1)[0] ?? null;
}

/** Levels step between values; markers hide the line (the markers carry the points). */
function indicatorLineShape(output: IndicatorOutput): { lineType?: LineType; lineVisible?: boolean; pointMarkersVisible?: boolean; crosshairMarkerVisible?: boolean } {
  if (output.render === 'levels') return { lineType: LineType.WithSteps, lineVisible: true };
  if (output.render === 'markers') return { lineVisible: false, pointMarkersVisible: false, crosshairMarkerVisible: false };
  return {};
}

function indicatorLineStyle(value: IndicatorOutput['lineStyle']): LineStyle | undefined {
  if (value === 'dotted') return LineStyle.Dotted;
  if (value === 'dashed') return LineStyle.Dashed;
  if (value === 'large-dashed') return LineStyle.LargeDashed;
  if (value === 'sparse-dotted') return LineStyle.SparseDotted;
  if (value === 'solid') return LineStyle.Solid;
  return undefined;
}

/**
 * Chart data of a line output. Level outputs (pivots, boxes) get whitespace on the chart's bars that have no value between their
 * first and last point, so separate levels are not joined by a diagonal; marker outputs are drawn by `indicatorMarkers` instead.
 */
/**
 * An indicator output on the chart's bars (`alignIndicatorPoints`), the same output when its points are already
 * on bar times. Raw bar times, not the plotted times: indicators compute on the bars on every chart type.
 */
export function indicatorOutputOnBars(output: IndicatorOutput, barTimes: readonly UTCTimestamp[], stepSeconds: number | null): IndicatorOutput {
  // Per-bar colours and labels are on bar times already (they paint those bars).
  if (output.kind === 'background' || output.kind === 'bar-colors') return output;
  const points = output.points.map((point) => ({ time: timestamp(point.time), value: point.value }));
  const aligned = alignIndicatorPoints(points, barTimes, stepSeconds);
  if (aligned.length === points.length && aligned.every((point, index) => point.time === points[index].time)) return output;
  return { ...output, points: aligned.map((point) => ({ time: new Date(point.time * 1_000).toISOString(), value: point.value })) };
}

export function indicatorLineData(
  output: IndicatorOutput,
  barTimes: readonly UTCTimestamp[],
  multiplier = 1,
): Array<LineData<UTCTimestamp> | WhitespaceData<UTCTimestamp>> {
  const scale = output.pane === 0 && output.kind !== 'background' ? multiplier : 1;
  const points = output.points.map((point) => ({ time: timestamp(point.time), value: point.value * scale, ...(point.color ? { color: point.color } : {}) }));
  if (output.render !== 'levels' || points.length < 2) return points;
  const first = points[0].time;
  const last = points[points.length - 1].time;
  const valued = new Set<number>(points.map((point) => point.time));
  const gaps = barTimes.filter((time) => time > first && time < last && !valued.has(time)).map((time) => ({ time }));
  return [...points, ...gaps].sort((left, right) => left.time - right.time);
}

/** `rgba(r, g, b, a)` as `rgb(r, g, b)`; any other colour as it is. */
export function opaqueColor(color: string): string {
  const match = /^rgba\(\s*([^,]+),\s*([^,]+),\s*([^,]+),[^)]*\)$/.exec(color.trim());
  return match ? `rgb(${match[1]}, ${match[2]}, ${match[3]})` : color;
}

/** A background output's labels (Trading Sessions' names) as text marks at the top of the pane. */
export function backgroundLabelMarkers(output: IndicatorOutput): SeriesMarker<UTCTimestamp>[] {
  return output.points.flatMap((point) => (point.label ? [{
    time: timestamp(point.time),
    position: 'aboveBar' as const,
    shape: 'square' as const,
    size: 0,
    // The text takes the marker's colour: the shading's colour made opaque, so a 10% tint still gives readable text.
    color: opaqueColor(point.color ?? output.color ?? '#787b86'),
    text: point.label,
  }] : []));
}

/** The bar colours of `bar-colors` outputs, by bar time (TVP-6.2). A later output wins on a bar both colour. */
export function indicatorBarColors(outputs: readonly IndicatorOutput[]): Map<number, string> {
  const colors = new Map<number, string>();
  for (const output of outputs) {
    if (output.kind !== 'bar-colors' || output.visible === false) continue;
    for (const point of output.points) if (point.color) colors.set(timestamp(point.time), point.color);
  }
  return colors;
}

/** The average close of the bars in a visible logical range (Visible Average Price), or null without bars in view. */
export function visibleAverageClose(bars: readonly MarketBar[], range: { from: number; to: number } | null): number | null {
  if (!range || bars.length === 0) return null;
  const from = Math.max(0, Math.ceil(range.from));
  const to = Math.min(bars.length - 1, Math.floor(range.to));
  if (to < from) return null;
  let total = 0;
  for (let index = from; index <= to; index += 1) total += Number(bars[index].close);
  return total / (to - from + 1);
}

/** Point signals (divergences, reversals, fractals) as series markers at their price. */
export function indicatorMarkers(output: IndicatorOutput, multiplier = 1): SeriesMarker<UTCTimestamp>[] {
  const scale = output.pane === 0 ? multiplier : 1;
  const { markerPosition: position, markerText: text } = output;
  // Above or below the bar (candlestick patterns), with the pattern's short name.
  if (position) {
    return output.points.map((point) => ({
      time: timestamp(point.time), position, shape: output.marker ?? 'circle', color: point.color ?? output.color ?? indicatorColor(output), size: 1,
      ...(point.label ?? text ? { text: point.label ?? text } : {}),
    }));
  }
  // A point's own colour and label win (script shapes coloured per bar, script labels; TVP-11.1).
  return output.points.map((point) => ({
    time: timestamp(point.time),
    position: 'atPriceMiddle',
    price: point.value * scale,
    shape: output.marker ?? 'circle',
    color: point.color ?? output.color ?? indicatorColor(output),
    size: 1,
    ...(point.label ? { text: point.label } : {}),
  }));
}

function indicatorPriceFormat(precision: number | null | undefined): { priceFormat?: { type: 'price'; precision: number; minMove: number } } {
  if (precision === null || precision === undefined) return {};
  return { priceFormat: { type: 'price', precision, minMove: 10 ** -precision } };
}

/** The kind of series an indicator output is drawn on; a change of kind replaces the series. */
function seriesKind(output: IndicatorOutput): 'candles' | 'bars' | 'fill' | 'columns' | 'line' {
  if (output.kind === 'candles') return output.barStyle === 'bars' ? 'bars' : 'candles';
  if (output.kind === 'fill') return 'fill';
  return output.kind === 'histogram' || output.kind === 'background' ? 'columns' : 'line';
}

export class TradingChartAdapter {
  private readonly chart: IChartApi;
  private priceSeries: PriceSeries;
  private readonly volumeSeries: ISeriesApi<'Histogram'>;
  private readonly indicatorSeries = new Map<string, IndicatorSeries>();
  private readonly indicatorSeriesPanes = new Map<string, number>();
  /** Each indicator series' kind: an output that changes kind gets a new series of the right type. */
  private readonly indicatorSeriesKinds = new Map<string, 'line' | 'columns' | 'candles' | 'bars' | 'fill'>();
  private readonly indicatorMarkerPlugins = new Map<string, ISeriesMarkersPluginApi<Time>>();
  private readonly comparisonSeries = new Map<string, ISeriesApi<'Line'>>();
  private readonly comparisonSeriesPanes = new Map<string, number>();
  private readonly comparisonSeriesOptions = new Map<string, string>();
  private readonly indicatorScaleRanges = new Map<string, { from: number; to: number }>();
  private indicatorPaneIds: string[] = [];
  private readonly indicatorPaneHeights = new Map<string, number>();
  private readonly restoredPaneHeights = new Map<string, number>();
  private fullscreenIndicatorId: string | null = null;
  private fullscreenMainPane = false;
  private readonly fullscreenPaneStretchFactors = new Map<number, number>();
  private maxZoomOutRange: LogicalRange | null = null;
  private rightOffset = DEFAULT_TRADING_RIGHT_OFFSET;
  private priceScaleSide: TradingPriceScaleSide = 'right';
  private priceScaleLabelsVisible = true;
  private priceScaleLinesVisible = true;
  private gridLinesVisible = true;
  private latestValueLabelVisible = true;
  private chartType: TradingChartType;
  private readonly revisions = new Map<number, number>();
  private readonly viewportListeners = new Set<() => void>();
  private readonly priceSeriesPrimitives = new Set<ISeriesPrimitive<Time>>();
  /** Script fills (TVP-11.1), by output key: the primitive on each fill's hidden series. */
  private readonly fillPrimitives = new Map<string, FillBandPrimitive>();
  /** Bar-click listeners (time sync), also told of clicks the pan handling takes (`clickAt`). */
  private readonly barClickListeners = new Set<(timeMs: number) => void>();
  private readonly comparisonViewportHandler = () => {
    this.renderComparisonSeries();
    this.renderViewportAverage();
  };
  /** Bar colours from `bar-colors` outputs (Bollinger Bars), applied to the price series' data. */
  private barColors = new Map<number, string>();
  /** The Visible Average Price output and its price line. */
  private viewportAverageOutput: IndicatorOutput | null = null;
  private viewportAverageLine: { series: PriceSeries; line: IPriceLine } | null = null;
  private comparisonRenderInProgress = false;
  private comparisonRenderPending = false;
  private bars: MarketBar[] = [];
  /** The price series' plotted times (bar times, or brick times on Renko-type charts); comparisons align to them. */
  private priceTimes: UTCTimestamp[] = [];
  /** Every time point the chart plots (all series), as Lightweight Charts indexes them; null = rebuild. */
  private chartTimeIndex: DrawingTimeIndex | null = null;
  /** Per series, the times (UTC seconds) it was given, whitespace items included, as Lightweight Charts indexes them. */
  private readonly seriesTimes = new Map<string, readonly number[]>();
  /** The loaded bars' own time index (for `drawingBars`); null = rebuild. */
  private rawBarIndex: DrawingTimeIndex | null = null;
  private barSeries: DrawingBarSeries | null = null;
  private barSeriesCache: (DrawingBar | undefined)[] = [];
  private indicatorOutputs: IndicatorOutput[] = [];
  private comparisonData: TradingComparisonData[] = [];
  private priceScaleMultiplier = 1;
  private sessionPriceLineConfig: TradingSessionPriceLine | null = null;
  private sessionPriceLine: { series: PriceSeries; line: IPriceLine } | null = null;
  private destroyed = false;

  constructor(container: HTMLElement, chartType: TradingChartType = 'candlestick') {
    this.chartType = chartType;
    this.chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#9eacbd' },
      crosshair: { mode: CrosshairMode.Normal },
      grid: { vertLines: { color: 'rgba(120,145,170,.08)' }, horzLines: { color: 'rgba(120,145,170,.08)' } },
      rightPriceScale: { borderColor: 'rgba(140,160,180,.18)' },
      timeScale: {
        borderColor: 'rgba(140,160,180,.18)',
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: false,
        fixRightEdge: false,
        rightOffset: DEFAULT_TRADING_RIGHT_OFFSET,
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: { time: false, price: false },
        axisDoubleClickReset: { time: true, price: true },
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: false,
        horzTouchDrag: true,
        vertTouchDrag: true,
      },
      kineticScroll: { mouse: true, touch: true },
    });
    this.setTimezone('UTC');
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.comparisonViewportHandler);
    this.priceSeries = this.createPriceSeries(chartType);
    this.volumeSeries = this.chart.addSeries(HistogramSeries, { priceScaleId: 'volume', priceFormat: { type: 'volume' }, priceLineVisible: false });
    this.volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  }

  private createPriceSeries(type: TradingChartType): PriceSeries {
    if (isLineType(type)) {
      return this.chart.addSeries(LineSeries, {
        color: '#4dabf7',
        lineWidth: 1,
        lineType: type === 'step-line' ? LineType.WithSteps : LineType.Simple,
        pointMarkersVisible: type === 'line-with-markers',
      });
    }
    if (isAreaType(type)) return this.chart.addSeries(AreaSeries, { lineColor: '#4dabf7', topColor: 'rgba(77,171,247,.35)', bottomColor: 'rgba(77,171,247,.02)', lineWidth: 1 });
    if (type === 'baseline') return this.chart.addSeries(BaselineSeries, { baseValue: { type: 'price', price: 0 }, topLineColor: '#20c997', bottomLineColor: '#ff6b6b', lineWidth: 1 });
    if (isColumnType(type)) return this.chart.addSeries(HistogramSeries, {
      priceScaleId: isVolumeColumnType(type) ? 'volume' : 'right',
      priceFormat: isVolumeColumnType(type) ? { type: 'volume' } : { type: 'price', precision: 2, minMove: 0.01 },
      base: 0,
    });
    if (isBarType(type)) return this.chart.addSeries(BarSeries, {
      upColor: '#20c997',
      downColor: '#ff6b6b',
      openVisible: type !== 'high-low',
      thinBars: type === 'high-low',
    });
    if (type === 'hollow-candles') {
      return this.chart.addSeries(CandlestickSeries, {
        upColor: 'transparent',
        downColor: '#ff6b6b',
        borderVisible: true,
        borderUpColor: '#20c997',
        borderDownColor: '#ff6b6b',
        wickUpColor: '#20c997',
        wickDownColor: '#ff6b6b',
      });
    }
    return this.chart.addSeries(CandlestickSeries, { upColor: '#20c997', downColor: '#ff6b6b', borderVisible: false, wickUpColor: '#20c997', wickDownColor: '#ff6b6b' });
  }

  private setPriceData(bars: readonly MarketBar[]): void {
    const visibleBars = displayBars(bars, this.chartType);
    // Synthetic bars (Renko, range, line break, Kagi, P&F) plot their own times.
    this.priceTimes = visibleBars.map((bar) => timestamp(bar.start_time));
    this.noteSeriesTimes('price', this.priceTimes.map((time) => ({ time })));
    if (isCandlestickType(this.chartType)) (this.priceSeries as ISeriesApi<'Candlestick'>).setData(visibleBars.map((bar) => this.coloredBar(this.chartType === 'volume-candles' ? volumeCandleData(bar, visibleBars, this.priceScaleMultiplier) : candlestickData(bar, this.priceScaleMultiplier))));
    else if (isBarType(this.chartType)) (this.priceSeries as ISeriesApi<'Bar'>).setData(visibleBars.map((bar) => this.coloredBar(candlestickData(bar, this.priceScaleMultiplier))));
    else if (isColumnType(this.chartType)) (this.priceSeries as ISeriesApi<'Histogram'>).setData(visibleBars.map((bar) => isVolumeColumnType(this.chartType) ? volumeData(bar) : columnData(bar, this.priceScaleMultiplier)));
    else if (isAreaType(this.chartType)) (this.priceSeries as ISeriesApi<'Area'>).setData(visibleBars.map((bar) => lineData(bar, this.priceScaleMultiplier)));
    else if (this.chartType === 'baseline') (this.priceSeries as ISeriesApi<'Baseline'>).setData(visibleBars.map((bar) => lineData(bar, this.priceScaleMultiplier)));
    else (this.priceSeries as ISeriesApi<'Line'>).setData(visibleBars.map((bar) => lineData(bar, this.priceScaleMultiplier)));
  }

  /** A candle or bar with the colour an indicator gave it (TVP-6.2), else as it is. */
  private coloredBar<T extends CandlestickData<UTCTimestamp>>(data: T): T {
    const color = this.barColors.size > 0 ? this.barColors.get(data.time as number) : undefined;
    return color ? { ...data, color, wickColor: color, borderColor: color } : data;
  }

  /** Applies the `bar-colors` outputs; redraws the bars only when the colours changed. */
  private applyIndicatorBarColors(outputs: readonly IndicatorOutput[]): void {
    const next = indicatorBarColors(outputs);
    const same = next.size === this.barColors.size && [...next].every(([time, color]) => this.barColors.get(time) === color);
    if (same) return;
    this.barColors = next;
    if (this.bars.length > 0) this.setPriceData(this.bars);
  }

  /** Background outputs go behind the price series (they're added after it, and a chart-type change re-adds it). */
  private sendBackgroundsBack(): void {
    for (const series of this.indicatorSeries.values()) {
      if (String(series.options().priceScaleId ?? '').startsWith('background:')) series.setSeriesOrder(0);
    }
  }

  /** The Visible Average Price line: the average close of the bars in view, kept as the view moves (TVP-6.2). */
  private renderViewportAverage(): void {
    if (this.destroyed) return;
    const output = this.viewportAverageOutput;
    const average = output ? visibleAverageClose(this.bars, this.chart.timeScale().getVisibleLogicalRange()) : null;
    const current = this.viewportAverageLine;
    if (average === null || !output) {
      if (current) current.series.removePriceLine(current.line);
      this.viewportAverageLine = null;
      return;
    }
    const options = {
      price: average * this.priceScaleMultiplier,
      color: output.color ?? '#ff9800',
      lineWidth: 1 as const,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: output.title,
    };
    if (current && current.series === this.priceSeries) {
      current.line.applyOptions(options);
      return;
    }
    if (current) current.series.removePriceLine(current.line);
    this.viewportAverageLine = { series: this.priceSeries, line: this.priceSeries.createPriceLine(options) };
  }

  private updatePriceData(bar: MarketBar): void {
    if (this.chartType === 'heikin-ashi' || SYNTHETIC_BAR_CHART_TYPES.has(this.chartType)) {
      this.setPriceData(this.bars);
      return;
    }
    if (isCandlestickType(this.chartType)) (this.priceSeries as ISeriesApi<'Candlestick'>).update(this.coloredBar(this.chartType === 'volume-candles' ? volumeCandleData(bar, this.bars, this.priceScaleMultiplier) : candlestickData(bar, this.priceScaleMultiplier)));
    else if (isBarType(this.chartType)) (this.priceSeries as ISeriesApi<'Bar'>).update(this.coloredBar(candlestickData(bar, this.priceScaleMultiplier)));
    else if (isColumnType(this.chartType)) (this.priceSeries as ISeriesApi<'Histogram'>).update(isVolumeColumnType(this.chartType) ? volumeData(bar) : columnData(bar, this.priceScaleMultiplier));
    else if (isAreaType(this.chartType)) (this.priceSeries as ISeriesApi<'Area'>).update(lineData(bar, this.priceScaleMultiplier));
    else if (this.chartType === 'baseline') (this.priceSeries as ISeriesApi<'Baseline'>).update(lineData(bar, this.priceScaleMultiplier));
    else (this.priceSeries as ISeriesApi<'Line'>).update(lineData(bar, this.priceScaleMultiplier));
  }

  setChartType(type: TradingChartType, bars: readonly MarketBar[]): void {
    this.assertActive();
    if (type === this.chartType) return;
    for (const primitive of this.priceSeriesPrimitives) this.priceSeries.detachPrimitive(primitive);
    this.chart.removeSeries(this.priceSeries);
    this.chartType = type;
    this.sessionPriceLine = null;
    this.priceSeries = this.createPriceSeries(type);
    this.priceSeries.applyOptions({ lastValueVisible: this.latestValueLabelVisible });
    for (const primitive of this.priceSeriesPrimitives) this.priceSeries.attachPrimitive(primitive);
    this.renderSessionPriceLine();
    this.viewportAverageLine = null;
    this.sendBackgroundsBack();
    this.setBars(bars, false);
    this.renderViewportAverage();
  }

  setAppearance(appearance: TradingChartAppearance): void {
    this.assertActive();
    const light = appearance === 'light';
    const textColor = light ? '#536273' : '#9eacbd';
    const gridColor = light ? 'rgba(91,111,132,.14)' : 'rgba(120,145,170,.08)';
    const borderColor = light ? 'rgba(91,111,132,.28)' : 'rgba(140,160,180,.18)';
    this.chart.applyOptions({
      layout: { background: { color: 'transparent' }, textColor },
      grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      leftPriceScale: { borderColor },
      rightPriceScale: { borderColor },
      timeScale: { borderColor },
    });
  }

  setTimezone(timeZone: string): void {
    this.assertActive();
    this.chart.applyOptions({
      localization: {
        timeFormatter: (time: Time) => formatTradingChartTime(time, timeZone, this.intervalStepSeconds()),
      },
      timeScale: {
        tickMarkFormatter: (time: Time) => formatTradingChartTick(time, timeZone),
      },
    });
  }

  setBars(bars: readonly MarketBar[], fit = true): void {
    this.assertActive();
    const visibleRange = fit ? null : this.chart.timeScale().getVisibleLogicalRange();
    this.bars = normalizeChartBars(bars);
    // New bars (another symbol or interval): no indicator colours until the indicators are recomputed for them.
    if (fit) this.barColors = new Map();
    this.invalidateDrawingTimes();
    this.revisions.clear();
    for (const bar of this.bars) this.revisions.set(timestamp(bar.start_time), bar.ingestion_revision);
    this.setPriceData(this.bars);
    const volume = this.bars.map(volumeData);
    this.volumeSeries.setData(volume);
    this.noteSeriesTimes('volume', volume);
    this.renderComparisonSeries();
    if (fit) this.fitContent();
    else if (visibleRange) this.chart.timeScale().setVisibleLogicalRange(visibleRange);
  }

  setIndicators(bars: readonly MarketBar[], indicators: readonly CoreIndicatorInstance[]): void {
    const normalizedBars = normalizeChartBars(bars);
    const outputs = indicators.filter((item) => item.enabled && item.visible !== false).flatMap((item) => indicatorOutputs(normalizedBars, item));
    this.setIndicatorOutputs(outputs);
  }

  setComparisonData(items: readonly TradingComparisonData[]): void {
    this.assertActive();
    this.comparisonData = items.map((item) => ({ ...item, bars: normalizeChartBars(item.bars) }));
    this.renderComparisonSeries();
  }

  private renderComparisonSeries(): void {
    if (this.destroyed) return;
    if (this.comparisonRenderInProgress) {
      this.comparisonRenderPending = true;
      return;
    }
    this.comparisonRenderInProgress = true;
    try {
      this.renderComparisonSeriesInternal();
    } finally {
      this.comparisonRenderInProgress = false;
      if (this.comparisonRenderPending && !this.destroyed) {
        this.comparisonRenderPending = false;
        this.renderComparisonSeries();
      }
    }
  }

  private renderComparisonSeriesInternal(): void {
    // Lightweight Charts re-hit-tests the active crosshair when a series is
    // moved or its options change. Clear it first so a transient empty
    // histogram point cannot crash the chart during a zoom/pan redraw.
    this.chart.clearCrosshairPosition();
    const paneItems = this.comparisonData.filter((item) => item.placement === 'pane');
    const enabled = new Set(this.comparisonData.map((item) => item.instrumentId));
    for (const [instrumentId, series] of this.comparisonSeries) {
      if (!enabled.has(instrumentId)) {
        this.removeComparisonSeries(instrumentId, series);
      }
    }

    let paneOffset = 0;
    const visibleRange = this.chart.timeScale().getVisibleLogicalRange();
    const anchorIndex = Math.max(0, Math.floor(visibleRange?.from ?? 0));
    const anchorTime = this.bars[anchorIndex]?.start_time ?? this.bars[0]?.start_time ?? null;
    const primaryRange = visiblePrimaryPriceRange(this.bars, visibleRange, this.priceScaleMultiplier);
    const percentValues = this.comparisonData.flatMap((item) => {
      if (item.placement !== 'percent') return [];
      const anchorClose = closeAtOrBefore(item.bars, anchorTime);
      if (anchorClose <= 0) return [];
      return item.bars.flatMap((bar) => {
        const time = Date.parse(bar.start_time);
        const close = Number(bar.close);
        if (!Number.isFinite(time) || time < primaryRange.fromTime || time > primaryRange.toTime || !Number.isFinite(close)) return [];
        return [(close / anchorClose - 1) * 100];
      });
    });
    const percentMin = Math.min(0, ...percentValues);
    const percentMax = Math.max(0, ...percentValues);
    const percentSpan = Math.max(Number.EPSILON, percentMax - percentMin);
    const priceSpan = primaryRange.max - primaryRange.min;
    for (const item of this.comparisonData) {
      const paneIndex = item.placement === 'pane' ? this.indicatorPaneIds.length + 1 + paneOffset++ : 0;
      const priceScaleId = item.placement === 'price-scale'
        ? `comparison:${item.instrumentId.replace(/[^a-z0-9_-]/gi, '_')}`
        : item.placement === 'pane' ? 'right' : this.priceScaleSide;
      const comparisonAnchorClose = closeAtOrBefore(item.bars, anchorTime);
      let series = this.comparisonSeries.get(item.instrumentId);
      let created = false;
      if (!series) {
        series = this.chart.addSeries(LineSeries, {
          color: item.color,
          lineWidth: 2,
          title: item.label,
          lastValueVisible: false,
          priceLineVisible: false,
          priceScaleId,
        }, paneIndex);
        this.comparisonSeries.set(item.instrumentId, series);
        this.comparisonSeriesPanes.set(item.instrumentId, paneIndex);
        created = true;
      } else if (this.comparisonSeriesPanes.get(item.instrumentId) !== paneIndex) {
        series.moveToPane(paneIndex);
        this.comparisonSeriesPanes.set(item.instrumentId, paneIndex);
      }
      const optionsKey = `${item.color}|${item.label}|${item.placement}|${priceScaleId}`;
      if (created || this.comparisonSeriesOptions.get(item.instrumentId) !== optionsKey) {
        if (!created) series.applyOptions({ color: item.color, title: item.label, priceScaleId, lastValueVisible: false, priceLineVisible: false, lineWidth: 2 });
        this.comparisonSeriesOptions.set(item.instrumentId, optionsKey);
        if (item.placement === 'price-scale') {
          series.priceScale().applyOptions({ visible: true, borderVisible: true, ticksVisible: true, minimumWidth: 58 });
        } else if (item.placement === 'pane') {
          series.priceScale().applyOptions({ visible: true, borderVisible: true, ticksVisible: true, minimumWidth: 58, scaleMargins: { top: 0.1, bottom: 0.1 } });
        }
      }
      const values = item.visible ? alignSeriesToTimes(item.bars.flatMap((bar) => {
        const close = Number(bar.close);
        if (!Number.isFinite(close) || !Number.isFinite(Date.parse(bar.start_time))) return [];
        const percentChange = comparisonAnchorClose > 0 ? (close / comparisonAnchorClose - 1) * 100 : 0;
        const value = item.placement === 'percent'
          ? primaryRange.min + ((percentChange - percentMin) / percentSpan) * priceSpan
          : close;
        return [{ time: timestamp(bar.start_time), value }];
      }), this.priceTimes) : [];
      series.setData(values);
      this.noteSeriesTimes(`comparison:${item.instrumentId}`, values);
    }

    const lastComparisonPane = this.indicatorPaneIds.length + paneItems.length;
    for (let index = this.chart.panes().length - 1; index > lastComparisonPane; index -= 1) {
      const pane = this.chart.panes()[index];
      if (pane?.getSeries().length === 0) this.chart.removePane(index);
    }
  }

  private removeComparisonSeries(instrumentId: string, series: ISeriesApi<'Line'>): void {
    this.comparisonSeries.delete(instrumentId);
    this.comparisonSeriesPanes.delete(instrumentId);
    this.comparisonSeriesOptions.delete(instrumentId);
    this.forgetSeriesTimes(`comparison:${instrumentId}`);
    try {
      this.chart.removeSeries(series);
    } catch (error) {
      if (!(error instanceof Error) || !error.message.includes('Series not found')) throw error;
    }
  }

  /** A fill's series (TVP-11.1): a hidden line in the pane, carrying the band primitive that paints the fill under it. */
  private addFillSeries(key: string, priceScaleId: string, paneIndex: number): ISeriesApi<'Line'> {
    const series = this.chart.addSeries(LineSeries, {
      color: 'rgba(0, 0, 0, 0)', lineVisible: false, title: '', lastValueVisible: false, priceLineVisible: false,
      crosshairMarkerVisible: false, priceScaleId,
    }, paneIndex);
    const primitive = new FillBandPrimitive();
    series.attachPrimitive(primitive);
    this.fillPrimitives.set(key, primitive);
    series.setSeriesOrder(0);
    return series;
  }

  private setFillData(output: IndicatorOutput, series: ISeriesApi<'Line'>): void {
    const scale = this.priceScaleMultiplier;
    const band = output.points.map((point) => ({
      time: timestamp(point.time) as Time,
      upper: (point.high ?? Number.NaN) * scale,
      lower: (point.low ?? Number.NaN) * scale,
      color: point.color ?? output.color ?? 'rgba(41, 98, 255, 0.2)',
    }));
    // The hidden line runs through the middle of the band, so the bars and crosshair know its times.
    const data = band.filter((point) => Number.isFinite(point.upper) && Number.isFinite(point.lower))
      .map((point) => ({ time: point.time, value: (point.upper + point.lower) / 2 }));
    this.noteSeriesTimes(`indicator:${output.key}`, data);
    series.setData(data);
    this.fillPrimitives.get(output.key)?.setPoints(band);
  }

  /** Candles (Volume Delta, CVD; a script's plotcandle and plotbar): on the chart's bars already, each with its own open, high and low, and a script's colour per bar. */
  private setCandleData(output: IndicatorOutput, series: ISeriesApi<'Candlestick'>, priceScaleId: string): void {
    const data = output.points.map((point) => ({
      time: timestamp(point.time), open: point.open ?? point.value, high: point.high ?? point.value, low: point.low ?? point.value, close: point.value,
      ...(point.color ? { color: point.color, wickColor: point.color, borderColor: point.color } : {}),
    }));
    series.applyOptions({ title: output.valuesInStatusLine === false ? '' : output.title, lastValueVisible: output.labelsOnPriceScale === true, priceScaleId, ...indicatorPriceFormat(output.precision) });
    this.noteSeriesTimes(`indicator:${output.key}`, data);
    series.setData(data);
  }

  setIndicatorOutputs(outputs: readonly IndicatorOutput[]): void {
    this.assertActive();
    this.indicatorOutputs = [...outputs];
    const paneIds = outputs.reduce<string[]>((ids, output) => {
      const paneId = indicatorPaneId(output);
      if (paneId && !ids.includes(paneId)) ids.push(paneId);
      return ids;
    }, []);

    const enabled = new Set(outputs.filter((output) => {
      const known = this.indicatorSeriesKinds.get(output.key);
      return known === undefined || known === seriesKind(output);
    }).map((output) => output.key));
    for (const [key, series] of this.indicatorSeries) {
      if (!enabled.has(key)) {
        this.indicatorMarkerPlugins.get(key)?.detach();
        this.indicatorMarkerPlugins.delete(key);
        this.chart.removeSeries(series);
        this.forgetSeriesTimes(`indicator:${key}`);
        this.indicatorSeries.delete(key);
        this.indicatorSeriesPanes.delete(key);
        this.indicatorSeriesKinds.delete(key);
        this.fillPrimitives.delete(key);
      }
    }
    const barTimes = this.bars.map((bar) => timestamp(bar.start_time));
    this.applyIndicatorBarColors(outputs);
    this.viewportAverageOutput = outputs.find((output) => output.kind === 'viewport-average' && output.visible !== false) ?? null;
    this.renderViewportAverage();
    for (const output of outputs) {
      // Bar colours, the visible average and tables (drawn over the chart) have no series of their own.
      if (output.visible === false || output.kind === 'bar-colors' || output.kind === 'viewport-average' || output.kind === 'table') continue;
      const columns = output.kind === 'histogram' || output.kind === 'background';
      const candles = output.kind === 'candles';
      const paneId = indicatorPaneId(output);
      const paneIndex = paneId === null ? 0 : paneIds.indexOf(paneId) + 1;
      // A custom price-scale ID creates an overlay scale in Lightweight Charts.
      // Pane indicators must use the pane's native right scale for its labels
      // and tick marks to render inside that pane.
      // A background has its own hidden scale, so its columns (value 1) fill the pane's height.
      const priceScaleId = output.kind === 'background' ? `background:${output.key}` : paneId === null ? this.priceScaleSide : 'right';
      let series = this.indicatorSeries.get(output.key);
      if (!series) {
        const color = output.color ?? indicatorColor(output);
        const lineStyle = indicatorLineStyle(output.lineStyle);
        const commonOptions = {
          color,
          title: output.valuesInStatusLine === false ? '' : output.title,
          lastValueVisible: output.labelsOnPriceScale === true,
          priceLineVisible: false,
          ...indicatorPriceFormat(output.precision),
        };
        series = output.kind === 'fill'
          ? this.addFillSeries(output.key, priceScaleId, paneIndex)
          : candles && output.barStyle === 'bars'
          ? this.chart.addSeries(BarSeries, { ...commonOptions, priceScaleId, upColor: INDICATOR_CANDLE_UP, downColor: INDICATOR_CANDLE_DOWN }, paneIndex)
          : candles
          ? this.chart.addSeries(CandlestickSeries, {
            ...commonOptions, priceScaleId, upColor: INDICATOR_CANDLE_UP, downColor: INDICATOR_CANDLE_DOWN, wickUpColor: INDICATOR_CANDLE_UP,
            wickDownColor: INDICATOR_CANDLE_DOWN, borderVisible: false,
          }, paneIndex)
          : columns
          ? this.chart.addSeries(HistogramSeries, { ...commonOptions, priceScaleId, ...(output.kind === 'background' ? { title: '', base: 0 } : {}) }, paneIndex)
          : this.chart.addSeries(LineSeries, { ...commonOptions, ...indicatorLineShape(output), ...(lineStyle === undefined ? {} : { lineStyle }), lineWidth: output.lineWidth ?? 1, priceScaleId }, paneIndex);
        if (output.kind === 'background') {
          series.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
          series.setSeriesOrder(0);
        }
        this.indicatorSeries.set(output.key, series);
        this.indicatorSeriesPanes.set(output.key, paneIndex);
        this.indicatorSeriesKinds.set(output.key, seriesKind(output));
      } else if (this.indicatorSeriesPanes.get(output.key) !== paneIndex) {
        series.moveToPane(paneIndex);
        this.indicatorSeriesPanes.set(output.key, paneIndex);
      }
      if (output.kind === 'fill') {
        this.setFillData(output, series as ISeriesApi<'Line'>);
        continue;
      }
      if (candles) {
        this.setCandleData(output, series as ISeriesApi<'Candlestick'>, priceScaleId);
        continue;
      }
      if (columns) {
        (series as ISeriesApi<'Histogram'>).applyOptions({
          color: output.color ?? indicatorColor(output),
          title: output.valuesInStatusLine === false || output.kind === 'background' ? '' : output.title,
          lastValueVisible: output.labelsOnPriceScale === true,
          priceLineVisible: false,
          priceScaleId,
          ...indicatorPriceFormat(output.precision),
        });
      } else {
        const lineStyle = indicatorLineStyle(output.lineStyle);
        (series as ISeriesApi<'Line'>).applyOptions({
          color: output.color ?? indicatorColor(output),
          title: output.valuesInStatusLine === false ? '' : output.title,
          lastValueVisible: output.labelsOnPriceScale === true,
          priceLineVisible: false,
          lineWidth: output.lineWidth ?? 1,
          priceScaleId,
          ...indicatorLineShape(output),
          ...(lineStyle === undefined ? {} : { lineStyle }),
          ...indicatorPriceFormat(output.precision),
        });
      }
      const data = indicatorLineData(indicatorOutputOnBars(output, barTimes, this.intervalStepSeconds()), barTimes, this.priceScaleMultiplier);
      this.noteSeriesTimes(`indicator:${output.key}`, data);
      if (columns) {
        (series as ISeriesApi<'Histogram'>).setData(data);
        const labels = output.kind === 'background' ? backgroundLabelMarkers(output) : [];
        const labelPlugin = this.indicatorMarkerPlugins.get(output.key);
        if (labels.length > 0 && labelPlugin) labelPlugin.setMarkers(labels);
        else if (labels.length > 0) this.indicatorMarkerPlugins.set(output.key, createSeriesMarkers(series as ISeriesApi<'Histogram'>, labels) as ISeriesMarkersPluginApi<Time>);
        else if (labelPlugin) { labelPlugin.detach(); this.indicatorMarkerPlugins.delete(output.key); }
        continue;
      }
      (series as ISeriesApi<'Line'>).setData(data);
      const markers = output.render === 'markers' ? indicatorMarkers(output, this.priceScaleMultiplier) : null;
      const plugin = this.indicatorMarkerPlugins.get(output.key);
      if (markers && plugin) plugin.setMarkers(markers);
      else if (markers) this.indicatorMarkerPlugins.set(output.key, createSeriesMarkers(series as ISeriesApi<'Line'>, markers) as ISeriesMarkersPluginApi<Time>);
      else if (plugin) { plugin.detach(); this.indicatorMarkerPlugins.delete(output.key); }
    }
    this.applyIndicatorPanes(paneIds);
  }

  /** Indicator panes after their outputs change: scales, kept heights and ranges, and panes left empty. */
  private applyIndicatorPanes(paneIds: string[]): void {
    this.indicatorPaneIds = paneIds;
    for (const [index, paneId] of paneIds.entries()) {
      const pane = this.chart.panes()[index + 1];
      if (!pane) continue;
      const scale = pane.priceScale('right');
      scale.applyOptions({
        visible: true,
        borderVisible: true,
        ticksVisible: true,
        minimumWidth: 58,
      });
      const range = indicatorPaneScale(paneId as CoreIndicatorInstance['id']);
      const visibleRange = this.indicatorScaleRanges.get(paneId);
      if (visibleRange) scale.setVisibleRange(visibleRange);
      else if (range) scale.setVisibleRange({ from: range.min, to: range.max });
    }
    for (const id of this.restoredPaneHeights.keys()) {
      if (!paneIds.includes(id)) this.restoredPaneHeights.delete(id);
    }
    for (const id of this.indicatorScaleRanges.keys()) {
      if (!paneIds.includes(id)) this.indicatorScaleRanges.delete(id);
    }
    for (const id of this.indicatorPaneHeights.keys()) {
      if (!paneIds.includes(id)) this.indicatorPaneHeights.delete(id);
    }
    const comparisonPaneCount = this.comparisonData.filter((item) => item.placement === 'pane').length;
    for (let index = this.chart.panes().length - 1; index > paneIds.length + comparisonPaneCount; index -= 1) {
      const pane = this.chart.panes()[index];
      if (pane?.getSeries().length === 0) this.chart.removePane(index);
    }
    this.renderComparisonSeries();
  }

  indicatorPaneGeometry(): TradingIndicatorPaneGeometry[] {
    this.assertActive();
    const chartElement = this.chart.chartElement();
    const chartRect = chartElement.getBoundingClientRect();
    return this.indicatorPaneIds.flatMap((id, index) => {
      const paneIndex = index + 1;
      const pane = this.chart.panes()[paneIndex];
      const element = pane?.getHTMLElement();
      if (!element) return [];
      const rect = element.getBoundingClientRect();
      return [{ id, paneIndex, top: rect.top - chartRect.top, height: rect.height }];
    });
  }

  /** The main (price) pane's height in pixels from the chart's top, or null before layout. */
  mainPaneHeight(): number | null {
    this.assertActive();
    const height = this.chart.panes()[0]?.getHTMLElement()?.getBoundingClientRect().height;
    return height ? height : null;
  }

  setIndicatorPaneMinimized(id: string, minimized: boolean): void {
    this.assertActive();
    if (this.fullscreenIndicatorId !== null) return;
    const index = this.indicatorPaneIds.indexOf(id);
    if (index < 0) return;
    const pane = this.chart.panes()[index + 1];
    if (!pane) return;
    if (minimized) {
      if (!this.restoredPaneHeights.has(id)) this.restoredPaneHeights.set(id, Math.max(100, pane.getHeight()));
      pane.setHeight(34);
      return;
    }
    pane.setHeight(this.restoredPaneHeights.get(id) ?? this.indicatorPaneHeights.get(id) ?? 140);
    this.restoredPaneHeights.delete(id);
  }

  resetIndicatorPaneView(id: string): void {
    this.assertActive();
    const index = this.indicatorPaneIds.indexOf(id);
    if (index < 0) return;
    const pane = this.chart.panes()[index + 1];
    const scale = pane?.priceScale('right');
    if (!scale) return;

    // Remove the manual range so future indicator updates keep the restored
    // default instead of immediately applying the user's previous drag.
    this.indicatorScaleRanges.delete(id);
    const range = indicatorPaneScale(id as CoreIndicatorInstance['id']);
    if (range) {
      scale.setAutoScale(false);
      scale.setVisibleRange({ from: range.min, to: range.max });
    } else {
      scale.setAutoScale(true);
    }
    this.notifyViewportChange();
  }

  resizeIndicatorPaneByPixels(id: string, edge: 'top' | 'bottom', deltaY: number): void {
    this.assertActive();
    if (this.fullscreenIndicatorId !== null || !Number.isFinite(deltaY) || deltaY === 0) return;
    const index = this.indicatorPaneIds.indexOf(id);
    if (index < 0) return;
    const pane = this.chart.panes()[index + 1];
    if (!pane || pane.getHeight() <= 34) return;
    const heightDelta = edge === 'top' ? -deltaY : deltaY;
    const nextHeight = Math.max(80, pane.getHeight() + heightDelta);
    if (Math.abs(nextHeight - pane.getHeight()) < 0.5) return;
    pane.setHeight(nextHeight);
    this.indicatorPaneHeights.set(id, nextHeight);
    this.notifyViewportChange();
  }

  setIndicatorPaneFullscreen(id: string | null): void {
    this.assertActive();
    if (id === null) {
      this.restoreFullscreenPaneHeights();
      return;
    }
    const indicatorIndex = this.indicatorPaneIds.indexOf(id);
    if (indicatorIndex < 0) return;
    const paneIndex = indicatorIndex + 1;
    const panes = this.chart.panes();
    if (!panes[paneIndex]) return;
    if (this.fullscreenIndicatorId !== id || this.fullscreenMainPane) {
      this.restoreFullscreenPaneHeights();
      this.fullscreenIndicatorId = id;
      this.fullscreenMainPane = false;
      this.captureFullscreenPaneState(panes);
    }
    this.applyFullscreenPaneHeights();
  }

  setMainPaneFullscreen(enabled: boolean): void {
    this.assertActive();
    if (!enabled) {
      this.restoreFullscreenPaneHeights();
      return;
    }
    if (!this.fullscreenMainPane || this.fullscreenIndicatorId !== null) {
      this.restoreFullscreenPaneHeights();
      this.fullscreenIndicatorId = null;
      this.fullscreenMainPane = true;
      this.captureFullscreenPaneState(this.chart.panes());
    }
    this.applyFullscreenPaneHeights();
  }

  refreshIndicatorPaneFullscreen(): void {
    this.assertActive();
    if (this.fullscreenIndicatorId !== null || this.fullscreenMainPane) this.applyFullscreenPaneHeights();
  }

  private captureFullscreenPaneState(panes: ReturnType<IChartApi['panes']>): void {
    this.fullscreenPaneStretchFactors.clear();
    for (const [index, pane] of panes.entries()) {
      this.fullscreenPaneStretchFactors.set(index, pane.getStretchFactor());
    }
  }

  private applyFullscreenPaneHeights(): void {
    if (this.fullscreenIndicatorId === null && !this.fullscreenMainPane) return;
    const indicatorIndex = this.fullscreenIndicatorId === null ? -1 : this.indicatorPaneIds.indexOf(this.fullscreenIndicatorId);
    if (!this.fullscreenMainPane && indicatorIndex < 0) {
      this.restoreFullscreenPaneHeights();
      return;
    }
    const panes = this.chart.panes();
    const focusedPaneIndex = this.fullscreenMainPane ? 0 : indicatorIndex + 1;
    if (!panes[focusedPaneIndex]) {
      this.restoreFullscreenPaneHeights();
      return;
    }
    for (const [index, pane] of panes.entries()) {
      const focused = index === focusedPaneIndex;
      pane.setStretchFactor(focused ? 1 : 0);
      const element = pane.getHTMLElement();
      if (element) {
        element.style.visibility = focused ? 'visible' : 'hidden';
        element.style.pointerEvents = focused ? 'auto' : 'none';
      }
    }
  }

  private restoreFullscreenPaneHeights(): void {
    if (this.fullscreenIndicatorId === null && !this.fullscreenMainPane) return;
    const panes = this.chart.panes();
    for (const [index, stretchFactor] of this.fullscreenPaneStretchFactors) panes[index]?.setStretchFactor(stretchFactor);
    for (const pane of panes) {
      const element = pane.getHTMLElement();
      if (element) {
        element.style.removeProperty('visibility');
        element.style.removeProperty('pointer-events');
      }
    }
    this.fullscreenIndicatorId = null;
    this.fullscreenMainPane = false;
    this.fullscreenPaneStretchFactors.clear();
  }

  updateBar(bar: MarketBar): boolean {
    this.assertActive();
    const time = timestamp(bar.start_time);
    const previousRevision = this.revisions.get(time) ?? 0;
    if (bar.ingestion_revision < previousRevision) return false;
    this.revisions.set(time, bar.ingestion_revision);
    const existingIndex = this.bars.findIndex((item) => timestamp(item.start_time) === time);
    if (existingIndex >= 0) {
      // An in-place tick keeps every time point; only the cached numbers of this bar change.
      this.bars[existingIndex] = bar;
      this.barSeriesCache[existingIndex] = undefined;
    } else {
      this.bars.push(bar);
      this.priceTimes.push(time);
      this.appendSeriesTime('price', time);
      this.appendSeriesTime('volume', time);
      this.invalidateDrawingTimes();
    }
    this.updatePriceData(bar);
    this.volumeSeries.update(volumeData(bar));
    return true;
  }

  projectDrawingPoint(point: DrawingPoint): DrawingCoordinate | null {
    this.assertActive();
    const timeScale = this.chart.timeScale();
    const time = timestamp(point.time);
    const x = timeScale.timeToCoordinate(time)
      ?? (() => {
        const logical = this.timeIndex().logicalIndexForTime(point.time);
        return logical === null ? null : this.logicalCoordinate(logical);
      })();
    const y = this.priceSeries.priceToCoordinate(point.price * this.priceScaleMultiplier);
    return x === null || y === null ? null : { x, y };
  }

  indicatorPointToCoordinate(key: string, point: { time: string; value: number }): DrawingCoordinate | null {
    this.assertActive();
    const series = this.indicatorSeries.get(key);
    if (!series) return null;
    const x = this.chart.timeScale().timeToCoordinate(timestamp(point.time));
    const y = this.indicatorValueToCoordinate(key, point.value);
    return x === null || y === null ? null : { x, y };
  }

  indicatorValueToCoordinate(key: string, value: number): number | null {
    this.assertActive();
    const series = this.indicatorSeries.get(key);
    if (!series) return null;
    const paneIndex = this.indicatorSeriesPanes.get(key) ?? 0;
    const pane = this.chart.panes()[paneIndex];
    const paneElement = pane?.getHTMLElement();
    const chartElement = this.chart.chartElement();
    if (!paneElement || !chartElement) return null;
    const localY = series.priceToCoordinate(paneIndex === 0 ? value * this.priceScaleMultiplier : value);
    if (localY === null) return null;
    const paneRect = paneElement.getBoundingClientRect();
    const chartRect = chartElement.getBoundingClientRect();
    return localY + paneRect.top - chartRect.top;
  }

  indicatorValueToCoordinateForId(id: CoreIndicatorId, value: number): number | null {
    this.assertActive();
    const key = [...this.indicatorSeries.keys()].find((candidate) => candidate.split(':', 1)[0] === id);
    return key === undefined ? null : this.indicatorValueToCoordinate(key, value);
  }

  indicatorPlotWidth(): number {
    this.assertActive();
    return this.chart.timeScale().width();
  }

  /**
   * The chart point at a pane coordinate. Times snap to the nearest bar unless
   * `exactTime` is set (freehand strokes), which keeps the time between bars.
   */
  drawingPointFromCoordinate(x: number, y: number, options: { exactTime?: boolean } = {}): DrawingPoint | null {
    this.assertActive();
    const timeScale = this.chart.timeScale();
    const logical = timeScale.coordinateToLogical(x);
    const price = this.priceSeries.coordinateToPrice(y);
    if (price === null) return null;
    if (logical !== null && options.exactTime) {
      const exact = this.timeIndex().timeForLogicalIndex(logical);
      return exact === null ? null : { time: new Date(Date.parse(exact)).toISOString(), price: price / this.priceScaleMultiplier };
    }
    const time = logical === null
      ? timeScale.coordinateToTime(x)
      : this.timeIndex().timeForLogicalIndex(Math.round(logical));
    const seconds = typeof time === 'number' ? time : typeof time === 'string' ? timestamp(time) : null;
    if (seconds === null) return null;
    return { time: new Date(seconds * 1_000).toISOString(), price: price / this.priceScaleMultiplier };
  }

  /** The loaded bars as numbers, for drawings computed from bars (regression, VWAP, profiles). */
  drawingBars(): DrawingBarSeries {
    this.assertActive();
    if (!this.barSeries) {
      const bars = this.bars;
      const index = this.rawBarTimeIndex();
      this.barSeries = {
        length: bars.length,
        at: (position) => {
          const bar = bars[position];
          if (!bar) return undefined;
          this.barSeriesCache[position] ??= {
            time: bar.start_time,
            open: Number(bar.open),
            high: Number(bar.high),
            low: Number(bar.low),
            close: Number(bar.close),
            volume: Number(bar.volume),
          };
          return this.barSeriesCache[position];
        },
        indexAtOrBefore: (time) => index.indexAtOrBefore(time),
      };
    }
    return this.barSeries;
  }

  /**
   * The `drawingBars()` positions currently visible (partly visible bars at the edges included), or null before the chart
   * has a range or when no loaded bar is in view (scrolled into the empty space past either end).
   */
  drawingVisibleBars(): { from: number; to: number } | null {
    this.assertActive();
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (!range || this.bars.length === 0) return null;
    const chart = this.timeIndex();
    const fromTime = chart.timeForLogicalIndex(Math.max(0, Math.floor(range.from)));
    const toTime = chart.timeForLogicalIndex(Math.max(0, Math.ceil(range.to)));
    const bars = this.rawBarTimeIndex();
    const atOrBefore = fromTime ? bars.indexAtOrBefore(fromTime) : -1;
    // The first bar starting at or after the left edge.
    const from = fromTime && atOrBefore >= 0 && Date.parse(this.bars[atOrBefore].start_time) < Date.parse(fromTime) ? atOrBefore + 1 : Math.max(0, atOrBefore);
    const to = toTime ? bars.indexAtOrBefore(toTime) : this.bars.length - 1;
    return to < 0 || from > to ? null : { from, to };
  }

  /**
   * Whether the chart's bar index is the loaded bars' own index, as the server evaluates drawing alerts. Brick-type
   * charts (Renko, range, line break, Kagi, P&F) plot synthetic bars, so a sloped line's bar index differs there.
   */
  drawingBarIndexMatchesBars(): boolean {
    if (SYNTHETIC_BAR_CHART_TYPES.has(this.chartType) || this.bars.length === 0) return false;
    // Checked, not assumed: up to the last bar the chart plots exactly the bars' times, so any future source of extra
    // time points falls back to flat levels instead of a sloped alert that crosses other prices on the server.
    const lastBar = this.bars[this.bars.length - 1].start_time;
    return this.timeIndex().indexAtOrBefore(lastBar) === this.bars.length - 1;
  }

  /** The chart bar index of a time (fractional between bars), on the chart's own time scale. */
  drawingBarIndexForTime(time: string): number | null {
    this.assertActive();
    return this.timeIndex().logicalIndexForTime(time);
  }

  /** The time at a chart bar index (inverse of drawingBarIndexForTime). */
  drawingTimeForBarIndex(index: number): string | null {
    this.assertActive();
    return this.timeIndex().timeForLogicalIndex(index);
  }

  /** The time `count` bars after `time`, across gaps; beyond the data at the bar interval. */
  drawingTimeAfterBars(time: string, count: number): string | null {
    this.assertActive();
    return this.timeIndex().timeAfterBars(time, count);
  }

  /** A price as the main price scale formats it. */
  formatDrawingPrice(price: number): string {
    this.assertActive();
    return this.priceSeries.priceFormatter().format(price * this.priceScaleMultiplier);
  }

  zoomAtCoordinate(x: number, deltaY: number): void {
    this.assertActive();
    const timeScale = this.chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    const anchor = timeScale.coordinateToLogical(x);
    if (!range || anchor === null || !Number.isFinite(deltaY)) return;
    const normalizedDelta = Math.max(-0.35, Math.min(0.35, deltaY / 500));
    const factor = Math.exp(normalizedDelta);
    const nextRange: LogicalRange = {
      from: anchor - (anchor - range.from) * factor,
      to: anchor + (range.to - anchor) * factor,
    };
    timeScale.setVisibleLogicalRange(constrainZoomOutRange(nextRange, this.maxZoomOutRange, anchor));
  }

  panTimeByPixels(deltaX: number): void {
    this.assertActive();
    if (!Number.isFinite(deltaX) || deltaX === 0) return;
    const timeScale = this.chart.timeScale();
    const range = timeScale.getVisibleLogicalRange();
    if (!range) return;

    // Use the current bar spacing instead of converting the range endpoints
    // back to coordinates. Once a drag moves past the loaded data, the chart
    // clamps both out-of-range logical coordinates to the same edge, making
    // that coordinate-based calculation return zero and stopping the drag.
    const pixelsPerLogical = timeScale.options().barSpacing;
    if (!Number.isFinite(pixelsPerLogical) || pixelsPerLogical <= 0) return;
    const logicalDelta = deltaX / pixelsPerLogical;
    timeScale.setVisibleLogicalRange({ from: range.from - logicalDelta, to: range.to - logicalDelta });
  }

  panPriceScaleByPixels(deltaY: number): void {
    this.assertActive();
    this.panPanePriceScaleByPixels(0, deltaY);
  }

  panPriceScaleByPixelsAtCoordinate(y: number, deltaY: number): void {
    this.assertActive();
    if (!Number.isFinite(y)) return;
    this.panPanePriceScaleByPixels(this.paneIndexAtCoordinate(y), deltaY);
  }

  indicatorPaneIdAtCoordinate(y: number): string | null {
    this.assertActive();
    const paneIndex = this.paneIndexAtCoordinate(y);
    return paneIndex > 0 ? this.indicatorPaneIds[paneIndex - 1] ?? null : null;
  }

  /** The pane under a viewport y: the main pane, an indicator pane, or null on the time axis (TVP-2.5). */
  paneAtClientY(clientY: number): TradingPaneHit | null {
    this.assertActive();
    if (!Number.isFinite(clientY)) return null;
    for (const [paneIndex, pane] of this.chart.panes().entries()) {
      const rect = pane.getHTMLElement()?.getBoundingClientRect();
      if (!rect || rect.height <= 0 || clientY < rect.top || clientY > rect.top + rect.height) continue;
      if (paneIndex === 0) return { kind: 'main' };
      const id = this.indicatorPaneIds[paneIndex - 1];
      return id ? { kind: 'indicator', id: id as CoreIndicatorId } : null;
    }
    return null;
  }

  indicatorPaneIdAtClientY(clientY: number): CoreIndicatorId | null {
    this.assertActive();
    const chartRect = this.chart.chartElement().getBoundingClientRect();
    return this.indicatorPaneIdAtCoordinate(clientY - chartRect.top) as CoreIndicatorId | null;
  }

  indicatorValueFromCoordinate(id: CoreIndicatorId, y: number): number | null {
    this.assertActive();
    const paneIndex = this.indicatorPaneIds.indexOf(id) + 1;
    if (paneIndex <= 0 || !Number.isFinite(y)) return null;
    const pane = this.chart.panes()[paneIndex];
    const paneElement = pane?.getHTMLElement();
    if (!paneElement) return null;
    const chartRect = this.chart.chartElement().getBoundingClientRect();
    const paneRect = paneElement.getBoundingClientRect();
    const localY = y - (paneRect.top - chartRect.top);
    for (const [key, series] of this.indicatorSeries) {
      if (key.split(':', 1)[0] !== id || this.indicatorSeriesPanes.get(key) !== paneIndex) continue;
      const value = series.coordinateToPrice(localY);
      return typeof value === 'number' && Number.isFinite(value) ? value : null;
    }
    return null;
  }

  indicatorValueFromClientY(id: CoreIndicatorId, clientY: number): number | null {
    this.assertActive();
    const chartRect = this.chart.chartElement().getBoundingClientRect();
    return this.indicatorValueFromCoordinate(id, clientY - chartRect.top);
  }

  private panPanePriceScaleByPixels(paneIndex: number, deltaY: number): void {
    if (!Number.isFinite(deltaY) || deltaY === 0) return;
    const priceScale = this.panePriceScale(paneIndex);
    const range = priceScale?.getVisibleRange();
    const paneHeight = this.chart.panes()[paneIndex]?.getHeight() ?? this.chart.chartElement().getBoundingClientRect().height;
    if (!priceScale || !range || !Number.isFinite(paneHeight) || paneHeight <= 0) return;
    const priceDelta = deltaY / paneHeight * (range.to - range.from);
    priceScale.setAutoScale(false);
    const nextRange = { from: range.from - priceDelta, to: range.to - priceDelta };
    priceScale.setVisibleRange(nextRange);
    const paneId = paneIndex > 0 ? this.indicatorPaneIds[paneIndex - 1] : undefined;
    if (paneId) this.indicatorScaleRanges.set(paneId, nextRange);
    this.notifyViewportChange();
  }

  private paneIndexAtCoordinate(y: number): number {
    if (!Number.isFinite(y)) return 0;
    const chartRect = this.chart.chartElement().getBoundingClientRect();
    for (const [paneIndex, pane] of this.chart.panes().entries()) {
      const paneElement = pane.getHTMLElement();
      if (!paneElement) continue;
      const paneRect = paneElement.getBoundingClientRect();
      const top = paneRect.top - chartRect.top;
      if (y >= top && y <= top + paneRect.height) return paneIndex;
    }
    return 0;
  }

  private panePriceScale(paneIndex: number) {
    if (paneIndex === 0) return this.chart.priceScale(this.priceScaleSide);
    return this.chart.panes()[paneIndex]?.priceScale('right') ?? null;
  }

  private paneValueAtCoordinate(paneIndex: number, y: number): number | null {
    if (paneIndex === 0) return this.priceSeries.coordinateToPrice(y);
    const pane = this.chart.panes()[paneIndex];
    const paneElement = pane?.getHTMLElement();
    if (!paneElement) return null;
    const chartRect = this.chart.chartElement().getBoundingClientRect();
    const paneRect = paneElement.getBoundingClientRect();
    const localY = y - (paneRect.top - chartRect.top);
    for (const [key, series] of this.indicatorSeries) {
      if (this.indicatorSeriesPanes.get(key) === paneIndex) return series.coordinateToPrice(localY);
    }
    return null;
  }

  zoomPriceScaleAtCoordinate(y: number, deltaY: number): void {
    this.assertActive();
    const paneIndex = this.paneIndexAtCoordinate(y);
    const priceScale = this.panePriceScale(paneIndex);
    if (!priceScale) return;
    const range = priceScale.getVisibleRange();
    const anchor = this.paneValueAtCoordinate(paneIndex, y);
    if (!range || anchor === null || !Number.isFinite(deltaY)) return;
    const normalizedDelta = Math.max(-0.35, Math.min(0.35, deltaY / 500));
    const factor = Math.exp(normalizedDelta);
    const nextRange = {
      from: anchor - (anchor - range.from) * factor,
      to: anchor + (range.to - anchor) * factor,
    };
    priceScale.setAutoScale(false);
    priceScale.setVisibleRange(nextRange);
    const paneId = paneIndex > 0 ? this.indicatorPaneIds[paneIndex - 1] : undefined;
    if (paneId) this.indicatorScaleRanges.set(paneId, nextRange);
    this.notifyViewportChange();
  }

  isPriceScaleCoordinate(x: number): boolean {
    this.assertActive();
    const width = this.chart.chartElement().getBoundingClientRect().width;
    const mainPriceScaleWidth = this.chart.priceScale(this.priceScaleSide).width();
    const indicatorPriceScaleWidth = [...new Set(this.indicatorSeriesPanes.values())]
      .filter((paneIndex) => paneIndex > 0)
      .reduce((maxWidth, paneIndex) => Math.max(maxWidth, this.panePriceScale(paneIndex)?.width() ?? 0), 0);
    if (!Number.isFinite(x) || width <= 0) return false;
    if (this.priceScaleSide === 'right' && x >= width - mainPriceScaleWidth) return true;
    if (this.priceScaleSide === 'left' && x <= mainPriceScaleWidth) return true;
    return indicatorPriceScaleWidth > 0 && x >= width - indicatorPriceScaleWidth;
  }

  isMainPriceScaleCoordinate(x: number): boolean {
    this.assertActive();
    const width = this.chart.chartElement().getBoundingClientRect().width;
    const priceScaleWidth = this.chart.priceScale(this.priceScaleSide).width();
    if (!Number.isFinite(x) || width <= 0 || priceScaleWidth <= 0) return false;
    return this.priceScaleSide === 'right' ? x >= width - priceScaleWidth : x <= priceScaleWidth;
  }

  setPriceScaleAutoScale(autoScale: boolean): void {
    this.assertActive();
    this.chart.priceScale(this.priceScaleSide).setAutoScale(autoScale);
    this.notifyViewportChange();
  }

  setPriceScaleMultiplier(multiplier: number): void {
    this.assertActive();
    const nextMultiplier = Number.isFinite(multiplier) && multiplier > 0 ? multiplier : 1;
    if (Math.abs(nextMultiplier - this.priceScaleMultiplier) < 1e-12) return;
    const visibleRange = this.chart.timeScale().getVisibleLogicalRange();
    this.priceScaleMultiplier = nextMultiplier;
    this.renderSessionPriceLine();
    this.setPriceData(this.bars);
    this.setIndicatorOutputs(this.indicatorOutputs);
    this.chart.priceScale(this.priceScaleSide).setAutoScale(true);
    if (visibleRange) this.chart.timeScale().setVisibleLogicalRange(visibleRange);
    this.notifyViewportChange();
  }

  setPriceScaleMode(mode: PriceScaleMode): void {
    this.assertActive();
    this.chart.priceScale(this.priceScaleSide).applyOptions({ mode });
    this.notifyViewportChange();
  }

  setPriceScaleInvert(invertScale: boolean): void {
    this.assertActive();
    this.chart.priceScale(this.priceScaleSide).applyOptions({ invertScale });
    this.notifyViewportChange();
  }

  setPriceScaleSide(side: TradingPriceScaleSide): void {
    this.assertActive();
    this.priceScaleSide = side;
    this.priceSeries.applyOptions({ priceScaleId: side });
    for (const [key, series] of this.indicatorSeries) {
      if (this.indicatorSeriesPanes.get(key) === 0) series.applyOptions({ priceScaleId: side });
    }
    for (const [instrumentId, series] of this.comparisonSeries) {
      const item = this.comparisonData.find((comparison) => comparison.instrumentId === instrumentId);
      if (item?.placement === 'percent') series.applyOptions({ priceScaleId: side });
    }
    this.chart.applyOptions({
      leftPriceScale: { visible: side === 'left' && this.priceScaleLabelsVisible, borderVisible: this.priceScaleLinesVisible, ticksVisible: this.priceScaleLinesVisible },
      rightPriceScale: { visible: side === 'right' && this.priceScaleLabelsVisible, borderVisible: this.priceScaleLinesVisible, ticksVisible: this.priceScaleLinesVisible },
    });
  }

  setPriceScaleLabelsVisible(visible: boolean): void {
    this.assertActive();
    this.priceScaleLabelsVisible = visible;
    this.chart.applyOptions({
      leftPriceScale: { visible: this.priceScaleSide === 'left' && visible },
      rightPriceScale: { visible: this.priceScaleSide === 'right' && visible },
    });
  }

  setLatestValueLabelVisible(visible: boolean): void {
    this.assertActive();
    this.latestValueLabelVisible = visible;
    this.priceSeries.applyOptions({ lastValueVisible: visible });
  }

  setPriceScaleLinesVisible(visible: boolean): void {
    this.assertActive();
    this.priceScaleLinesVisible = visible;
    this.chart.applyOptions({
      leftPriceScale: { borderVisible: visible, ticksVisible: visible },
      rightPriceScale: { borderVisible: visible, ticksVisible: visible },
    });
  }

  setGridLinesVisible(visible: boolean): void {
    this.assertActive();
    this.gridLinesVisible = visible;
    this.chart.applyOptions({ grid: { vertLines: { visible }, horzLines: { visible } } });
  }

  setScalePriceOnly(enabled: boolean): void {
    this.assertActive();
    this.chart.applyOptions({ handleScale: { axisPressedMouseMove: { time: !enabled, price: true } } });
  }

  priceToCoordinate(price: number): number | null {
    this.assertActive();
    return this.priceSeries.priceToCoordinate(price);
  }

  priceFromCoordinate(y: number): number | null {
    this.assertActive();
    return this.priceSeries.coordinateToPrice(y);
  }

  snapshotDataUrl(): string {
    this.assertActive();
    return this.chart.takeScreenshot().toDataURL('image/png');
  }

  /** Where the main series' last-price label sits, so the bar-close countdown can sit under it (TVP-2.5). */
  lastPriceLabelPosition(): TradingLastPriceLabelPosition | null {
    this.assertActive();
    if (!this.latestValueLabelVisible || !this.priceScaleLabelsVisible) return null;
    const last = this.priceSeries.lastValueData(true);
    if (last.noData) return null;
    const y = this.priceSeries.priceToCoordinate(last.price);
    const scaleWidth = this.chart.priceScale(this.priceScaleSide).width();
    const paneHeight = this.chart.panes()[0]?.getHeight() ?? 0;
    if (y === null || scaleWidth <= 0 || y < 0 || y > paneHeight) return null;
    return { y, color: last.color, side: this.priceScaleSide, scaleWidth, paneHeight };
  }

  /** The main price scale's side and width and the main pane's height, for controls beside the scale (TVP-7.3). */
  mainPriceScaleBox(): { side: 'left' | 'right'; scaleWidth: number; paneHeight: number } | null {
    this.assertActive();
    const scaleWidth = this.chart.priceScale(this.priceScaleSide).width();
    const paneHeight = this.chart.panes()[0]?.getHeight() ?? 0;
    return scaleWidth > 0 && paneHeight > 0 ? { side: this.priceScaleSide, scaleWidth, paneHeight } : null;
  }

  /** A labelled price line, such as the latest pre/post-market price; null removes it (TVP-2.5). */
  setSessionPriceLine(line: TradingSessionPriceLine | null): void {
    this.assertActive();
    const current = this.sessionPriceLineConfig;
    if (current === line || (current && line && current.price === line.price && current.title === line.title && current.color === line.color)) return;
    this.sessionPriceLineConfig = line ? { ...line } : null;
    this.renderSessionPriceLine();
  }

  private renderSessionPriceLine(): void {
    if (this.sessionPriceLine) {
      const { series, line } = this.sessionPriceLine;
      this.sessionPriceLine = null;
      if (series === this.priceSeries) series.removePriceLine(line);
    }
    const config = this.sessionPriceLineConfig;
    if (!config) return;
    const line = this.priceSeries.createPriceLine({
      price: config.price * this.priceScaleMultiplier,
      color: config.color,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: config.title,
    });
    this.sessionPriceLine = { series: this.priceSeries, line };
  }

  onIndicatorClick(listener: (selection: TradingIndicatorSelection) => void): () => void {
    this.assertActive();
    const handler = (parameter: MouseEventParams) => {
      const hoveredSeries = parameter.hoveredInfo?.series ?? parameter.hoveredSeries;
      if (!hoveredSeries || !parameter.point) return;
      const selected = [...this.indicatorSeries.entries()].find(([, series]) => series === hoveredSeries);
      if (!selected) return;
      const output = this.indicatorOutputs.find((item) => item.key === selected[0]);
      if (!output) return;
      listener({
        id: selected[0].split(':', 1)[0] as CoreIndicatorId,
        x: parameter.point.x,
        y: parameter.point.y,
      });
    };
    this.chart.subscribeClick(handler);
    return () => this.chart.unsubscribeClick(handler);
  }

  /**
   * A click on a bar of the chart: its time in milliseconds (time sync, TVP-4.2). Mouse presses on the chart are taken
   * by its pan handling, which reports a press without a drag through `clickAt`; the chart's own click event covers the
   * presses it lets through (touch, replay).
   */
  onBarClick(listener: (timeMs: number) => void): () => void {
    this.assertActive();
    const handler = (parameter: { time?: Time }) => {
      if (typeof parameter.time === 'number') listener(parameter.time * 1000);
    };
    this.chart.subscribeClick(handler);
    this.barClickListeners.add(listener);
    return () => {
      this.chart.unsubscribeClick(handler);
      this.barClickListeners.delete(listener);
    };
  }

  /** A click at a pane x that the chart's pan handling took: the bar under it, if any, to the bar-click listeners. */
  clickAt(x: number): void {
    this.assertActive();
    const logical = this.chart.timeScale().coordinateToLogical(x);
    if (logical === null) return;
    // Only a bar the chart has (as the chart's own click event gives), not the empty space before or after them.
    const index = Math.round(logical);
    const timeIndex = this.timeIndex();
    if (index < 0 || index >= timeIndex.length) return;
    const time = timeIndex.timeForLogicalIndex(index);
    if (time === null) return;
    const timeMs = Date.parse(time);
    if (!Number.isFinite(timeMs)) return;
    for (const listener of [...this.barClickListeners]) listener(timeMs);
  }

  onCrosshair(listener: (point: TradingCrosshairPoint | null) => void): () => void {
    this.assertActive();
    const handler = (parameter: { time?: Time; point?: { x: number; y: number }; paneIndex?: number; seriesData: Map<unknown, unknown> }) => {
      if (parameter.time === undefined || parameter.point === undefined) { listener(null); return; }
      // The y is measured within the pane under the pointer: it is a price only in the main pane.
      const pointerPrice = (parameter.paneIndex ?? 0) === 0 ? this.priceSeries.coordinateToPrice(parameter.point.y) : null;
      if (typeof pointerPrice === 'number' && Number.isFinite(pointerPrice)) {
        listener({ time: parameter.time, price: pointerPrice / this.priceScaleMultiplier, pointer: true });
        return;
      }
      const datum = parameter.seriesData.get(this.priceSeries) as { close?: number; value?: number } | undefined;
      const price = datum?.close ?? datum?.value;
      listener(typeof price === 'number' ? { time: parameter.time, price: price / this.priceScaleMultiplier } : null);
    };
    this.chart.subscribeCrosshairMove(handler);
    return () => this.chart.unsubscribeCrosshairMove(handler);
  }
  setCrosshair(point: TradingCrosshairPoint | null): void { this.assertActive(); if (point === null) this.chart.clearCrosshairPosition(); else this.chart.setCrosshairPosition(point.price * this.priceScaleMultiplier, point.time, this.priceSeries); }
  onVisibleRange(listener: (range: TradingVisibleRange | null) => void): () => void { this.assertActive(); const handler = (range: TradingVisibleRange | null) => listener(range); this.chart.timeScale().subscribeVisibleTimeRangeChange(handler); return () => this.chart.timeScale().unsubscribeVisibleTimeRangeChange(handler); }
  onViewportChange(listener: () => void): () => void {
    this.assertActive();
    const logicalRangeHandler = () => listener();
    const sizeHandler = () => listener();
    this.chart.timeScale().subscribeVisibleLogicalRangeChange(logicalRangeHandler);
    this.chart.timeScale().subscribeSizeChange(sizeHandler);
    this.viewportListeners.add(listener);
    return () => {
      this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(logicalRangeHandler);
      this.chart.timeScale().unsubscribeSizeChange(sizeHandler);
      this.viewportListeners.delete(listener);
    };
  }
  setVisibleRange(range: TradingVisibleRange): void {
    this.assertActive();
    if (this.bars.length === 0 || range.from == null || range.to == null) return;
    const timeScale = this.chart.timeScale();
    if (timeScale.timeToIndex(range.from, true) === null || timeScale.timeToIndex(range.to, true) === null) return;
    timeScale.setVisibleRange(range);
  }
  timeToCoordinate(value: string): number | null {
    this.assertActive();
    const timeScale = this.chart.timeScale();
    const direct = timeScale.timeToCoordinate(timestamp(value));
    if (direct !== null) return direct;
    const logical = this.timeIndex().logicalIndexForTime(value);
    return logical === null ? null : this.logicalCoordinate(logical);
  }
  barTimeToCoordinate(value: string): number | null {
    this.assertActive();
    const logical = this.timeIndex().logicalIndexForTime(value);
    return logical === null ? null : this.logicalCoordinate(logical);
  }
  barIndexAtCoordinate(x: number, barCount: number): number | null {
    this.assertActive();
    const logical = this.chart.timeScale().coordinateToLogical(x);
    if (logical === null || !Number.isFinite(logical)) return null;
    const index = Math.round(logical);
    return index >= 0 && index < barCount ? index : null;
  }
  fitContent(): void {
    this.assertActive();
    const timeScale = this.chart.timeScale();
    timeScale.fitContent();
    this.chart.priceScale(this.priceScaleSide).setAutoScale(true);
    if (this.bars.length > 0) {
      const range = {
        from: -0.5,
        to: this.bars.length - 0.5 + this.rightOffset,
      };
      timeScale.setVisibleLogicalRange(range);
      this.maxZoomOutRange = range;
      return;
    }
    const range = timeScale.getVisibleLogicalRange();
    if (range) this.maxZoomOutRange = { from: range.from, to: range.to };
  }
  setRightOffset(offset: number): void {
    this.assertActive();
    this.rightOffset = Math.max(0, Math.min(100, Math.round(offset)));
    this.chart.timeScale().applyOptions({ rightOffset: this.rightOffset });
  }
  scrollToLatest(): void { this.assertActive(); this.chart.timeScale().scrollToRealTime(); }
  /** Attaches a primitive to the main price series; it follows chart type changes. Returns a detach function. */
  attachPriceSeriesPrimitive(primitive: ISeriesPrimitive<Time>): () => void {
    this.assertActive();
    this.priceSeriesPrimitives.add(primitive);
    this.priceSeries.attachPrimitive(primitive);
    return () => {
      if (!this.priceSeriesPrimitives.delete(primitive) || this.destroyed) return;
      this.priceSeries.detachPrimitive(primitive);
    };
  }
  api(): IChartApi { this.assertActive(); return this.chart; }
  destroy(): void { if (this.destroyed) return; this.restoreFullscreenPaneHeights(); this.destroyed = true; this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.comparisonViewportHandler); this.revisions.clear(); this.bars = []; this.priceTimes = []; this.seriesTimes.clear(); this.invalidateDrawingTimes(); this.indicatorOutputs = []; this.comparisonData = []; this.indicatorSeries.clear(); this.indicatorMarkerPlugins.clear(); this.comparisonSeries.clear(); this.comparisonSeriesPanes.clear(); this.comparisonSeriesOptions.clear(); this.viewportListeners.clear(); this.barClickListeners.clear(); this.chart.remove(); }
  /**
   * The x coordinate of a logical index. Lightweight Charts returns 0 for a
   * fractional logical index, so a time between bars (another interval's
   * bar, a freehand point) interpolates between its neighbouring bars.
   */
  private logicalCoordinate(logical: number): number | null {
    const timeScale = this.chart.timeScale();
    const lower = Math.floor(logical);
    const from = timeScale.logicalToCoordinate(lower as Logical);
    if (logical === lower || from === null) return from;
    const to = timeScale.logicalToCoordinate((lower + 1) as Logical);
    return to === null ? null : from + (to - from) * (logical - lower);
  }
  /** The chart's own time scale: every time any series plots, indexed as Lightweight Charts does. */
  private timeIndex(): DrawingTimeIndex {
    if (!this.chartTimeIndex) {
      const times: number[] = [];
      for (const seriesTimes of this.seriesTimes.values()) {
        for (const time of seriesTimes) {
          if (Number.isFinite(time)) times.push(time * 1_000);
        }
      }
      this.chartTimeIndex = DrawingTimeIndex.fromTimes(times, this.intervalStep());
    }
    return this.chartTimeIndex;
  }
  private intervalStepSeconds(): number | null {
    const step = this.intervalStep();
    return step === null ? null : step / 1_000;
  }
  private intervalStep(): number | null {
    const interval = this.bars[0]?.interval;
    return interval ? intervalStepMs(interval) : null;
  }
  private rawBarTimeIndex(): DrawingTimeIndex {
    this.rawBarIndex ??= new DrawingTimeIndex(this.bars, this.intervalStep());
    return this.rawBarIndex;
  }
  private invalidateDrawingTimes(): void {
    this.chartTimeIndex = null;
    this.rawBarIndex = null;
    this.barSeries = null;
    this.barSeriesCache = [];
  }
  /** Notes the times a series was given; the chart timeline is rebuilt only when they changed. */
  private noteSeriesTimes(key: string, data: readonly { time: Time }[]): void {
    const times = data.map((item) => (typeof item.time === 'number' ? item.time : Number.NaN));
    const previous = this.seriesTimes.get(key);
    if (previous && previous.length === times.length && previous.every((time, index) => Object.is(time, times[index]))) return;
    this.seriesTimes.set(key, times);
    this.chartTimeIndex = null;
  }
  /** Notes a bar a series appended by `update` (a new time after its last one). */
  private appendSeriesTime(key: string, time: number): void {
    const previous = this.seriesTimes.get(key) ?? [];
    if (previous.length > 0 && previous[previous.length - 1] >= time) return;
    this.seriesTimes.set(key, [...previous, time]);
    this.chartTimeIndex = null;
  }
  private forgetSeriesTimes(key: string): void {
    if (this.seriesTimes.delete(key)) this.chartTimeIndex = null;
  }
  private notifyViewportChange(): void { for (const listener of this.viewportListeners) listener(); }
  private assertActive(): void { if (this.destroyed) throw new Error('Trading chart adapter is disposed'); }
}
