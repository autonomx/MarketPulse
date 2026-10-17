// Forecasting tools (TVP-3.6): position forecast, bars pattern, ghost feed and sector.
import { booleanProperty, stringProperty } from '../properties';
import { areaFill, lineStroke } from '../shapes';
import {
  defineDrawingTool,
  type DrawingBar,
  type DrawingBarSeries,
  type DrawingGeometryContext,
  type DrawingPoint,
  type DrawingShape,
  type ScreenPoint,
} from '../types';
import { priceLine, rangeStats } from './ranges';

const UP = '#089981';
const DOWN = '#f23645';

export type ForecastState = 'in-progress' | 'success' | 'failure';

/**
 * A forecast succeeds when the price reaches the target price by the target time (a bar's high for a rise, its low
 * for a fall, from the first bar after the source time). Until the target time has a bar it is in progress.
 */
export function forecastState(source: DrawingPoint, target: DrawingPoint, bars: DrawingBarSeries): ForecastState {
  const rising = target.price >= source.price;
  const from = Date.parse(source.time);
  const to = Date.parse(target.time);
  let reachedTime = false;
  for (let index = Math.max(0, bars.indexAtOrBefore(source.time)); index < bars.length; index += 1) {
    const bar = bars.at(index);
    if (!bar) break;
    const time = Date.parse(bar.time);
    if (time <= from) continue;
    if (time > to) {
      reachedTime = true;
      break;
    }
    if (rising ? bar.high >= target.price : bar.low <= target.price) return 'success';
    if (time === to) reachedTime = true;
  }
  return reachedTime ? 'failure' : 'in-progress';
}

export const positionForecastTool = defineDrawingTool({
  id: 'position-forecast',
  label: 'Position forecast',
  displayName: 'Forecast',
  group: 'forecasting',
  creation: { gesture: 'drag' },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  geometry: (context) => {
    const [source, target] = context.points;
    const [rawSource, rawTarget] = context.rawPoints;
    const state = forecastState(rawSource, rawTarget, context.bars);
    const color = state === 'success' ? UP : state === 'failure' ? DOWN : context.style.color;
    const stats = rangeStats(rawSource, rawTarget, context);
    const status = state === 'success' ? 'Success' : state === 'failure' ? 'Failure' : 'In progress';
    const text = `${priceLine(stats, context.formatPrice)} · ${status}`;
    const width = Math.max(130, text.length * 6.6 + 16);
    const above = target.y <= source.y;
    const top = above ? target.y - 30 : target.y + 10;
    return [
      { kind: 'segment', x1: source.x, y1: source.y, x2: target.x, y2: target.y, ...lineStroke(context), stroke: color, dash: [6, 4] },
      { kind: 'marker', x: source.x, y: source.y, radius: 4, stroke: color, fill: '#ffffff', strokeWidth: 2 },
      { kind: 'marker', x: target.x, y: target.y, radius: 4, stroke: color, fill: color },
      { kind: 'rect', x: target.x - width / 2, y: top, width, height: 20, radius: 3, fill: color, hit: 'none' },
      { kind: 'text', x: target.x, y: top + 14, text, align: 'middle', fontSize: 11, fill: '#ffffff', halo: false, hit: 'none' },
    ];
  },
});

/** The bars a bars pattern copies: from the first bar at or after `from` to the last at or before `to`. */
export function patternBars(bars: DrawingBarSeries, from: string, to: string): DrawingBar[] {
  const [start, end] = Date.parse(from) <= Date.parse(to) ? [from, to] : [to, from];
  const result: DrawingBar[] = [];
  const last = bars.indexAtOrBefore(end);
  const startTime = Date.parse(start);
  for (let index = last; index >= 0; index -= 1) {
    const bar = bars.at(index);
    if (!bar || Date.parse(bar.time) < startTime) break;
    result.push(bar);
  }
  return result.reverse();
}

/** Pixels per bar near `time`, from the chart's bar spacing (at least 1). */
function barSpacing(context: DrawingGeometryContext, time: string): number {
  const next = context.timeAfterBars(time, 1);
  const here = context.project({ time, price: 0 });
  const there = next ? context.project({ time: next, price: 0 }) : null;
  return here && there ? Math.max(1, Math.abs(there.x - here.x)) : 6;
}

/** One candle as shapes: a wick and a body, filled when it falls. */
function candle(x: number, width: number, open: ScreenPoint['y'], high: number, low: number, close: number, color: string, opacity: number): DrawingShape[] {
  const bodyTop = Math.min(open, close);
  const bodyHeight = Math.max(1, Math.abs(close - open));
  return [
    { kind: 'segment', x1: x, y1: high, x2: x, y2: low, stroke: color, strokeWidth: 1, opacity, hit: 'none' },
    { kind: 'rect', x: x - width / 2, y: bodyTop, width, height: bodyHeight, stroke: color, strokeWidth: 1, fill: color, fillOpacity: close > open ? 0.85 : 0.25, opacity },
  ];
}

export const barsPatternTool = defineDrawingTool({
  id: 'bars-pattern',
  label: 'Bars pattern',
  group: 'forecasting',
  creation: { gesture: 'drag' },
  defaultProperties: { mode: 'candles', mirrored: false, flipped: false },
  propertySchema: [
    { key: 'mode', label: 'Mode', type: 'select', options: [{ value: 'candles', label: 'Bars' }, { value: 'line', label: 'Line' }] },
    { key: 'mirrored', label: 'Mirrored', type: 'boolean' },
    { key: 'flipped', label: 'Flipped', type: 'boolean' },
  ],
  draftPreview: 'shapes',
  // The source range is fixed at creation; the anchors then place the copy (the first one at the first bar's open).
  onCreate: ([first, second], services) => {
    const source = patternBars(services.bars, first.time, second.time);
    const open = source[0]?.open ?? first.price;
    return {
      points: [{ ...first, time: source[0]?.time ?? first.time, price: open }, { ...second }],
      properties: { sourceFrom: source[0]?.time ?? first.time, sourceTo: source.at(-1)?.time ?? second.time },
    };
  },
  geometry: (context) => {
    const [anchor] = context.rawPoints;
    const source = patternBars(context.bars, stringProperty(context.properties, 'sourceFrom', anchor.time), stringProperty(context.properties, 'sourceTo', context.rawPoints[1].time));
    if (source.length === 0) return [{ kind: 'segment', x1: context.points[0].x, y1: context.points[0].y, x2: context.points[1].x, y2: context.points[1].y, ...lineStroke(context), dash: [4, 4] }];
    const mirrored = booleanProperty(context.properties, 'mirrored', false);
    const flipped = booleanProperty(context.properties, 'flipped', false);
    const base = source[0].open;
    const price = (value: number) => anchor.price + (flipped ? -1 : 1) * (value - base);
    const ordered = mirrored ? [...source].reverse() : source;
    const spacing = barSpacing(context, anchor.time);
    const width = Math.max(1, spacing * 0.6);
    const shapes: DrawingShape[] = [];
    const line: ScreenPoint[] = [];
    ordered.forEach((bar, offset) => {
      const time = offset === 0 ? anchor.time : context.timeAfterBars(anchor.time, offset);
      if (!time) return;
      const at = (value: number) => context.project({ time, price: price(value) });
      const [open, high, low, close] = [bar.open, bar.high, bar.low, bar.close].map(at);
      if (!open || !high || !low || !close) return;
      const [o, c] = mirrored ? [close, open] : [open, close];
      if (stringProperty(context.properties, 'mode', 'candles') === 'line') line.push(c);
      else shapes.push(...candle(open.x, width, o.y, flipped ? low.y : high.y, flipped ? high.y : low.y, c.y, context.style.color, 0.75));
    });
    if (line.length >= 2) shapes.push({ kind: 'polyline', points: line, ...lineStroke(context) });
    return shapes;
  },
});

/** Ghost candles along a sketched path: one bar per chart bar between anchors, each closing on the path. */
export const MAX_GHOST_CANDLES = 300;

export function ghostCandles(points: readonly DrawingPoint[], barsBetween: (from: DrawingPoint, to: DrawingPoint) => number): { time: number; open: number; high: number; low: number; close: number }[] {
  const candles: { time: number; open: number; high: number; low: number; close: number }[] = [];
  for (let segment = 1; segment < points.length; segment += 1) {
    const from = points[segment - 1];
    const to = points[segment];
    // Bounded: a path drawn far ahead on a higher interval must not become thousands of candles on a lower one.
    const count = Math.min(MAX_GHOST_CANDLES, Math.max(1, barsBetween(from, to)));
    for (let step = 1; step <= count; step += 1) {
      const open = from.price + (to.price - from.price) * (step - 1) / count;
      const close = from.price + (to.price - from.price) * step / count;
      const wick = Math.max(Math.abs(close - open) * 0.5, Math.abs(to.price - from.price) * 0.02 / count);
      candles.push({ time: Date.parse(from.time) + (Date.parse(to.time) - Date.parse(from.time)) * step / count, open, close, high: Math.max(open, close) + wick, low: Math.min(open, close) - wick });
    }
  }
  return candles;
}

export const ghostFeedTool = defineDrawingTool({
  id: 'ghost-feed',
  label: 'Ghost feed',
  group: 'forecasting',
  creation: { gesture: 'click-click', minAnchors: 2 },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  previewAnchors: 2,
  geometry: (context) => {
    const barsBetween = (from: DrawingPoint, to: DrawingPoint) => {
      const a = context.barIndexForTime(from.time);
      const b = context.barIndexForTime(to.time);
      return a === null || b === null ? 10 : Math.round(Math.abs(b - a));
    };
    const spacing = barSpacing(context, context.rawPoints[0].time);
    const shapes: DrawingShape[] = [{ kind: 'polyline', points: context.points, ...lineStroke(context), strokeWidth: 1, dash: [3, 3], opacity: 0.5 }];
    for (const bar of ghostCandles(context.rawPoints, barsBetween)) {
      const time = new Date(bar.time).toISOString();
      const [open, high, low, close] = [bar.open, bar.high, bar.low, bar.close].map((price) => context.project({ time, price }));
      if (!open || !high || !low || !close) continue;
      shapes.push(...candle(open.x, Math.max(1, spacing * 0.6), open.y, high.y, low.y, close.y, bar.close >= bar.open ? UP : DOWN, 0.55));
    }
    return shapes;
  },
});

/** Points of the sector around `center` from `start` to the direction of `end`, at `start`'s radius. */
export function sectorPoints(center: ScreenPoint, start: ScreenPoint, end: ScreenPoint): ScreenPoint[] {
  const radius = Math.hypot(start.x - center.x, start.y - center.y);
  const from = Math.atan2(start.y - center.y, start.x - center.x);
  let sweep = Math.atan2(end.y - center.y, end.x - center.x) - from;
  if (sweep > Math.PI) sweep -= 2 * Math.PI;
  if (sweep < -Math.PI) sweep += 2 * Math.PI;
  const steps = Math.max(2, Math.ceil(Math.abs(sweep) / (Math.PI / 36)));
  const arc = Array.from({ length: steps + 1 }, (_, step) => {
    const angle = from + sweep * step / steps;
    return { x: center.x + radius * Math.cos(angle), y: center.y + radius * Math.sin(angle) };
  });
  return [center, ...arc];
}

export const sectorTool = defineDrawingTool({
  id: 'sector',
  label: 'Sector',
  group: 'forecasting',
  creation: { gesture: 'click-click', anchors: 3 },
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  previewAnchors: 2,
  geometry: (context) => {
    const [center, start, end] = context.points;
    if (!end) return [{ kind: 'segment', x1: center.x, y1: center.y, x2: start.x, y2: start.y, ...lineStroke(context) }];
    return [{ kind: 'polygon', points: sectorPoints(center, start, end), ...lineStroke(context), ...areaFill(context.style.color) }];
  },
});
