import type { DrawingGeometryContext, DrawingModifiers, DrawingShape, ScreenPoint, ShapePaint } from './types';

/** The drawing's line paint from its style, with the selection class the overlay CSS highlights. */
export function lineStroke(context: Pick<DrawingGeometryContext, 'style' | 'selected'>): ShapePaint {
  return {
    stroke: context.style.color,
    strokeWidth: context.style.lineWidth,
    dash: context.style.lineStyle === 'dashed' ? [6, 4] : undefined,
    className: context.selected ? 'selected' : undefined,
  };
}

/** Translucent area fill used by the closed shapes (rectangle, circle, ellipse). */
export function areaFill(color: string): Pick<ShapePaint, 'fill' | 'fillOpacity'> {
  return { fill: color, fillOpacity: 0x20 / 0xff };
}

/**
 * Where a ray from `from` through `through` leaves the viewport: the left or
 * right edge, or the top/bottom edge when the ray is vertical.
 */
export function rayEnd(from: ScreenPoint, through: ScreenPoint, viewport: { width: number; height: number }): ScreenPoint {
  const dx = through.x - from.x;
  if (Math.abs(dx) < 0.0001) return { x: from.x, y: through.y >= from.y ? viewport.height : 0 };
  const targetX = dx >= 0 ? viewport.width : 0;
  return { x: targetX, y: from.y + (through.y - from.y) / dx * (targetX - from.x) };
}

/** The segment `first`-`second`, optionally extended to the viewport edges. */
export function extendedSegment(
  first: ScreenPoint,
  second: ScreenPoint,
  viewport: { width: number; height: number },
  extendLeft: boolean,
  extendRight: boolean,
): [ScreenPoint, ScreenPoint] {
  const leftToRight = second.x >= first.x;
  const [left, right] = leftToRight ? [first, second] : [second, first];
  const start = extendLeft ? rayEnd(right, left, viewport) : left;
  const end = extendRight ? rayEnd(left, right, viewport) : right;
  return leftToRight ? [start, end] : [end, start];
}

/**
 * A filled arrow head at `tip`, pointing along `from` -> `tip`. Matches the SVG
 * marker the arrow tool used before: 6 x 6 stroke widths, its tip one stroke
 * width beyond the line end.
 */
export function arrowHead(from: ScreenPoint, tip: ScreenPoint, lineWidth: number, paint: ShapePaint): DrawingShape {
  const dx = tip.x - from.x;
  const dy = tip.y - from.y;
  const length = Math.hypot(dx, dy);
  const ux = length < 1e-9 ? 1 : dx / length;
  const uy = length < 1e-9 ? 0 : dy / length;
  const at = (along: number, across: number): ScreenPoint => ({
    x: tip.x + ux * along * lineWidth - uy * across * lineWidth,
    y: tip.y + uy * along * lineWidth + ux * across * lineWidth,
  });
  return { kind: 'polygon', points: [at(-5, -3), at(1, 0), at(-5, 3)], ...paint };
}

/**
 * Everything about a shape list except coordinates and text: kinds, paint,
 * classes, hit roles and text layout. The host patches coordinates in place
 * and re-renders through React whenever this changes.
 */
export function shapeSignature(shapes: readonly DrawingShape[]): string {
  return shapes.map((shape) => [
    shape.kind,
    shape.className ?? '',
    shape.stroke ?? '',
    shape.strokeWidth ?? '',
    shape.dash?.join(' ') ?? '',
    shape.fill ?? '',
    shape.fillOpacity ?? '',
    shape.opacity ?? '',
    shape.hit ?? '',
    shape.kind === 'text' ? `${shape.align ?? ''}/${shape.fontSize ?? ''}/${shape.fontWeight ?? ''}` : '',
  ].join('|')).join(';');
}

/**
 * Shift-constrain for two-anchor tools: with Shift held, snaps the candidate
 * to the nearest 45-degree direction from the previous anchor.
 */
export function constrainTo45Degrees(candidate: ScreenPoint, others: readonly ScreenPoint[], modifiers: DrawingModifiers): ScreenPoint {
  const origin = others[others.length - 1];
  if (!modifiers.shift || !origin) return candidate;
  const dx = candidate.x - origin.x;
  const dy = candidate.y - origin.y;
  const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
  const length = Math.hypot(dx, dy) * Math.abs(Math.cos(Math.atan2(dy, dx) - angle));
  return { x: origin.x + Math.cos(angle) * length, y: origin.y + Math.sin(angle) * length };
}

/** With Shift, makes the box from the previous anchor to `candidate` a square (rectangle), or a circle (ellipse). */
export function constrainToSquare(candidate: ScreenPoint, others: readonly ScreenPoint[], modifiers: DrawingModifiers): ScreenPoint {
  const origin = others[others.length - 1];
  if (!modifiers.shift || !origin) return candidate;
  const dx = candidate.x - origin.x;
  const dy = candidate.y - origin.y;
  const side = Math.max(Math.abs(dx), Math.abs(dy));
  return { x: origin.x + (dx < 0 ? -side : side), y: origin.y + (dy < 0 ? -side : side) };
}

/**
 * Ramer-Douglas-Peucker simplification, for freehand strokes. Keeps the first
 * and last point and every point further than `tolerance` from the chord.
 */
export function simplifyPolyline<T extends ScreenPoint>(points: readonly T[], tolerance: number): T[] {
  if (points.length <= 2 || tolerance <= 0) return [...points];
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack: [number, number][] = [[0, points.length - 1]];
  while (stack.length > 0) {
    const [start, end] = stack.pop()!;
    let farthest = -1;
    let farthestDistance = tolerance;
    for (let index = start + 1; index < end; index += 1) {
      const distance = distanceToSegment(points[index], points[start], points[end]);
      if (distance > farthestDistance) {
        farthest = index;
        farthestDistance = distance;
      }
    }
    if (farthest >= 0) {
      keep[farthest] = 1;
      stack.push([start, farthest], [farthest, end]);
    }
  }
  return points.filter((_, index) => keep[index] === 1);
}

export function distanceToSegment(point: ScreenPoint, start: ScreenPoint, end: ScreenPoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared));
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy));
}
