import { distanceToSegment } from './shapes';
import type { DrawingHit, DrawingShape, PathCommand, ScreenPoint } from './types';

/** Default hit tolerance around strokes, in CSS pixels. */
export const DRAWING_HIT_TOLERANCE = 5;

function filled(shape: DrawingShape): boolean {
  return shape.fill !== undefined && shape.fill !== 'none';
}

function stroked(shape: DrawingShape): boolean {
  return shape.stroke !== undefined && shape.stroke !== 'none';
}

function polylineDistance(point: ScreenPoint, points: readonly ScreenPoint[], closed: boolean): number {
  if (points.length === 0) return Number.POSITIVE_INFINITY;
  if (points.length === 1) return Math.hypot(point.x - points[0].x, point.y - points[0].y);
  let best = Number.POSITIVE_INFINITY;
  for (let index = 1; index < points.length; index += 1) {
    best = Math.min(best, distanceToSegment(point, points[index - 1], points[index]));
  }
  if (closed) best = Math.min(best, distanceToSegment(point, points[points.length - 1], points[0]));
  return best;
}

export function pointInPolygon(point: ScreenPoint, points: readonly ScreenPoint[]): boolean {
  let inside = false;
  for (let index = 0, previous = points.length - 1; index < points.length; previous = index, index += 1) {
    const a = points[index];
    const b = points[previous];
    if ((a.y > point.y) !== (b.y > point.y) && point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

/** Flattens path commands into polylines (curves sampled), one per subpath. */
export function flattenPath(commands: readonly PathCommand[], steps = 16): { points: ScreenPoint[]; closed: boolean }[] {
  const paths: { points: ScreenPoint[]; closed: boolean }[] = [];
  let current: { points: ScreenPoint[]; closed: boolean } | null = null;
  let cursor: ScreenPoint = { x: 0, y: 0 };
  for (const command of commands) {
    if (command.op === 'M' || !current) {
      current = { points: [], closed: false };
      paths.push(current);
      if (command.op !== 'M') current.points.push(cursor);
    }
    if (command.op === 'M' || command.op === 'L') {
      cursor = { x: command.x, y: command.y };
      current.points.push(cursor);
    } else if (command.op === 'Q' || command.op === 'C') {
      const start = cursor;
      for (let step = 1; step <= steps; step += 1) {
        const t = step / steps;
        const u = 1 - t;
        current.points.push(command.op === 'Q'
          ? { x: u * u * start.x + 2 * u * t * command.cx + t * t * command.x, y: u * u * start.y + 2 * u * t * command.cy + t * t * command.y }
          : {
            x: u * u * u * start.x + 3 * u * u * t * command.c1x + 3 * u * t * t * command.c2x + t * t * t * command.x,
            y: u * u * u * start.y + 3 * u * u * t * command.c1y + 3 * u * t * t * command.c2y + t * t * t * command.y,
          });
      }
      cursor = { x: command.x, y: command.y };
    } else {
      current.closed = true;
      cursor = current.points[0] ?? cursor;
      current = null;
    }
  }
  return paths;
}

function textBounds(shape: Extract<DrawingShape, { kind: 'text' }>): { x: number; y: number; width: number; height: number } {
  const fontSize = shape.fontSize ?? 11;
  const width = shape.text.length * fontSize * 0.6;
  const left = shape.align === 'middle' ? shape.x - width / 2 : shape.align === 'end' ? shape.x - width : shape.x;
  return { x: left, y: shape.y - fontSize, width, height: fontSize * 1.25 };
}

function rectDistance(point: ScreenPoint, rect: { x: number; y: number; width: number; height: number }): number {
  const dx = Math.max(rect.x - point.x, 0, point.x - (rect.x + rect.width));
  const dy = Math.max(rect.y - point.y, 0, point.y - (rect.y + rect.height));
  return Math.hypot(dx, dy);
}

/** Distance from `point` to a shape's painted area, 0 inside a filled shape. */
export function shapeDistance(shape: DrawingShape, point: ScreenPoint): number {
  const halfStroke = stroked(shape) ? (shape.strokeWidth ?? 1) / 2 : 0;
  switch (shape.kind) {
    case 'segment':
      return Math.max(0, distanceToSegment(point, { x: shape.x1, y: shape.y1 }, { x: shape.x2, y: shape.y2 }) - halfStroke);
    case 'polyline':
      return Math.max(0, polylineDistance(point, shape.points, false) - halfStroke);
    case 'polygon':
      if (filled(shape) && pointInPolygon(point, shape.points)) return 0;
      return Math.max(0, polylineDistance(point, shape.points, true) - halfStroke);
    case 'rect': {
      const corners = [
        { x: shape.x, y: shape.y },
        { x: shape.x + shape.width, y: shape.y },
        { x: shape.x + shape.width, y: shape.y + shape.height },
        { x: shape.x, y: shape.y + shape.height },
      ];
      if (filled(shape)) return Math.max(0, rectDistance(point, shape) - halfStroke);
      return Math.max(0, polylineDistance(point, corners, true) - halfStroke);
    }
    case 'ellipse': {
      const rx = Math.max(shape.rx, 1e-9);
      const ry = Math.max(shape.ry, 1e-9);
      const normalized = Math.hypot((point.x - shape.cx) / rx, (point.y - shape.cy) / ry);
      if (filled(shape) && normalized <= 1) return 0;
      return Math.max(0, Math.abs(normalized - 1) * Math.min(rx, ry) - halfStroke);
    }
    case 'path': {
      let best = Number.POSITIVE_INFINITY;
      for (const subpath of flattenPath(shape.commands)) {
        if (filled(shape) && pointInPolygon(point, subpath.points)) return 0;
        best = Math.min(best, polylineDistance(point, subpath.points, subpath.closed));
      }
      return Math.max(0, best - halfStroke);
    }
    case 'marker':
      return Math.max(0, Math.hypot(point.x - shape.x, point.y - shape.y) - shape.radius - halfStroke);
    case 'text':
      return rectDistance(point, textBounds(shape));
  }
}

/** The default hit test: the nearest interactive shape within `tolerance`. */
export function hitTestShapes(shapes: readonly DrawingShape[], point: ScreenPoint, tolerance = DRAWING_HIT_TOLERANCE): DrawingHit | null {
  let best: number | null = null;
  for (const shape of shapes) {
    if (shape.hit === 'none') continue;
    const distance = shapeDistance(shape, point);
    if (distance <= tolerance && (best === null || distance < best)) best = distance;
  }
  return best === null ? null : { distance: best };
}
