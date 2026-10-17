// Alert lines of drawings (TVP-1.4 contract): two anchors, straight in
// bar-index space, plus `extend`. See DrawingAlertLevel and barTimeline.ts.
import type { DrawingAlertExtend, DrawingAlertLevel, DrawingPoint, DrawingToolServices } from './types';

function anchor(point: DrawingPoint): DrawingPoint {
  return { time: point.time, price: point.price };
}

/** The line through two anchors, ordered by time. Null when both share a time (a vertical line). */
export function lineAlertLevel(key: string, label: string, first: DrawingPoint, second: DrawingPoint, extend: DrawingAlertExtend): DrawingAlertLevel | null {
  const firstTime = Date.parse(first.time);
  const secondTime = Date.parse(second.time);
  if (!Number.isFinite(firstTime) || !Number.isFinite(secondTime) || firstTime === secondTime) return null;
  const anchors: [DrawingPoint, DrawingPoint] = firstTime < secondTime ? [anchor(first), anchor(second)] : [anchor(second), anchor(first)];
  return { key, label, anchors, extend, interpolation: 'bars' };
}

/**
 * A horizontal level at `point.price`: anchors at the point and one bar later.
 * `extend: 'right'` makes a ray from the point's time; `both` a full line.
 */
export function horizontalAlertLevel(
  key: string,
  label: string,
  point: DrawingPoint,
  extend: DrawingAlertExtend,
  services: Pick<DrawingToolServices, 'timeAfterBars'>,
): DrawingAlertLevel | null {
  const later = services.timeAfterBars(point.time, 1);
  return later ? lineAlertLevel(key, label, point, { time: later, price: point.price }, extend) : null;
}

/**
 * The parallel of `level` through `point` ("through-point" form): the same
 * anchor times, shifted by the price difference at `point`'s bar index. Use it
 * for derived lines (a channel's second line, a pitchfork's median).
 */
export function lineThroughPoint(
  key: string,
  label: string,
  level: DrawingAlertLevel,
  point: DrawingPoint,
  services: Pick<DrawingToolServices, 'barIndexForTime'>,
): DrawingAlertLevel | null {
  const index = services.barIndexForTime(point.time);
  const price = index === null ? null : alertLevelPriceAt(level, index, services.barIndexForTime, true);
  if (price === null) return null;
  const shift = point.price - price;
  const [first, second] = level.anchors;
  return lineAlertLevel(key, label, { ...first, price: first.price + shift }, { ...second, price: second.price + shift }, level.extend);
}

/**
 * The level's price at bar index `index`, or null outside its extent.
 * `barIndexForTime` maps a time to its bar index on the alert's interval (the
 * `barTimeline.ts` spec); `ignoreExtent` evaluates the infinite line.
 */
export function alertLevelPriceAt(
  level: DrawingAlertLevel,
  index: number,
  barIndexForTime: (time: string) => number | null,
  ignoreExtent = false,
): number | null {
  const [first, second] = level.anchors;
  const firstIndex = barIndexForTime(first.time);
  const secondIndex = barIndexForTime(second.time);
  if (firstIndex === null || secondIndex === null || secondIndex === firstIndex) return null;
  if (!ignoreExtent) {
    if (index < firstIndex && level.extend !== 'left' && level.extend !== 'both') return null;
    if (index > secondIndex && level.extend !== 'right' && level.extend !== 'both') return null;
  }
  return first.price + (second.price - first.price) * (index - firstIndex) / (secondIndex - firstIndex);
}
