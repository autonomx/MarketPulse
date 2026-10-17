// Drawings computed from the chart's bars (TVP-3.1): regression trend and anchored VWAP.
import { lineAlertLevel } from '../alertLevels';
import { booleanProperty, numberProperty } from '../properties';
import { areaFill, lineStroke } from '../shapes';
import {
  anchorHandle,
  defineDrawingTool,
  type DrawingAlertLevel,
  type DrawingBarSeries,
  type DrawingGeometryContext,
  type DrawingPoint,
  type DrawingShape,
  type ScreenPoint,
} from '../types';

/** A least-squares line through bar closes: price = intercept + slope * (bar position - first). */
export type RegressionFit = { first: number; last: number; intercept: number; slope: number; deviation: number };

/**
 * The first bar at or after `time`; -1 when there is none, and also when `time` is before the first loaded bar: what
 * these drawings show depends on bars that aren't loaded yet, so they wait for them instead of starting elsewhere.
 */
function firstBarFrom(bars: DrawingBarSeries, time: string): number {
  const before = bars.indexAtOrBefore(time);
  if (before < 0) {
    const first = bars.at(0);
    return first && Date.parse(first.time) === Date.parse(time) ? 0 : -1;
  }
  const bar = bars.at(before);
  const index = bar && Date.parse(bar.time) < Date.parse(time) ? before + 1 : before;
  return index < bars.length ? index : -1;
}

/** A key for what a computation over `bars` read: its size and its first and last bars (updated in place by ticks). */
function seriesKey(bars: DrawingBarSeries): string {
  const first = bars.at(0);
  const last = bars.at(bars.length - 1);
  return `${bars.length}|${first?.time}|${last?.time}|${last?.open}|${last?.high}|${last?.low}|${last?.close}|${last?.volume}`;
}

/** A small most-recently-used cache: drawings recompute only when their bars or anchors change, not every frame. */
function memo<T>(limit: number): (key: string, compute: () => T) => T {
  const entries = new Map<string, T>();
  return (key, compute) => {
    const cached = entries.get(key);
    if (cached !== undefined || entries.has(key)) {
      entries.delete(key);
      entries.set(key, cached as T);
      return cached as T;
    }
    const value = compute();
    entries.set(key, value);
    if (entries.size > limit) entries.delete(entries.keys().next().value as string);
    return value;
  };
}

/**
 * The regression of closes on bar position over the bars from `start` to `end` (either order), with the population
 * standard deviation of the residuals. Null with fewer than two bars in the range.
 */
export function regressionFit(bars: DrawingBarSeries, start: string, end: string): RegressionFit | null {
  return regressionMemo(`${start}|${end}|${seriesKey(bars)}`, () => computeRegressionFit(bars, start, end));
}

const regressionMemo = memo<RegressionFit | null>(64);

function computeRegressionFit(bars: DrawingBarSeries, start: string, end: string): RegressionFit | null {
  const [from, to] = Date.parse(start) <= Date.parse(end) ? [start, end] : [end, start];
  const first = firstBarFrom(bars, from);
  const last = bars.indexAtOrBefore(to);
  if (first < 0 || last - first < 1) return null;
  const count = last - first + 1;
  let sumX = 0;
  let sumY = 0;
  let sumXY = 0;
  let sumXX = 0;
  for (let index = first; index <= last; index += 1) {
    const x = index - first;
    const y = bars.at(index)?.close ?? Number.NaN;
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumXX += x * x;
  }
  const slope = (count * sumXY - sumX * sumY) / (count * sumXX - sumX * sumX);
  const intercept = (sumY - slope * sumX) / count;
  let squares = 0;
  for (let index = first; index <= last; index += 1) {
    const residual = (bars.at(index)?.close ?? Number.NaN) - (intercept + slope * (index - first));
    squares += residual * residual;
  }
  const deviation = Math.sqrt(squares / count);
  return Number.isFinite(slope) && Number.isFinite(intercept) && Number.isFinite(deviation)
    ? { first, last, intercept, slope, deviation }
    : null;
}

/** The fit's end points in time/price, shifted by `offset`. */
function fitLine(bars: DrawingBarSeries, fit: RegressionFit, offset: number): [DrawingPoint, DrawingPoint] {
  return [
    { time: bars.at(fit.first)?.time ?? '', price: fit.intercept + offset },
    { time: bars.at(fit.last)?.time ?? '', price: fit.intercept + fit.slope * (fit.last - fit.first) + offset },
  ];
}

const REGRESSION_LINES = [
  { key: 'upper', label: 'Upper line', sign: 1 },
  { key: 'base', label: 'Regression line', sign: 0 },
  { key: 'lower', label: 'Lower line', sign: -1 },
] as const;

export const regressionTrendTool = defineDrawingTool({
  id: 'regression-trend',
  label: 'Regression trend',
  group: 'channels',
  creation: { gesture: 'drag' },
  defaultProperties: { deviations: 2, extendRight: false, fill: true },
  propertySchema: [
    { key: 'deviations', label: 'Deviation', type: 'number', min: 0, max: 10, step: 0.1 },
    { key: 'extendRight', label: 'Extend right', type: 'boolean' },
    { key: 'fill', label: 'Background', type: 'boolean' },
  ],
  draftPreview: 'shapes',
  geometry: (context) => {
    const fit = regressionFit(context.bars, context.rawPoints[0].time, context.rawPoints[1].time);
    if (!fit) return [];
    const spread = numberProperty(context.properties, 'deviations', 2) * fit.deviation;
    const extendRight = booleanProperty(context.properties, 'extendRight', false);
    const stroke = lineStroke(context);
    const projected = REGRESSION_LINES.map(({ sign }) => fitLine(context.bars, fit, sign * spread).map((point) => context.project(point)));
    if (projected.some(([start, end]) => !start || !end)) return [];
    const ends = projected.map(([start, end]) => {
      const a = start as ScreenPoint;
      const b = end as ScreenPoint;
      if (!extendRight || Math.abs(b.x - a.x) < 0.0001) return [a, b] as const;
      const x = context.viewport.width;
      return [a, { x, y: a.y + (b.y - a.y) * (x - a.x) / (b.x - a.x) }] as const;
    });
    const [upper, base, lower] = ends;
    const shapes: DrawingShape[] = [];
    if (booleanProperty(context.properties, 'fill', true)) {
      shapes.push({ kind: 'polygon', points: [upper[0], upper[1], lower[1], lower[0]], ...areaFill(context.style.color) });
    }
    shapes.push({ kind: 'segment', x1: base[0].x, y1: base[0].y, x2: base[1].x, y2: base[1].y, ...stroke, dash: [6, 4] });
    for (const [start, end] of [upper, lower]) shapes.push({ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...stroke });
    return shapes;
  },
  alertLevels: ([first, second], properties, services) => {
    if (!first || !second) return [];
    const fit = regressionFit(services.bars, first.time, second.time);
    if (!fit) return [];
    const spread = numberProperty(properties, 'deviations', 2) * fit.deviation;
    const extend = booleanProperty(properties, 'extendRight', false) ? 'right' : 'none';
    return REGRESSION_LINES.map(({ key, label, sign }) => {
      const [start, end] = fitLine(services.bars, fit, sign * spread);
      return lineAlertLevel(key, label, start, end, extend);
    }).filter((level): level is DrawingAlertLevel => level !== null);
  },
});

/**
 * Anchored VWAP from `anchor` on: the cumulative (high + low + close) / 3 weighted by volume, the formula of the
 * Anchored VWAP indicator (`anchoredVolumeWeightedAveragePrice`). Values start at the first bar at or after the
 * anchor; `first` is that bar's position (-1 when none).
 */
export function anchoredVwap(bars: DrawingBarSeries, anchor: string): { first: number; values: number[] } {
  const first = firstBarFrom(bars, anchor);
  if (first < 0) return { first, values: [] };
  let priceVolume = 0;
  let volume = 0;
  const values: number[] = [];
  for (let index = first; index < bars.length; index += 1) {
    const bar = bars.at(index);
    if (!bar) break;
    const typical = (bar.high + bar.low + bar.close) / 3;
    priceVolume += typical * bar.volume;
    volume += bar.volume;
    values.push(volume === 0 ? typical : priceVolume / volume);
  }
  return { first, values };
}

const vwapMemo = memo<{ first: number; values: number[] }>(32);

function cachedVwap(bars: DrawingBarSeries, anchor: string): { first: number; values: number[] } {
  return vwapMemo(`${anchor}|${seriesKey(bars)}`, () => anchoredVwap(bars, anchor));
}

/** Where the anchored VWAP starts on screen: its first value at the first bar from the anchor. */
function vwapStart(context: Pick<DrawingGeometryContext, 'bars' | 'rawPoints' | 'project'>): ScreenPoint | null {
  const { first, values } = cachedVwap(context.bars, context.rawPoints[0].time);
  const time = first >= 0 ? context.bars.at(first)?.time : undefined;
  return time && values.length > 0 ? context.project({ time, price: values[0] }) : null;
}

export const anchoredVwapTool = defineDrawingTool({
  id: 'anchored-vwap',
  label: 'Anchored VWAP',
  group: 'volume-based',
  creation: { gesture: 'click' },
  defaultProperties: {},
  propertySchema: [],
  // The handle sits on the line at its first bar; dragging it moves the anchor in time.
  handles: (context) => {
    const start = vwapStart(context);
    return start ? [anchorHandle(0, start)] : [];
  },
  geometry: (context) => {
    const { first, values } = cachedVwap(context.bars, context.rawPoints[0].time);
    if (first < 0 || values.length === 0) return [];
    const visible = context.visibleBars();
    const from = Math.max(first, (visible?.from ?? first) - 1);
    const to = Math.min(first + values.length - 1, (visible?.to ?? first + values.length - 1) + 1);
    const points: ScreenPoint[] = [];
    for (let index = from; index <= to; index += 1) {
      const time = context.bars.at(index)?.time;
      const projected = time ? context.project({ time, price: values[index - first] }) : null;
      if (projected) points.push(projected);
    }
    const stroke = lineStroke(context);
    const start = vwapStart(context);
    const shapes: DrawingShape[] = [];
    if (points.length >= 2) shapes.push({ kind: 'polyline', points, ...stroke });
    if (start) shapes.push({ kind: 'marker', x: start.x, y: start.y, radius: 3, ...stroke, fill: stroke.stroke });
    return shapes;
  },
});
