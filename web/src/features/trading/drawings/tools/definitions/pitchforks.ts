// Pitchforks, pitchfan and Gann tools (TVP-3.3).
//
// A pitchfork is three clicks: A, then B and C, the swing high and low. Its median line runs from an origin through
// the midpoint M of B-C; the tines are parallels through M + (B - M) x level on either side. The variants differ in
// the origin only: A (Andrews), A moved halfway to B in price (Schiff), or the midpoint of A-B in price and time
// (modified Schiff, and the inside pitchfork, which TradingView documents with that same origin).
// Lines are built in bar index/price and projected, so the drawing and its alerts agree.
import { lineAlertLevel, lineThroughPoint } from '../alertLevels';
import { booleanProperty, numberListProperty, numberProperty, recordsProperty } from '../properties';
import { areaFill, constrainToSquare, lineStroke, rayEnd } from '../shapes';
import {
  anchorHandle,
  defineDrawingTool,
  type DrawingAlertLevel,
  type DrawingGeometryContext,
  type DrawingPoint,
  type DrawingProperties,
  type DrawingPropertyRecord,
  type DrawingShape,
  type DrawingToolServices,
  type ScreenPoint,
} from '../types';

type PitchforkKind = 'andrews' | 'schiff' | 'modified-schiff' | 'inside';
type Services = Pick<DrawingToolServices, 'barIndexForTime' | 'timeForBarIndex'>;

/** A point at a fractional bar index between two anchors (bar-index space, like the chart and the alerts). */
function between(first: DrawingPoint, second: DrawingPoint, share: number, services: Services): DrawingPoint | null {
  const a = services.barIndexForTime(first.time);
  const b = services.barIndexForTime(second.time);
  if (a === null || b === null) return null;
  const time = services.timeForBarIndex(a + (b - a) * share);
  return time ? { time, price: first.price + (second.price - first.price) * share } : null;
}

/** The pitchfork's origin, median target M, and the tine points at `level` (B side, C side), in time/price. */
export function pitchforkPoints(kind: PitchforkKind, points: readonly DrawingPoint[], services: Services): { origin: DrawingPoint; middle: DrawingPoint } | null {
  const [a, b, c] = points;
  if (!a || !b || !c) return null;
  const middle = between(b, c, 0.5, services);
  if (!middle) return null;
  if (kind === 'andrews') return { origin: a, middle };
  if (kind === 'schiff') return { origin: { time: a.time, price: (a.price + b.price) / 2 }, middle };
  const origin = between(a, b, 0.5, services);
  return origin ? { origin, middle } : null;
}

const PITCHFORK_LEVELS: readonly DrawingPropertyRecord[] = [0.25, 0.382, 0.5, 0.618, 0.75, 1, 1.5, 1.75, 2].map((value) => ({
  value,
  color: '',
  visible: value === 0.5 || value === 1,
}));

type Tine = { key: string; value: number; color: string; side: 1 | -1 };

function tines(properties: DrawingProperties, stroke: string): Tine[] {
  const seen = new Map<number, number>();
  return recordsProperty(properties, 'levels', PITCHFORK_LEVELS).flatMap((record): Tine[] => {
    const value = record.value;
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return [];
    const count = (seen.get(value) ?? 0) + 1;
    seen.set(value, count);
    if (record.visible === false) return [];
    const key = count === 1 ? `level-${value}` : `level-${value}-${count}`;
    const color = typeof record.color === 'string' && record.color ? record.color : stroke;
    return [{ key: `${key}-upper`, value, color, side: 1 }, { key: `${key}-lower`, value, color, side: -1 }];
  });
}

/** The tine through M + (B - M) x level (side 1) or M + (C - M) x level (side -1). */
function tinePoint(points: readonly DrawingPoint[], middle: DrawingPoint, value: number, side: 1 | -1, services: Services): DrawingPoint | null {
  const toward = side === 1 ? points[1] : points[2];
  return toward ? between(middle, toward, value, services) : null;
}

/**
 * The point one median-length further along the median from `point`, in bar index/price: a tine is the line through
 * its start and this point, exactly the line its alert follows (`lineThroughPoint`), on any price scale.
 */
function alongMedian(point: DrawingPoint, fork: { origin: DrawingPoint; middle: DrawingPoint }, services: Services): DrawingPoint | null {
  const start = services.barIndexForTime(point.time);
  const origin = services.barIndexForTime(fork.origin.time);
  const middle = services.barIndexForTime(fork.middle.time);
  if (start === null || origin === null || middle === null || middle === origin) return null;
  const time = services.timeForBarIndex(start + (middle - origin));
  return time ? { time, price: point.price + (fork.middle.price - fork.origin.price) } : null;
}

function pitchforkGeometry(kind: PitchforkKind, context: DrawingGeometryContext): DrawingShape[] {
  const [a, b, c] = context.points;
  const stroke = lineStroke(context);
  const color = stroke.stroke ?? context.style.color;
  if (!c) return [{ kind: 'segment', x1: a.x, y1: a.y, x2: b.x, y2: b.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
  const shapes: DrawingShape[] = [{ kind: 'segment', x1: b.x, y1: b.y, x2: c.x, y2: c.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
  const fork = pitchforkPoints(kind, context.rawPoints, context);
  const origin = fork ? context.project(fork.origin) : null;
  const middle = fork ? context.project(fork.middle) : null;
  if (!fork || !origin || !middle) return shapes;
  if (kind !== 'andrews') shapes.push({ kind: 'segment', x1: a.x, y1: a.y, x2: origin.x, y2: origin.y, ...stroke, dash: [4, 4], strokeWidth: 1 });
  const direction = { x: middle.x - origin.x, y: middle.y - origin.y };
  const ray = (from: ScreenPoint): ScreenPoint => rayEnd(from, { x: from.x + direction.x, y: from.y + direction.y }, context.viewport);
  const medianEnd = ray(middle);
  shapes.push({ kind: 'segment', x1: origin.x, y1: origin.y, x2: medianEnd.x, y2: medianEnd.y, ...stroke });
  const projected = tines(context.properties, color).flatMap((tine) => {
    const point = tinePoint(context.rawPoints, fork.middle, tine.value, tine.side, context);
    const start = point ? context.project(point) : null;
    if (!point || !start) return [];
    const further = alongMedian(point, fork, context);
    const through = further ? context.project(further) : null;
    return [{ ...tine, start, end: through ? rayEnd(start, through, context.viewport) : ray(start) }];
  });
  // Fill between the outermost tines on each side.
  if (booleanProperty(context.properties, 'fill', true)) {
    const upper = projected.filter((tine) => tine.side === 1).sort((x, y) => y.value - x.value)[0];
    const lower = projected.filter((tine) => tine.side === -1).sort((x, y) => y.value - x.value)[0];
    if (upper && lower) shapes.unshift({ kind: 'polygon', points: [upper.start, upper.end, lower.end, lower.start], ...areaFill(color), hit: 'none' });
  }
  for (const tine of projected) shapes.push({ kind: 'segment', x1: tine.start.x, y1: tine.start.y, x2: tine.end.x, y2: tine.end.y, ...stroke, stroke: tine.color });
  return shapes;
}

function pitchforkAlertLevels(kind: PitchforkKind, points: readonly DrawingPoint[], properties: DrawingProperties, services: DrawingToolServices): DrawingAlertLevel[] {
  const fork = pitchforkPoints(kind, points, services);
  if (!fork) return [];
  const median = lineAlertLevel('median', 'Median line', fork.origin, fork.middle, 'right');
  if (!median) return [];
  const levels: DrawingAlertLevel[] = [median];
  for (const tine of tines(properties, '')) {
    const point = tinePoint(points, fork.middle, tine.value, tine.side, services);
    const level = point ? lineThroughPoint(tine.key, `${tine.side === 1 ? 'B' : 'C'} side ${tine.value}`, median, point, services) : null;
    if (level) levels.push(level);
  }
  return levels;
}

function makePitchfork<const Id extends string>(id: Id, label: string, displayName: string, kind: PitchforkKind) {
  return defineDrawingTool({
    id,
    label,
    displayName,
    group: 'pitchforks',
    creation: { gesture: 'click-click', anchors: 3 },
    draftPreview: 'shapes',
    previewAnchors: 2,
    defaultProperties: { levels: PITCHFORK_LEVELS, fill: true },
    propertySchema: [
      {
        key: 'levels',
        label: 'Levels',
        type: 'records',
        fields: [
          { key: 'value', label: 'Level', type: 'number', min: 0, step: 0.001 },
          { key: 'color', label: 'Colour', type: 'color' },
          { key: 'visible', label: 'Visible', type: 'boolean' },
        ],
        newRecord: { value: 3, color: '', visible: true },
      },
      { key: 'fill', label: 'Background', type: 'boolean' },
    ],
    geometry: (context) => pitchforkGeometry(kind, context),
    alertLevels: (points, properties, services) => pitchforkAlertLevels(kind, points, properties, services),
  });
}

export const pitchforkTool = makePitchfork('pitchfork', 'Pitchfork', 'Pitchfork', 'andrews');
export const schiffPitchforkTool = makePitchfork('schiff-pitchfork', 'Schiff pitchfork', 'Schiff Pitchfork', 'schiff');
export const modifiedSchiffPitchforkTool = makePitchfork('modified-schiff-pitchfork', 'Modified Schiff pitchfork', 'Modified Schiff Pitchfork', 'modified-schiff');
export const insidePitchforkTool = makePitchfork('inside-pitchfork', 'Inside pitchfork', 'Inside Pitchfork', 'inside');

// Pitchfan: rays from A through B-C at the levels.

const PITCHFAN_LEVELS: readonly number[] = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];

export const pitchfanTool = defineDrawingTool({
  id: 'pitchfan',
  label: 'Pitchfan',
  displayName: 'Pitchfan',
  group: 'fibonacci',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { levels: PITCHFAN_LEVELS },
  propertySchema: [{ key: 'levels', label: 'Levels', type: 'number-list', min: 0, max: 1 }],
  geometry: (context) => {
    const [a, b, c] = context.points;
    const stroke = lineStroke(context);
    if (!c) return [{ kind: 'segment', x1: a.x, y1: a.y, x2: b.x, y2: b.y, ...stroke }];
    const shapes: DrawingShape[] = [{ kind: 'segment', x1: b.x, y1: b.y, x2: c.x, y2: c.y, ...stroke, dash: [4, 4], strokeWidth: 1 }];
    for (const level of numberListProperty(context.properties, 'levels', PITCHFAN_LEVELS)) {
      const through = { x: b.x + (c.x - b.x) * level, y: b.y + (c.y - b.y) * level };
      const end = rayEnd(a, through, context.viewport);
      shapes.push({ kind: 'segment', x1: a.x, y1: a.y, x2: end.x, y2: end.y, ...stroke });
    }
    return shapes;
  },
});

// Gann tools.

const GANN_LEVELS: readonly number[] = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];

function gannGrid(
  context: DrawingGeometryContext,
  priceLevels: readonly number[],
  timeLevels: readonly number[],
  labels: boolean,
  [first, second]: readonly ScreenPoint[] = context.points,
): DrawingShape[] {
  const stroke = lineStroke(context);
  const color = stroke.stroke ?? context.style.color;
  const left = Math.min(first.x, second.x);
  const right = Math.max(first.x, second.x);
  const top = Math.min(first.y, second.y);
  const bottom = Math.max(first.y, second.y);
  const shapes: DrawingShape[] = [{ kind: 'rect', x: left, y: top, width: right - left, height: bottom - top, ...stroke, ...areaFill(color) }];
  for (const level of priceLevels) {
    const y = first.y + (second.y - first.y) * level;
    shapes.push({ kind: 'segment', x1: left, y1: y, x2: right, y2: y, ...stroke, strokeWidth: 1 });
    if (labels) shapes.push({ kind: 'text', x: left - 4, y: y + 4, text: String(level), align: 'end', fill: color, hit: 'none' });
  }
  for (const level of timeLevels) {
    const x = first.x + (second.x - first.x) * level;
    shapes.push({ kind: 'segment', x1: x, y1: top, x2: x, y2: bottom, ...stroke, strokeWidth: 1 });
    if (labels) shapes.push({ kind: 'text', x, y: bottom + 14, text: String(level), align: 'middle', fill: color, hit: 'none' });
  }
  return shapes;
}

export const gannBoxTool = defineDrawingTool({
  id: 'gann-box',
  label: 'Gann box',
  displayName: 'Gann Box',
  group: 'gann',
  creation: { gesture: 'drag' },
  // With Shift the box keeps a square shape, so its angles are true 1x1 steps on the screen.
  constrain: constrainToSquare,
  defaultProperties: { priceLevels: GANN_LEVELS, timeLevels: GANN_LEVELS, showLabels: true, showAngles: false },
  propertySchema: [
    { key: 'priceLevels', label: 'Price levels', type: 'number-list', min: 0, max: 1 },
    { key: 'timeLevels', label: 'Time levels', type: 'number-list', min: 0, max: 1 },
    { key: 'showLabels', label: 'Labels', type: 'boolean' },
    { key: 'showAngles', label: 'Angles', type: 'boolean' },
  ],
  geometry: (context) => {
    const shapes = gannGrid(
      context,
      numberListProperty(context.properties, 'priceLevels', GANN_LEVELS),
      numberListProperty(context.properties, 'timeLevels', GANN_LEVELS),
      booleanProperty(context.properties, 'showLabels', true),
    );
    if (booleanProperty(context.properties, 'showAngles', false)) {
      const [first, second] = context.points;
      shapes.push({ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...lineStroke(context), dash: [4, 4], strokeWidth: 1 });
      shapes.push({ kind: 'segment', x1: first.x, y1: second.y, x2: second.x, y2: first.y, ...lineStroke(context), dash: [4, 4], strokeWidth: 1 });
    }
    return shapes;
  },
});

const SQUARE_LEVELS: readonly number[] = [0.25, 0.382, 0.5, 0.618, 0.75];

/** A Gann square from A to the corner: level grid, diagonals, fans from A through the levels, and arcs from A. */
function gannSquareGeometry(context: DrawingGeometryContext, corner: ScreenPoint): DrawingShape[] {
  const first = context.points[0];
  const second = corner;
  const stroke = lineStroke(context);
  const levels = numberListProperty(context.properties, 'levels', SQUARE_LEVELS);
  const shapes = gannGrid(context, levels, levels, booleanProperty(context.properties, 'showLabels', true), [first, second]);
  shapes.push(
    { kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke, strokeWidth: 1 },
    { kind: 'segment', x1: first.x, y1: second.y, x2: second.x, y2: first.y, ...stroke, strokeWidth: 1 },
  );
  const width = second.x - first.x;
  const height = second.y - first.y;
  if (booleanProperty(context.properties, 'showFans', true)) {
    // From A to each level on the far sides.
    for (const level of levels) {
      shapes.push(
        { kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: first.y + height * level, ...stroke, strokeWidth: 1, dash: [2, 3] },
        { kind: 'segment', x1: first.x, y1: first.y, x2: first.x + width * level, y2: second.y, ...stroke, strokeWidth: 1, dash: [2, 3] },
      );
    }
  }
  if (booleanProperty(context.properties, 'showArcs', true)) {
    for (const share of [...levels, 1]) {
      // A quarter ellipse from A, spanning the box's share in time and in price.
      const points = Array.from({ length: 25 }, (_, index) => {
        const angle = (Math.PI / 2) * index / 24;
        return { x: first.x + width * share * Math.cos(angle), y: first.y + height * share * Math.sin(angle) };
      });
      shapes.push({ kind: 'polyline', points, ...stroke, strokeWidth: 1, dash: [3, 3] });
    }
  }
  return shapes;
}

const SQUARE_FIELDS = [
  { key: 'levels', label: 'Levels', type: 'number-list', min: 0, max: 1 },
  { key: 'showLabels', label: 'Labels', type: 'boolean' },
  { key: 'showFans', label: 'Fans', type: 'boolean' },
  { key: 'showArcs', label: 'Arcs', type: 'boolean' },
] as const;

export const gannSquareTool = defineDrawingTool({
  id: 'gann-square',
  label: 'Gann square',
  displayName: 'Gann Square',
  group: 'gann',
  creation: { gesture: 'drag' },
  constrain: constrainToSquare,
  defaultProperties: { levels: SQUARE_LEVELS, showLabels: true, showFans: true, showArcs: true },
  propertySchema: SQUARE_FIELDS,
  geometry: (context) => gannSquareGeometry(context, context.points[1]),
});

/**
 * Gann square fixed: its scale (price per bar) is fixed when it is drawn, so it stays a true square in price and
 * time on any zoom; moving B changes only its size (bars), the price side follows the scale.
 */
export function gannFixedCorner(points: readonly DrawingPoint[], pricePerBar: number, services: Pick<DrawingToolServices, 'barIndexForTime'>): DrawingPoint | null {
  const [first, second] = points;
  const start = first ? services.barIndexForTime(first.time) : null;
  const end = second ? services.barIndexForTime(second.time) : null;
  if (start === null || end === null) return null;
  // No scale yet (a click without a drag, or drawn before bars loaded): B as placed.
  if (!(pricePerBar > 0)) return { time: second.time, price: second.price };
  const direction = second.price >= first.price ? 1 : -1;
  return { time: second.time, price: first.price + direction * Math.abs(end - start) * pricePerBar };
}

export const gannSquareFixedTool = defineDrawingTool({
  id: 'gann-square-fixed',
  label: 'Gann square fixed',
  displayName: 'Gann Square Fixed',
  group: 'gann',
  creation: { gesture: 'drag' },
  defaultProperties: { pricePerBar: 0, levels: SQUARE_LEVELS, showLabels: true, showFans: true, showArcs: true },
  propertySchema: [{ key: 'pricePerBar', label: 'Price per bar', type: 'number', min: 0 }, ...SQUARE_FIELDS],
  // The scale is taken from the drag: B's price over its bars from A.
  onCreate: (anchors, services) => {
    const [first, second] = anchors;
    const start = first ? services.barIndexForTime(first.time) : null;
    const end = second ? services.barIndexForTime(second.time) : null;
    const bars = start === null || end === null ? 0 : Math.abs(end - start);
    return { points: [...anchors], properties: { pricePerBar: bars > 0 ? Math.abs(second.price - first.price) / bars : 0 } };
  },
  // B's handle sits on the drawn corner; dragging it changes the size (bars) and keeps the scale.
  handles: (context) => {
    const pricePerBar = numberProperty(context.properties, 'pricePerBar', 0);
    const corner = gannFixedCorner(context.rawPoints, pricePerBar, context);
    const at = (corner ? context.project(corner) : null) ?? context.points[1];
    return [
      anchorHandle(0, context.points[0]),
      {
        id: 'anchor-1',
        x: at.x,
        y: at.y,
        drag: ({ points, point, properties, services }) => {
          const moved = [points[0], { ...points[1], ...point }];
          const next = gannFixedCorner(moved, numberProperty(properties, 'pricePerBar', 0), services);
          return { points: [points[0], next ? { ...moved[1], price: next.price } : moved[1]] };
        },
      },
    ];
  },
  geometry: (context) => {
    const corner = gannFixedCorner(context.rawPoints, numberProperty(context.properties, 'pricePerBar', 0), context);
    const projected = corner ? context.project(corner) : null;
    return gannSquareGeometry(context, projected ?? context.points[1]);
  },
});

/** Gann fan angles as (time units, price units) per step; 1x1 passes through B. */
const GANN_ANGLES: ReadonlyArray<readonly [number, number, string]> = [
  [8, 1, '1x8'], [4, 1, '1x4'], [3, 1, '1x3'], [2, 1, '1x2'], [1, 1, '1x1'], [1, 2, '2x1'], [1, 3, '3x1'], [1, 4, '4x1'], [1, 8, '8x1'],
];

export const gannFanTool = defineDrawingTool({
  id: 'gann-fan',
  label: 'Gann fan',
  displayName: 'Gann Fan',
  group: 'gann',
  creation: { gesture: 'drag' },
  defaultProperties: { showLabels: true },
  propertySchema: [{ key: 'showLabels', label: 'Labels', type: 'boolean' }],
  geometry: (context) => {
    const [origin, through] = context.points;
    const stroke = lineStroke(context);
    const color = stroke.stroke ?? context.style.color;
    const dx = through.x - origin.x;
    const dy = through.y - origin.y;
    const labels = booleanProperty(context.properties, 'showLabels', true);
    return GANN_ANGLES.flatMap(([time, price, name]): DrawingShape[] => {
      // 1x1 through B; NxM takes N price units per M time units of the B box.
      const toward = time >= price ? { x: origin.x + dx, y: origin.y + dy / time } : { x: origin.x + dx / price, y: origin.y + dy };
      const end = rayEnd(origin, toward, context.viewport);
      const shapes: DrawingShape[] = [{ kind: 'segment', x1: origin.x, y1: origin.y, x2: end.x, y2: end.y, ...stroke, strokeWidth: name === '1x1' ? stroke.strokeWidth : 1 }];
      if (labels) shapes.push({ kind: 'text', x: toward.x + 4, y: toward.y - 2, text: name, fill: color, hit: 'none' });
      return shapes;
    });
  },
});
