// Trend line variants (TVP-3.1): info line, extended line and trend angle.
import { lineAlertLevel } from '../alertLevels';
import { booleanProperty } from '../properties';
import { constrainTo45Degrees, extendedSegment, lineStroke } from '../shapes';
import { defineDrawingTool, type DrawingPoint, type DrawingShape, type DrawingToolServices, type ScreenPoint } from '../types';

/** The screen angle of `first` -> `second` in degrees, counter-clockwise from the horizontal, -180 to 180. */
export function screenAngle(first: ScreenPoint, second: ScreenPoint): number {
  return Math.atan2(first.y - second.y, second.x - first.x) * 180 / Math.PI;
}

function signed(value: number, digits: number): string {
  const text = Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return `${value < 0 ? '-' : ''}${text}`;
}

/** The info line's label: price change (percent), bars between the anchors (gaps don't count) and angle. */
export function infoLineLabel(
  first: DrawingPoint,
  second: DrawingPoint,
  angle: number,
  services: Pick<DrawingToolServices, 'barIndexForTime' | 'formatPrice'>,
): string {
  const delta = second.price - first.price;
  const percent = first.price === 0 ? 0 : delta / first.price * 100;
  const firstIndex = services.barIndexForTime(first.time);
  const secondIndex = services.barIndexForTime(second.time);
  const bars = firstIndex === null || secondIndex === null ? null : Math.round(secondIndex - firstIndex);
  const price = `${delta < 0 ? '-' : ''}${services.formatPrice(Math.abs(delta))} (${signed(percent, 2)}%)`;
  return [price, bars === null ? null : `${bars} bars`, `${signed(angle, 1)}°`].filter(Boolean).join(' · ');
}

function segmentAlert(key: string, label: string, first: DrawingPoint | undefined, second: DrawingPoint | undefined, both = false) {
  if (!first || !second) return [];
  const level = lineAlertLevel(key, label, first, second, both ? 'both' : 'none');
  return level ? [level] : [];
}

export const infoLineTool = defineDrawingTool({
  id: 'info-line',
  label: 'Info line',
  group: 'lines',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: { showLabel: true },
  propertySchema: [{ key: 'showLabel', label: 'Show info', type: 'boolean' }],
  draftPreview: 'shapes',
  geometry: (context) => {
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    const shapes: DrawingShape[] = [{ kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke }];
    if (booleanProperty(context.properties, 'showLabel', true)) {
      const [rawFirst, rawSecond] = context.rawPoints;
      const text = infoLineLabel(rawFirst, rawSecond, screenAngle(first, second), context);
      const below = second.y >= first.y;
      shapes.push({ kind: 'text', x: second.x + 6, y: second.y + (below ? 14 : -6), text, fontSize: 11, hit: 'none' });
    }
    return shapes;
  },
  alertLevels: ([first, second]) => segmentAlert('line', 'Info line', first, second),
});

export const extendedLineTool = defineDrawingTool({
  id: 'extended-line',
  label: 'Extended line',
  group: 'lines',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: {},
  propertySchema: [],
  geometry: (context) => {
    const [first, second] = context.points;
    const [start, end] = extendedSegment(first, second, context.viewport, true, true);
    return [{ kind: 'segment', x1: start.x, y1: start.y, x2: end.x, y2: end.y, ...lineStroke(context) }];
  },
  alertLevels: ([first, second]) => segmentAlert('line', 'Extended line', first, second, true),
});

/** Points of an arc of `radius` around `center` from 0 to `degrees` (counter-clockwise on screen). */
function arcPoints(center: ScreenPoint, radius: number, degrees: number): ScreenPoint[] {
  const steps = Math.max(2, Math.ceil(Math.abs(degrees) / 10));
  return Array.from({ length: steps + 1 }, (_, step) => {
    const angle = degrees * step / steps * Math.PI / 180;
    return { x: center.x + radius * Math.cos(angle), y: center.y - radius * Math.sin(angle) };
  });
}

export const trendAngleTool = defineDrawingTool({
  id: 'trend-angle',
  label: 'Trend angle',
  group: 'lines',
  creation: { gesture: 'drag' },
  constrain: constrainTo45Degrees,
  defaultProperties: {},
  propertySchema: [],
  draftPreview: 'shapes',
  geometry: (context) => {
    const [first, second] = context.points;
    const stroke = lineStroke(context);
    const angle = screenAngle(first, second);
    const length = Math.hypot(second.x - first.x, second.y - first.y);
    const radius = Math.min(40, Math.max(12, length / 3));
    // One convention for the arc and the label: the angle from the rightward horizontal, -180 to 180.
    const baseEnd = { x: first.x + radius * 1.5, y: first.y };
    const arc = arcPoints(first, radius, angle);
    const middle = angle / 2 * Math.PI / 180;
    const labelPoint = { x: first.x + (radius + 14) * Math.cos(middle), y: first.y - (radius + 14) * Math.sin(middle) };
    return [
      { kind: 'segment', x1: first.x, y1: first.y, x2: second.x, y2: second.y, ...stroke },
      { kind: 'segment', x1: first.x, y1: first.y, x2: baseEnd.x, y2: baseEnd.y, ...stroke, strokeWidth: 1, dash: [4, 3], hit: 'none' },
      { kind: 'polyline', points: arc, ...stroke, strokeWidth: 1, hit: 'none' },
      { kind: 'text', x: labelPoint.x, y: labelPoint.y + 4, text: `${signed(angle, 1)}°`, fontSize: 11, align: Math.abs(angle) > 90 ? 'end' : 'start', hit: 'none' },
    ];
  },
  alertLevels: ([first, second]) => segmentAlert('line', 'Trend angle', first, second),
});
