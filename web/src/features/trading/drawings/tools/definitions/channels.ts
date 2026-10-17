// Channels (TVP-3.1): parallel channel, flat top/bottom and disjoint channel.
//
// Each is three clicks: two anchors for the first line, a third that places the
// other line. Every line is built once in time/price (`channelLines`) and both
// drawn (projected) and alerted on from there, so the drawing and its alerts
// agree on every price scale. Alert levels are two-anchor lines, straight in
// bar-index space (`barTimeline.ts`).
import { alertLevelPriceAt, lineAlertLevel } from '../alertLevels';
import { booleanProperty } from '../properties';
import { areaFill, extendedSegment, lineStroke } from '../shapes';
import {
  anchorHandle,
  defineDrawingTool,
  type DrawingAlertExtend,
  type DrawingAlertLevel,
  type DrawingGeometryContext,
  type DrawingHandle,
  type DrawingHandleDrag,
  type DrawingPoint,
  type DrawingProperties,
  type DrawingPropertyField,
  type DrawingShape,
  type DrawingToolServices,
  type ScreenPoint,
} from '../types';

const EXTEND_FIELDS: readonly DrawingPropertyField[] = [
  { key: 'extendLeft', label: 'Extend left', type: 'boolean' },
  { key: 'extendRight', label: 'Extend right', type: 'boolean' },
  { key: 'fill', label: 'Background', type: 'boolean' },
];

function extendOf(properties: DrawingProperties): DrawingAlertExtend {
  const left = booleanProperty(properties, 'extendLeft', false);
  const right = booleanProperty(properties, 'extendRight', false);
  return left && right ? 'both' : left ? 'left' : right ? 'right' : 'none';
}

type Line = readonly [DrawingPoint, DrawingPoint];
type ChannelKind = 'parallel' | 'flat' | 'disjoint';
/** A channel's lines in time/price: the first (`base`), the other (`other`) and, for the parallel channel, the middle. */
type ChannelLines = { base: Line; other: Line; middle?: Line };

const shifted = ([first, second]: Line, offset: number): Line => [
  { time: first.time, price: first.price + offset },
  { time: second.time, price: second.price + offset },
];

/** The price of the line through `first` and `second` at `time`'s bar index; null without an index. */
export function linePriceAt(first: DrawingPoint, second: DrawingPoint, time: string, services: Pick<DrawingToolServices, 'barIndexForTime'>): number | null {
  const level = lineAlertLevel('line', '', first, second, 'none');
  const index = services.barIndexForTime(time);
  return level && index !== null ? alertLevelPriceAt(level, index, services.barIndexForTime, true) : null;
}

/** The lines of a three-anchor channel, or null while it has fewer anchors or the parallel's offset is unknown. */
export function channelLines(kind: ChannelKind, points: readonly DrawingPoint[], services: Pick<DrawingToolServices, 'barIndexForTime'>): ChannelLines | null {
  const [first, second, third] = points;
  if (!first || !second || !third) return null;
  const base: Line = [first, second];
  if (kind === 'flat') return { base, other: [{ time: first.time, price: third.price }, { time: second.time, price: third.price }] };
  if (kind === 'disjoint') {
    return { base, other: [{ time: first.time, price: third.price }, { time: second.time, price: third.price - (second.price - first.price) }] };
  }
  const onBase = linePriceAt(first, second, third.time, services);
  if (onBase === null) return null;
  const offset = third.price - onBase;
  return { base, other: shifted(base, offset), middle: shifted(base, offset / 2) };
}

function projectLine(context: DrawingGeometryContext, [first, second]: Line): [ScreenPoint, ScreenPoint] | null {
  const start = context.project(first);
  const end = context.project(second);
  return start && end ? [start, end] : null;
}

function extended(context: DrawingGeometryContext, [first, second]: readonly [ScreenPoint, ScreenPoint]): [ScreenPoint, ScreenPoint] {
  return extendedSegment(
    first,
    second,
    context.viewport,
    booleanProperty(context.properties, 'extendLeft', false),
    booleanProperty(context.properties, 'extendRight', false),
  );
}

function segment(context: DrawingGeometryContext, [start, end]: readonly [ScreenPoint, ScreenPoint], extra: Partial<Pick<DrawingShape, 'dash' | 'strokeWidth'>> = {}): DrawingShape {
  return { kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...lineStroke(context), ...extra };
}

/** The channel drawn from its time/price lines; the fill spans the extended lines too. */
function channelGeometry(kind: ChannelKind, context: DrawingGeometryContext): DrawingShape[] {
  const [first, second] = context.points;
  const baseOnly = [segment(context, extended(context, [first, second]))];
  const lines = channelLines(kind, context.rawPoints, context);
  if (!lines) return baseOnly;
  const base = extended(context, [first, second]);
  const otherProjected = projectLine(context, lines.other);
  if (!otherProjected) return baseOnly;
  const other = extended(context, otherProjected);
  const shapes: DrawingShape[] = [];
  if (booleanProperty(context.properties, 'fill', true)) {
    shapes.push({ kind: 'polygon', points: [base[0], base[1], other[1], other[0]], ...areaFill(context.style.color) });
  }
  shapes.push(segment(context, base), segment(context, other));
  const middle = lines.middle && booleanProperty(context.properties, 'showMiddle', true) ? projectLine(context, lines.middle) : null;
  if (middle) shapes.push(segment(context, extended(context, middle), { dash: [4, 4], strokeWidth: 1 }));
  return shapes;
}

/**
 * Moves anchor `index` of the channel's first line and keeps the channel's width: the third anchor stays at its
 * time, at the same price distance from the moved line as before.
 */
export function moveKeepingWidth({ points, point, services }: Pick<DrawingHandleDrag, 'points' | 'point' | 'services'>, index: 0 | 1): DrawingPoint[] {
  const next = points.map((existing, position) => (position === index ? { ...existing, ...point } : existing));
  const [first, second, third] = points;
  if (!first || !second || !third) return next;
  const was = linePriceAt(first, second, third.time, services);
  const now = linePriceAt(next[0], next[1], third.time, services);
  if (was !== null && now !== null) next[2] = { ...third, price: now + (third.price - was) };
  return next;
}

/** Handles at both ends of the other line; dragging one sets only the third anchor's price. */
function otherLineHandles(kind: 'flat' | 'disjoint', context: DrawingGeometryContext): DrawingHandle[] {
  const [first, second] = context.points;
  const handles: DrawingHandle[] = [anchorHandle(0, first), anchorHandle(1, second)];
  const lines = channelLines(kind, context.rawPoints, context);
  const ends = lines ? projectLine(context, lines.other) : null;
  if (!ends) return handles;
  ends.forEach((position, end) => {
    handles.push({
      id: `other-${end}`,
      x: position.x,
      y: position.y,
      drag: ({ points, point }) => {
        const [anchor, next, third] = points;
        // The second end of the disjoint line sits at the first line's change below (or above) the third anchor.
        const rise = kind === 'disjoint' && end === 1 ? next.price - anchor.price : 0;
        return { points: [anchor, next, { ...third, time: anchor.time, price: point.price + rise }] };
      },
    });
  });
  return handles;
}

function channelAlertLevels(kind: ChannelKind, points: readonly DrawingPoint[], properties: DrawingProperties, services: DrawingToolServices): DrawingAlertLevel[] {
  const lines = channelLines(kind, points, services);
  if (!lines) return [];
  const extend = extendOf(properties);
  const level = (key: string, label: string, [first, second]: Line) => lineAlertLevel(key, label, first, second, extend);
  if (kind !== 'parallel') {
    return [
      level(kind === 'flat' ? 'trend' : 'first', kind === 'flat' ? 'Trend line' : 'First line', lines.base),
      level(kind === 'flat' ? 'flat' : 'second', kind === 'flat' ? 'Flat line' : 'Second line', lines.other),
    ].filter((item): item is DrawingAlertLevel => item !== null);
  }
  const otherAbove = lines.other[0].price > lines.base[0].price;
  const levels = [
    level(otherAbove ? 'lower' : 'upper', otherAbove ? 'Lower line' : 'Upper line', lines.base),
    level(otherAbove ? 'upper' : 'lower', otherAbove ? 'Upper line' : 'Lower line', lines.other),
  ];
  if (lines.middle && booleanProperty(properties, 'showMiddle', true)) levels.push(level('middle', 'Middle line', lines.middle));
  return levels.filter((item): item is DrawingAlertLevel => item !== null);
}

/** Flat and disjoint channels keep their third anchor at the first anchor's time: only its price matters. */
function thirdAtFirstTime(anchors: readonly DrawingPoint[]): { points: DrawingPoint[] } {
  const [first, second, third] = anchors;
  return { points: first && second && third ? [first, second, { ...third, time: first.time }] : [...anchors] };
}

export const parallelChannelTool = defineDrawingTool({
  id: 'parallel-channel',
  label: 'Parallel channel',
  group: 'channels',
  creation: { gesture: 'click-click', anchors: 3 },
  defaultProperties: { extendLeft: false, extendRight: false, fill: true, showMiddle: true },
  propertySchema: [...EXTEND_FIELDS, { key: 'showMiddle', label: 'Middle line', type: 'boolean' }],
  draftPreview: 'shapes',
  previewAnchors: 2,
  // The first line's anchors keep the width; the third anchor (on the other line) sets it.
  handles: (context) => context.points.map((position, index) => (index < 2 && context.points.length === 3
    ? { ...anchorHandle(index, position), drag: (input: DrawingHandleDrag) => ({ points: moveKeepingWidth(input, index as 0 | 1) }) }
    : anchorHandle(index, position))),
  geometry: (context) => channelGeometry('parallel', context),
  alertLevels: (points, properties, services) => channelAlertLevels('parallel', points, properties, services),
});

export const flatTopBottomTool = defineDrawingTool({
  id: 'flat-top-bottom',
  label: 'Flat top/bottom',
  group: 'channels',
  creation: { gesture: 'click-click', anchors: 3 },
  defaultProperties: { extendLeft: false, extendRight: false, fill: true },
  propertySchema: EXTEND_FIELDS,
  draftPreview: 'shapes',
  previewAnchors: 2,
  onCreate: thirdAtFirstTime,
  handles: (context) => otherLineHandles('flat', context),
  geometry: (context) => channelGeometry('flat', context),
  alertLevels: (points, properties, services) => channelAlertLevels('flat', points, properties, services),
});

export const disjointChannelTool = defineDrawingTool({
  id: 'disjoint-channel',
  label: 'Disjoint channel',
  group: 'channels',
  creation: { gesture: 'click-click', anchors: 3 },
  defaultProperties: { extendLeft: false, extendRight: false, fill: true },
  propertySchema: EXTEND_FIELDS,
  draftPreview: 'shapes',
  previewAnchors: 2,
  onCreate: thirdAtFirstTime,
  handles: (context) => otherLineHandles('disjoint', context),
  geometry: (context) => channelGeometry('disjoint', context),
  alertLevels: (points, properties, services) => channelAlertLevels('disjoint', points, properties, services),
});
