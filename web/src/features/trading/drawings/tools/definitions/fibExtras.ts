// Fibonacci tools beyond the retracement (TVP-3.2): trend-based extension and time, time zone, channel, speed
// resistance fan and arcs, circles, spiral and wedge.
//
// Price levels are built in time/price and projected, so a drawing and its alerts agree; screen-space tools (fan,
// arcs, circles, spiral, wedge) follow the anchors as TradingView's do, scaling with the chart's zoom.
import { horizontalAlertLevel, lineAlertLevel } from '../alertLevels';
import { booleanProperty, numberListProperty, recordsProperty } from '../properties';
import { areaFill, extendedSegment, lineStroke, rayEnd } from '../shapes';
import {
  defineDrawingTool,
  type DrawingAlertLevel,
  type DrawingGeometryContext,
  type DrawingPoint,
  type DrawingProperties,
  type DrawingPropertyField,
  type DrawingPropertyRecord,
  type DrawingShape,
  type DrawingToolServices,
  type PathCommand,
  type ScreenPoint,
} from '../types';
import { linePriceAt } from './channels';

const levelRecords = (values: readonly number[], hidden: readonly number[] = []): readonly DrawingPropertyRecord[] =>
  values.map((value) => ({ value, color: '', visible: !hidden.includes(value) }));

function levelsField(newValue: number): DrawingPropertyField {
  return {
    key: 'levels',
    label: 'Levels',
    type: 'records',
    fields: [
      { key: 'value', label: 'Level', type: 'number', step: 0.001 },
      { key: 'color', label: 'Colour', type: 'color' },
      { key: 'visible', label: 'Visible', type: 'boolean' },
    ],
    newRecord: { value: newValue, color: '', visible: true },
  };
}

type Level = { value: number; color: string };

/** The visible levels with their colours ('' = the drawing's colour). */
function visibleLevels(properties: DrawingProperties, fallback: readonly DrawingPropertyRecord[], stroke: string): Level[] {
  return recordsProperty(properties, 'levels', fallback).flatMap((record) => {
    const value = record.value;
    if (typeof value !== 'number' || !Number.isFinite(value) || record.visible === false) return [];
    return [{ value, color: typeof record.color === 'string' && record.color ? record.color : stroke }];
  });
}

/** Alert keys by value, a repeated value suffixed (as the retracement's, TVP-1.4). */
function keyedLevels(properties: DrawingProperties, fallback: readonly DrawingPropertyRecord[]): Array<{ key: string; value: number }> {
  const seen = new Map<number, number>();
  return recordsProperty(properties, 'levels', fallback).flatMap((record) => {
    const value = record.value;
    if (typeof value !== 'number' || !Number.isFinite(value)) return [];
    const count = (seen.get(value) ?? 0) + 1;
    seen.set(value, count);
    if (record.visible === false) return [];
    return [{ key: count === 1 ? `level-${value}` : `level-${value}-${count}`, value }];
  });
}

const EXTEND_FIELDS: readonly DrawingPropertyField[] = [
  { key: 'showLabels', label: 'Labels', type: 'boolean' },
  { key: 'extendLeft', label: 'Extend left', type: 'boolean' },
  { key: 'extendRight', label: 'Extend right', type: 'boolean' },
];

function label(text: string, at: ScreenPoint, color: string, align: 'start' | 'end' = 'start'): DrawingShape {
  return { kind: 'text', x: at.x + (align === 'start' ? 4 : -4), y: at.y - 2, text, fill: color, align, hit: 'none' };
}

function verticalLine(context: DrawingGeometryContext, x: number, color: string): DrawingShape {
  return { kind: 'segment', x1: x, y1: 0, x2: x, y2: context.viewport.height, ...lineStroke(context), stroke: color };
}

// Trend-based fib extension: A to B is the move, C where the extension starts; levels are C + (B - A) x level.

const EXTENSION_LEVELS = levelRecords([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 3.618, 4.236], [3.618, 4.236]);

export function fibExtensionPrice(points: readonly DrawingPoint[], level: number): number | null {
  const [first, second, third] = points;
  return first && second && third ? third.price + (second.price - first.price) * level : null;
}

export const fibExtensionTool = defineDrawingTool({
  id: 'fib-extension',
  label: 'Trend-based fib extension',
  displayName: 'Trend-Based Fib Extension',
  group: 'fibonacci',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { levels: EXTENSION_LEVELS, showLabels: true, extendLeft: false, extendRight: false },
  propertySchema: [levelsField(5.236), ...EXTEND_FIELDS],
  geometry: (context) => {
    const [first, second, third] = context.points;
    const stroke = lineStroke(context);
    const trend: DrawingShape[] = [{ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
    if (!third) return trend;
    trend.push({ kind: 'segment', x1: second.x, y1: second.y, x2: third.x, y2: third.y, ...stroke, dash: [4, 4], strokeWidth: 1 });
    const width = Math.max(Math.abs(second.x - first.x), Math.abs(third.x - second.x), 40);
    const x1 = booleanProperty(context.properties, 'extendLeft', false) ? 0 : third.x;
    const x2 = booleanProperty(context.properties, 'extendRight', false) ? context.viewport.width : third.x + width;
    const showLabels = booleanProperty(context.properties, 'showLabels', true);
    const shapes = visibleLevels(context.properties, EXTENSION_LEVELS, stroke.stroke ?? context.style.color).flatMap(({ value, color }): DrawingShape[] => {
      const price = fibExtensionPrice(context.rawPoints, value);
      const at = price === null ? null : context.project({ time: context.rawPoints[2].time, price });
      if (!at) return [];
      const line: DrawingShape = { kind: 'segment', x1, y1: at.y, x2, y2: at.y, ...stroke, stroke: color };
      const atEdge = x2 >= context.viewport.width - 1;
      return showLabels ? [line, label(`${value} (${context.formatPrice(price as number)})`, { x: x2, y: at.y }, color, atEdge ? 'end' : 'start')] : [line];
    });
    return [...trend, ...shapes];
  },
  // Each visible level from C onwards (TVP-1.4), keyed by value.
  alertLevels: (points, properties, services) => {
    const third = points[2];
    if (!third) return [];
    return keyedLevels(properties, EXTENSION_LEVELS).flatMap(({ key, value }) => {
      const price = fibExtensionPrice(points, value);
      const level = price === null ? null : horizontalAlertLevel(key, `Extension ${value} (${services.formatPrice(price)})`, { time: third.time, price }, 'right', services);
      return level ? [level] : [];
    });
  },
});

// Fib time zone: vertical lines at A + (B - A) x n bars for the Fibonacci numbers n.

const TIME_ZONES: readonly number[] = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144];

/** The x of bar index `index` (projected through the time of that index). */
function xAtIndex(context: DrawingGeometryContext, index: number, price: number): number | null {
  const time = context.timeForBarIndex(index);
  const at = time ? context.project({ time, price }) : null;
  return at ? at.x : null;
}

export const fibTimeZoneTool = defineDrawingTool({
  id: 'fib-time-zone',
  label: 'Fib time zone',
  displayName: 'Fib Time Zone',
  group: 'fibonacci',
  creation: { gesture: 'drag' },
  defaultProperties: { zones: TIME_ZONES, showLabels: true },
  propertySchema: [
    { key: 'zones', label: 'Zones (bars × A→B)', type: 'number-list', min: 0 },
    { key: 'showLabels', label: 'Labels', type: 'boolean' },
  ],
  geometry: (context) => {
    const [first, second] = context.rawPoints;
    const start = context.barIndexForTime(first.time);
    const end = context.barIndexForTime(second.time);
    const stroke = lineStroke(context);
    const showLabels = booleanProperty(context.properties, 'showLabels', true);
    if (start === null || end === null) return [verticalLine(context, context.points[0].x, stroke.stroke ?? context.style.color)];
    const step = end - start;
    const zones = step === 0 ? [0] : numberListProperty(context.properties, 'zones', TIME_ZONES);
    return zones.flatMap((zone): DrawingShape[] => {
      const x = xAtIndex(context, start + step * zone, first.price);
      if (x === null || x < -1 || x > context.viewport.width + 1) return [];
      const line = verticalLine(context, x, stroke.stroke ?? context.style.color);
      return showLabels ? [line, label(String(zone), { x, y: 14 }, stroke.stroke ?? context.style.color)] : [line];
    });
  },
});

// Trend-based fib time: vertical lines at C + (B - A) x level bars.

const TIME_LEVELS = levelRecords([0, 0.382, 0.5, 0.618, 1, 1.382, 1.618, 2, 2.382, 2.618, 3, 3.618, 4.236], [0.5, 1.382, 2.382, 3, 3.618, 4.236]);

export const fibTimeTool = defineDrawingTool({
  id: 'fib-time',
  label: 'Trend-based fib time',
  displayName: 'Trend-Based Fib Time',
  group: 'fibonacci',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { levels: TIME_LEVELS, showLabels: true },
  propertySchema: [levelsField(5.236), { key: 'showLabels', label: 'Labels', type: 'boolean' }],
  geometry: (context) => {
    const [first, second, third] = context.points;
    const stroke = lineStroke(context);
    const trend: DrawingShape[] = [{ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
    if (!third) return trend;
    trend.push({ kind: 'segment', x1: second.x, y1: second.y, x2: third.x, y2: third.y, ...stroke, dash: [4, 4], strokeWidth: 1 });
    const [a, b, c] = context.rawPoints.map((point) => context.barIndexForTime(point.time));
    if (a === null || b === null || c === null) return trend;
    const showLabels = booleanProperty(context.properties, 'showLabels', true);
    return [...trend, ...visibleLevels(context.properties, TIME_LEVELS, stroke.stroke ?? context.style.color).flatMap(({ value, color }): DrawingShape[] => {
      const x = xAtIndex(context, c + (b - a) * value, context.rawPoints[2].price);
      if (x === null) return [];
      const line = verticalLine(context, x, color);
      return showLabels ? [line, label(String(value), { x, y: 14 }, color)] : [line];
    })];
  },
});

// Fib channel: A-B is the base line, C sets the width; parallels at width x level.

const CHANNEL_LEVELS = levelRecords([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 3.618, 4.236], [3.618, 4.236]);

type Line = readonly [DrawingPoint, DrawingPoint];

/** The channel's line at `level` in time/price (level 0 is A-B, 1 passes through C), or null without a width. */
export function fibChannelLine(points: readonly DrawingPoint[], level: number, services: Pick<DrawingToolServices, 'barIndexForTime'>): Line | null {
  const [first, second, third] = points;
  if (!first || !second || !third) return null;
  const onBase = linePriceAt(first, second, third.time, services);
  if (onBase === null) return null;
  const offset = (third.price - onBase) * level;
  return [{ time: first.time, price: first.price + offset }, { time: second.time, price: second.price + offset }];
}

export const fibChannelTool = defineDrawingTool({
  id: 'fib-channel',
  label: 'Fib channel',
  displayName: 'Fib Channel',
  group: 'fibonacci',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { levels: CHANNEL_LEVELS, showLabels: true, extendLeft: false, extendRight: false },
  propertySchema: [levelsField(5.236), ...EXTEND_FIELDS],
  geometry: (context) => {
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    const extendLeft = booleanProperty(context.properties, 'extendLeft', false);
    const extendRight = booleanProperty(context.properties, 'extendRight', false);
    const base = extendedSegment(first, second, context.viewport, extendLeft, extendRight);
    const baseOnly: DrawingShape[] = [{ kind: 'segment', x1: base[0].x, y1: base[0].y, x2: base[1].x, y2: base[1].y, ...stroke }];
    // Without a width (fewer anchors, A and B on one bar, no bars yet) the base line still shows.
    if (context.points.length < 3 || !fibChannelLine(context.rawPoints, 0, context)) return baseOnly;
    const showLabels = booleanProperty(context.properties, 'showLabels', true);
    return visibleLevels(context.properties, CHANNEL_LEVELS, stroke.stroke ?? context.style.color).flatMap(({ value, color }): DrawingShape[] => {
      const line = fibChannelLine(context.rawPoints, value, context);
      const start = line ? context.project(line[0]) : null;
      const end = line ? context.project(line[1]) : null;
      if (!start || !end) return [];
      const [from, to] = extendedSegment(start, end, context.viewport, extendLeft, extendRight);
      const segment: DrawingShape = { kind: 'segment', x1: from.x, y1: from.y, x2: to.x, y2: to.y, ...stroke, stroke: color };
      const right = from.x >= to.x ? from : to;
      return showLabels ? [segment, label(String(value), right, color, right.x >= context.viewport.width - 1 ? 'end' : 'start')] : [segment];
    });
  },
  alertLevels: (points, properties, services) => {
    const extend = booleanProperty(properties, 'extendLeft', false)
      ? (booleanProperty(properties, 'extendRight', false) ? 'both' : 'left')
      : booleanProperty(properties, 'extendRight', false) ? 'right' : 'none';
    return keyedLevels(properties, CHANNEL_LEVELS).flatMap(({ key, value }): DrawingAlertLevel[] => {
      const line = fibChannelLine(points, value, services);
      const level = line ? lineAlertLevel(key, `Channel ${value}`, line[0], line[1], extend) : null;
      return level ? [level] : [];
    });
  },
});

// Screen-space Fibonacci tools: fan, arcs, circles, spiral, wedge.

const FAN_LEVELS: readonly number[] = [0.25, 0.382, 0.5, 0.618, 0.75];

function rays(context: DrawingGeometryContext, from: ScreenPoint, targets: readonly { at: ScreenPoint; text: string }[], color: string): DrawingShape[] {
  const stroke = lineStroke(context);
  return targets.flatMap(({ at, text }) => {
    const end = rayEnd(from, at, context.viewport);
    return [
      { kind: 'segment', x1: from.x, y1: from.y, x2: end.x, y2: end.y, ...stroke, stroke: color } as DrawingShape,
      label(text, at, color),
    ];
  });
}

export const fibSpeedFanTool = defineDrawingTool({
  id: 'fib-speed-fan',
  label: 'Fib speed resistance fan',
  displayName: 'Fib Speed Resistance Fan',
  group: 'fibonacci',
  creation: { gesture: 'drag' },
  defaultProperties: { priceLevels: FAN_LEVELS, timeLevels: FAN_LEVELS, showGrid: true },
  propertySchema: [
    { key: 'priceLevels', label: 'Price levels', type: 'number-list', min: 0, max: 1 },
    { key: 'timeLevels', label: 'Time levels', type: 'number-list', min: 0, max: 1 },
    { key: 'showGrid', label: 'Grid', type: 'boolean' },
  ],
  geometry: (context) => {
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    const color = stroke.stroke ?? context.style.color;
    const priceLevels = numberListProperty(context.properties, 'priceLevels', FAN_LEVELS);
    const timeLevels = numberListProperty(context.properties, 'timeLevels', FAN_LEVELS);
    const dx = second.x - first.x;
    const dy = second.y - first.y;
    const shapes: DrawingShape[] = [];
    if (booleanProperty(context.properties, 'showGrid', true)) {
      const left = Math.min(first.x, second.x);
      const top = Math.min(first.y, second.y);
      shapes.push({ kind: 'rect', x: left, y: top, width: Math.abs(dx), height: Math.abs(dy), ...stroke, ...areaFill(color), strokeWidth: 1, hit: 'none' });
      for (const level of priceLevels) {
        const y = first.y + dy * level;
        shapes.push({ kind: 'segment', x1: left, y1: y, x2: left + Math.abs(dx), y2: y, ...stroke, strokeWidth: 1, dash: [2, 3], hit: 'none' });
      }
      for (const level of timeLevels) {
        const x = first.x + dx * level;
        shapes.push({ kind: 'segment', x1: x, y1: top, x2: x, y2: top + Math.abs(dy), ...stroke, strokeWidth: 1, dash: [2, 3], hit: 'none' });
      }
    }
    const targets = [
      { at: second, text: '1' },
      ...priceLevels.map((level) => ({ at: { x: second.x, y: first.y + dy * level }, text: String(level) })),
      ...timeLevels.map((level) => ({ at: { x: first.x + dx * level, y: second.y }, text: String(level) })),
    ];
    return [...shapes, ...rays(context, first, targets, color)];
  },
});

/** A circular arc around `center` from `from` to `to` radians (screen y grows down), as cubic Beziers of at most 90 degrees. */
export function arcCommands(center: ScreenPoint, radius: number, from: number, to: number): PathCommand[] {
  const at = (angle: number): ScreenPoint => ({ x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius });
  const pieces = Math.max(1, Math.ceil(Math.abs(to - from) / (Math.PI / 2)));
  const sweep = (to - from) / pieces;
  const handle = (4 / 3) * Math.tan(sweep / 4) * radius;
  const start = at(from);
  const commands: PathCommand[] = [{ op: 'M', x: start.x, y: start.y }];
  for (let piece = 0; piece < pieces; piece += 1) {
    const a0 = from + sweep * piece;
    const a1 = a0 + sweep;
    const p0 = at(a0);
    const p3 = at(a1);
    commands.push({
      op: 'C',
      c1x: p0.x - Math.sin(a0) * handle,
      c1y: p0.y + Math.cos(a0) * handle,
      c2x: p3.x + Math.sin(a1) * handle,
      c2y: p3.y - Math.cos(a1) * handle,
      x: p3.x,
      y: p3.y,
    });
  }
  return commands;
}

function arcPoint(center: ScreenPoint, radius: number, angle: number): ScreenPoint {
  return { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius };
}

const ARC_LEVELS: readonly number[] = [0.236, 0.382, 0.5, 0.618, 0.786, 1];

export const fibArcsTool = defineDrawingTool({
  id: 'fib-arcs',
  label: 'Fib speed resistance arcs',
  displayName: 'Fib Speed Resistance Arcs',
  group: 'fibonacci',
  creation: { gesture: 'drag' },
  defaultProperties: { levels: ARC_LEVELS, fullCircles: false },
  propertySchema: [
    { key: 'levels', label: 'Levels', type: 'number-list', min: 0 },
    { key: 'fullCircles', label: 'Full circles', type: 'boolean' },
  ],
  geometry: (context) => {
    const [center, edge] = context.points;
    const stroke = lineStroke(context);
    const color = stroke.stroke ?? context.style.color;
    const radius = Math.hypot(edge.x - center.x, edge.y - center.y);
    const full = booleanProperty(context.properties, 'fullCircles', false);
    // Arcs open towards B: the half circle on B's side of the horizontal through A.
    const [from, to] = full ? [0, 2 * Math.PI] : edge.y <= center.y ? [Math.PI, 2 * Math.PI] : [0, Math.PI];
    const shapes: DrawingShape[] = [{ kind: 'segment', x1: center.x, y1: center.y, x2: edge.x, y2: edge.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
    for (const level of numberListProperty(context.properties, 'levels', ARC_LEVELS)) {
      const r = radius * level;
      if (r <= 0) continue;
      if (full) shapes.push({ kind: 'ellipse', cx: center.x, cy: center.y, rx: r, ry: r, ...stroke });
      else shapes.push({ kind: 'path', commands: arcCommands(center, r, from, to), ...stroke });
      shapes.push(label(String(level), arcPoint(center, r, (from + to) / 2), color));
    }
    return shapes;
  },
});

const CIRCLE_LEVELS: readonly number[] = [0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618];

export const fibCirclesTool = defineDrawingTool({
  id: 'fib-circles',
  label: 'Fib circles',
  displayName: 'Fib Circles',
  group: 'fibonacci',
  creation: { gesture: 'drag' },
  defaultProperties: { levels: CIRCLE_LEVELS },
  propertySchema: [{ key: 'levels', label: 'Levels', type: 'number-list', min: 0 }],
  geometry: (context) => {
    // The circle through A and B has its centre midway; the others share the centre at radius x level.
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    const color = stroke.stroke ?? context.style.color;
    const center = { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 };
    const radius = Math.hypot(second.x - first.x, second.y - first.y) / 2;
    const shapes: DrawingShape[] = [{ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
    for (const level of numberListProperty(context.properties, 'levels', CIRCLE_LEVELS)) {
      const r = radius * level;
      if (r <= 0) continue;
      shapes.push({ kind: 'ellipse', cx: center.x, cy: center.y, rx: r, ry: r, ...stroke }, label(String(level), { x: center.x + r, y: center.y }, color));
    }
    return shapes;
  },
});

const GOLDEN_RATIO = (1 + Math.sqrt(5)) / 2;

export const fibSpiralTool = defineDrawingTool({
  id: 'fib-spiral',
  label: 'Fib spiral',
  displayName: 'Fib Spiral',
  group: 'fibonacci',
  creation: { gesture: 'drag' },
  defaultProperties: { counterClockwise: false },
  propertySchema: [{ key: 'counterClockwise', label: 'Counter-clockwise', type: 'boolean' }],
  geometry: (context) => {
    // A golden spiral around A through B: the radius grows by the golden ratio every quarter turn.
    const [center, edge] = context.points;
    const stroke = lineStroke(context);
    const radius = Math.hypot(edge.x - center.x, edge.y - center.y);
    const guide: DrawingShape = { kind: 'segment', x1: center.x, y1: center.y, x2: edge.x, y2: edge.y, ...stroke, dash: [4, 4], strokeWidth: 1 };
    // A click without a drag still leaves something to see and select.
    if (radius < 1) return [guide, { kind: 'marker', x: center.x, y: center.y, radius: 3, fill: context.style.color }];
    const start = Math.atan2(edge.y - center.y, edge.x - center.x);
    const direction = booleanProperty(context.properties, 'counterClockwise', false) ? -1 : 1;
    const growth = Math.log(GOLDEN_RATIO) / (Math.PI / 2);
    // Out to the farthest viewport corner from the centre (the centre may be off-screen), plus a margin.
    const corners = [[0, 0], [context.viewport.width, 0], [0, context.viewport.height], [context.viewport.width, context.viewport.height]];
    const limit = Math.max(...corners.map(([x, y]) => Math.hypot(x - center.x, y - center.y))) * 1.2;
    const points: ScreenPoint[] = [];
    // Steps of at most 4 px along the arm where it can cross the view; coarse (π/32) while it is still nearer the
    // centre than the view's nearest point, so a far-off centre doesn't spend the point cap before the view.
    const nearest = Math.hypot(
      Math.max(0 - center.x, 0, center.x - context.viewport.width),
      Math.max(0 - center.y, 0, center.y - context.viewport.height),
    );
    for (let turn = -6 * Math.PI; turn <= 40 * Math.PI && points.length < 6_000;) {
      const r = radius * Math.exp(growth * turn);
      if (r > limit) break;
      if (r >= 0.5) {
        const angle = start + direction * turn;
        points.push({ x: center.x + Math.cos(angle) * r, y: center.y + Math.sin(angle) * r });
      }
      turn += r < nearest - 8 ? Math.PI / 32 : Math.min(Math.PI / 32, 4 / Math.max(r, 1));
    }
    return [guide, { kind: 'polyline', points, ...stroke }];
  },
});

const WEDGE_LEVELS: readonly number[] = [0.236, 0.382, 0.5, 0.618, 0.786, 1];

export const fibWedgeTool = defineDrawingTool({
  id: 'fib-wedge',
  label: 'Fib wedge',
  displayName: 'Fib Wedge',
  group: 'fibonacci',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { levels: WEDGE_LEVELS },
  propertySchema: [{ key: 'levels', label: 'Levels', type: 'number-list', min: 0 }],
  geometry: (context) => {
    // A apex; A-B and A-C the wedge's edges; arcs between the edges at |AB| x level.
    const [apex, first, second] = context.points;
    const stroke = lineStroke(context);
    const color = stroke.stroke ?? context.style.color;
    const shapes: DrawingShape[] = [{ kind: 'segment', x1: apex.x, y1: apex.y, x2: first.x, y2: first.y, ...stroke }];
    if (!second) return shapes;
    shapes.push({ kind: 'segment', x1: apex.x, y1: apex.y, x2: second.x, y2: second.y, ...stroke });
    const radius = Math.hypot(first.x - apex.x, first.y - apex.y);
    const from = Math.atan2(first.y - apex.y, first.x - apex.x);
    let to = Math.atan2(second.y - apex.y, second.x - apex.x);
    // The smaller angle between the edges.
    if (to - from > Math.PI) to -= 2 * Math.PI;
    if (from - to > Math.PI) to += 2 * Math.PI;
    for (const level of numberListProperty(context.properties, 'levels', WEDGE_LEVELS)) {
      if (radius * level <= 0) continue;
      shapes.push({ kind: 'path', commands: arcCommands(apex, radius * level, from, to), ...stroke }, label(String(level), arcPoint(apex, radius * level, to), color));
    }
    return shapes;
  },
});
