// Shapes and freehand (TVP-3.4): brush, highlighter, path, polyline, curve, double curve, triangle, rotated
// rectangle and arc. Freehand strokes are stored as simplified anchors in time/price (the registry's `freehand`
// gesture), so they stay attached to the chart on zoom.
import { booleanProperty, numberProperty } from '../properties';
import { areaFill, arrowHead, lineStroke } from '../shapes';
import { defineDrawingTool, type DrawingGeometryContext, type DrawingShape, type PathCommand, type ScreenPoint } from '../types';

/** A smooth path through the points: quadratic segments through the midpoints (the usual freehand smoothing). */
export function smoothPath(points: readonly ScreenPoint[]): PathCommand[] {
  if (points.length === 0) return [];
  const commands: PathCommand[] = [{ op: 'M', x: points[0].x, y: points[0].y }];
  if (points.length < 3) {
    for (const point of points.slice(1)) commands.push({ op: 'L', x: point.x, y: point.y });
    return commands;
  }
  for (let index = 1; index < points.length - 1; index += 1) {
    const control = points[index];
    const next = points[index + 1];
    const end = index === points.length - 2 ? next : { x: (control.x + next.x) / 2, y: (control.y + next.y) / 2 };
    commands.push({ op: 'Q', cx: control.x, cy: control.y, x: end.x, y: end.y });
  }
  return commands;
}

export const brushTool = defineDrawingTool({
  id: 'brush',
  label: 'Brush',
  displayName: 'Brush',
  group: 'brushes',
  creation: { gesture: 'freehand', minAnchors: 2, simplifyTolerance: 1.5 },
  draftPreview: 'shapes',
  handles: 'ends',
  defaultProperties: { fill: false },
  propertySchema: [{ key: 'fill', label: 'Fill', type: 'boolean' }],
  geometry: (context) => {
    const stroke = lineStroke(context);
    const fill = booleanProperty(context.properties, 'fill', false) ? areaFill(context.style.color) : {};
    return [{ kind: 'path', commands: smoothPath(context.points), ...stroke, ...fill }];
  },
});

export const highlighterTool = defineDrawingTool({
  id: 'highlighter',
  label: 'Highlighter',
  displayName: 'Highlighter',
  group: 'brushes',
  creation: { gesture: 'freehand', minAnchors: 2, simplifyTolerance: 2 },
  draftPreview: 'shapes',
  handles: 'ends',
  defaultProperties: { width: 14 },
  propertySchema: [{ key: 'width', label: 'Width', type: 'number', min: 4, max: 40, step: 1 }],
  geometry: (context) => [{
    kind: 'path',
    commands: smoothPath(context.points),
    stroke: context.style.color,
    strokeWidth: numberProperty(context.properties, 'width', 14),
    opacity: 0.35,
    className: context.selected ? 'selected' : undefined,
  }],
});

function polylineShapes(context: DrawingGeometryContext, closed: boolean, arrow: boolean): DrawingShape[] {
  const stroke = lineStroke(context);
  const points = context.points;
  if (closed && points.length >= 3) return [{ kind: 'polygon', points, ...stroke, ...areaFill(context.style.color) }];
  const shapes: DrawingShape[] = [{ kind: 'polyline', points, ...stroke }];
  if (arrow && points.length >= 2) shapes.push(arrowHead(points[points.length - 2], points[points.length - 1], context.style.lineWidth, { fill: context.style.color, className: stroke.className }));
  return shapes;
}

export const pathTool = defineDrawingTool({
  id: 'path',
  label: 'Path',
  displayName: 'Path',
  group: 'shapes',
  // One anchor per click; a double click finishes.
  creation: { gesture: 'click-click', minAnchors: 2 },
  defaultProperties: { arrowEnd: true },
  propertySchema: [{ key: 'arrowEnd', label: 'Arrow at the end', type: 'boolean' }],
  geometry: (context) => polylineShapes(context, false, booleanProperty(context.properties, 'arrowEnd', true)),
});

export const polylineTool = defineDrawingTool({
  id: 'polyline',
  label: 'Polyline',
  displayName: 'Polyline',
  group: 'shapes',
  creation: { gesture: 'click-click', minAnchors: 2 },
  defaultProperties: { closed: true },
  propertySchema: [{ key: 'closed', label: 'Closed (filled)', type: 'boolean' }],
  geometry: (context) => polylineShapes(context, booleanProperty(context.properties, 'closed', true), false),
});

export const curveTool = defineDrawingTool({
  id: 'curve',
  label: 'Curve',
  displayName: 'Curve',
  group: 'shapes',
  // Start, end, then the point the curve bends towards.
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [start, end, bend] = context.points;
    const stroke = lineStroke(context);
    if (!bend) return [{ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...stroke }];
    // The quadratic whose middle passes through the bend point: control = 2 bend - (start + end) / 2.
    const control = { x: 2 * bend.x - (start.x + end.x) / 2, y: 2 * bend.y - (start.y + end.y) / 2 };
    return [{ kind: 'path', commands: [{ op: 'M', x: start.x, y: start.y }, { op: 'Q', cx: control.x, cy: control.y, x: end.x, y: end.y }], ...stroke }];
  },
});

export const doubleCurveTool = defineDrawingTool({
  id: 'double-curve',
  label: 'Double curve',
  displayName: 'Double Curve',
  group: 'shapes',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    // An S curve: it bends towards the third point on the first half and away from it on the second.
    const [start, end, bend] = context.points;
    const stroke = lineStroke(context);
    if (!bend) return [{ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...stroke }];
    const middle = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
    const offset = { x: bend.x - (start.x + middle.x) / 2, y: bend.y - (start.y + middle.y) / 2 };
    const first = { x: (start.x + middle.x) / 2 + offset.x * 2, y: (start.y + middle.y) / 2 + offset.y * 2 };
    const second = { x: (middle.x + end.x) / 2 - offset.x * 2, y: (middle.y + end.y) / 2 - offset.y * 2 };
    return [{
      kind: 'path',
      commands: [{ op: 'M', x: start.x, y: start.y }, { op: 'Q', cx: first.x, cy: first.y, x: middle.x, y: middle.y }, { op: 'Q', cx: second.x, cy: second.y, x: end.x, y: end.y }],
      ...stroke,
    }];
  },
});

export const triangleTool = defineDrawingTool({
  id: 'triangle',
  label: 'Triangle',
  displayName: 'Triangle',
  group: 'shapes',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { fill: true },
  propertySchema: [{ key: 'fill', label: 'Background', type: 'boolean' }],
  geometry: (context) => {
    const stroke = lineStroke(context);
    if (context.points.length < 3) return [{ kind: 'polyline', points: context.points, ...stroke }];
    const fill = booleanProperty(context.properties, 'fill', true) ? areaFill(context.style.color) : {};
    return [{ kind: 'polygon', points: context.points, ...stroke, ...fill }];
  },
});

/** A rectangle with side A-B and the width C sets, perpendicular to A-B on the screen. */
export function rotatedRectangleCorners([first, second, third]: readonly ScreenPoint[]): ScreenPoint[] {
  const dx = second.x - first.x;
  const dy = second.y - first.y;
  const length = Math.hypot(dx, dy);
  if (length < 1e-9) return [first, second, second, first];
  const normal = { x: -dy / length, y: dx / length };
  const width = (third.x - first.x) * normal.x + (third.y - first.y) * normal.y;
  return [first, second, { x: second.x + normal.x * width, y: second.y + normal.y * width }, { x: first.x + normal.x * width, y: first.y + normal.y * width }];
}

export const rotatedRectangleTool = defineDrawingTool({
  id: 'rotated-rectangle',
  label: 'Rotated rectangle',
  displayName: 'Rotated Rectangle',
  group: 'shapes',
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { fill: true },
  propertySchema: [{ key: 'fill', label: 'Background', type: 'boolean' }],
  geometry: (context) => {
    const stroke = lineStroke(context);
    const [first, second] = context.points;
    if (context.points.length < 3) return [{ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke }];
    const fill = booleanProperty(context.properties, 'fill', true) ? areaFill(context.style.color) : {};
    return [{ kind: 'polygon', points: rotatedRectangleCorners(context.points), ...stroke, ...fill }];
  },
});

/** The circle through three points, or null when they are on one line. */
export function circleThrough(a: ScreenPoint, b: ScreenPoint, c: ScreenPoint): { x: number; y: number; r: number } | null {
  const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
  if (Math.abs(d) < 1e-9) return null;
  const a2 = a.x * a.x + a.y * a.y;
  const b2 = b.x * b.x + b.y * b.y;
  const c2 = c.x * c.x + c.y * c.y;
  const x = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d;
  const y = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d;
  return { x, y, r: Math.hypot(a.x - x, a.y - y) };
}

/** The points of the arc from `start` to `end` that passes through `through`. */
export function arcThrough(start: ScreenPoint, end: ScreenPoint, through: ScreenPoint, steps = 48): ScreenPoint[] {
  const circle = circleThrough(start, through, end);
  if (!circle) return [start, end];
  const angle = (point: ScreenPoint) => Math.atan2(point.y - circle.y, point.x - circle.x);
  const from = angle(start);
  let to = angle(end);
  const via = angle(through);
  const normalize = (value: number) => ((value - from) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
  // Go the way round that passes the third point.
  if (normalize(via) > normalize(to)) to = from - (2 * Math.PI - normalize(to));
  else to = from + normalize(to);
  return Array.from({ length: steps + 1 }, (_, index) => {
    const theta = from + (to - from) * index / steps;
    return { x: circle.x + Math.cos(theta) * circle.r, y: circle.y + Math.sin(theta) * circle.r };
  });
}

export const arcTool = defineDrawingTool({
  id: 'arc',
  label: 'Arc',
  displayName: 'Arc',
  group: 'shapes',
  // Start, end, then a point the arc passes through.
  creation: { gesture: 'click-click', anchors: 3 },
  draftPreview: 'shapes',
  previewAnchors: 2,
  defaultProperties: { fill: true },
  propertySchema: [{ key: 'fill', label: 'Background', type: 'boolean' }],
  geometry: (context) => {
    const [start, end, through] = context.points;
    const stroke = lineStroke(context);
    if (!through) return [{ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...stroke }];
    const points = arcThrough(start, end, through);
    if (booleanProperty(context.properties, 'fill', true)) return [{ kind: 'polygon', points, ...stroke, ...areaFill(context.style.color) }];
    return [{ kind: 'polyline', points, ...stroke }];
  },
});
