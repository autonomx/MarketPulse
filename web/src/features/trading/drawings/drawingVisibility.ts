// Per-interval visibility of drawings (TVP-3.8), as TradingView's Visibility
// tab: each interval unit is on or off, and the units with counts keep a
// range (show on 1-15 minutes only, say). A drawing without settings shows
// on every interval.
import { parseTradingInterval, type TradingIntervalUnit } from '../tradingIntervals';

export type VisibilityUnit = 'ticks' | 'seconds' | 'minutes' | 'hours' | 'days' | 'weeks' | 'months' | 'ranges';
export type UnitVisibility = { visible: boolean; from?: number; to?: number };
export type DrawingVisibility = Partial<Record<VisibilityUnit, UnitVisibility>>;

/** The units in TradingView's order, with the count range each allows (none for ticks and ranges). */
export const VISIBILITY_UNITS: readonly { unit: VisibilityUnit; label: string; min?: number; max?: number }[] = [
  { unit: 'ticks', label: 'Ticks' },
  { unit: 'seconds', label: 'Seconds', min: 1, max: 59 },
  { unit: 'minutes', label: 'Minutes', min: 1, max: 59 },
  { unit: 'hours', label: 'Hours', min: 1, max: 24 },
  { unit: 'days', label: 'Days', min: 1, max: 366 },
  { unit: 'weeks', label: 'Weeks', min: 1, max: 52 },
  { unit: 'months', label: 'Months', min: 1, max: 12 },
  { unit: 'ranges', label: 'Ranges' },
];

const UNIT_OF: Record<TradingIntervalUnit, VisibilityUnit> = {
  t: 'ticks', s: 'seconds', m: 'minutes', h: 'hours', d: 'days', w: 'weeks', mo: 'months', r: 'ranges',
};

/** The visibility unit and count of an interval (`15m` -> minutes 15); null for an interval that doesn't parse. */
export function visibilityUnitOf(interval: string): { unit: VisibilityUnit; count: number } | null {
  const parsed = parseTradingInterval(interval);
  if (!parsed.ok) return null;
  // 60 minutes and more are hours on the chart, as TradingView counts them.
  if (parsed.unit === 'm' && parsed.count >= 60 && parsed.count % 60 === 0) return { unit: 'hours', count: parsed.count / 60 };
  return { unit: UNIT_OF[parsed.unit], count: parsed.count };
}

/** Whether a drawing shows on `interval`. Anything unreadable shows, so a drawing never vanishes by mistake. */
export function drawingVisibleOnInterval(visibility: DrawingVisibility | undefined, interval: string): boolean {
  if (!visibility || typeof visibility !== 'object') return true;
  const at = visibilityUnitOf(interval);
  if (!at) return true;
  const setting = visibility[at.unit];
  if (!setting || typeof setting !== 'object') return true;
  if (setting.visible === false) return false;
  const from = Number.isFinite(setting.from) ? (setting.from as number) : -Infinity;
  const to = Number.isFinite(setting.to) ? (setting.to as number) : Infinity;
  return at.count >= from && at.count <= to;
}

/** "Show only on this interval": every unit off except the current interval's, limited to its count. */
export function onlyOnInterval(interval: string): DrawingVisibility {
  const at = visibilityUnitOf(interval);
  if (!at) return {};
  const visibility: DrawingVisibility = {};
  for (const { unit, min } of VISIBILITY_UNITS) {
    visibility[unit] = unit === at.unit ? (min === undefined ? { visible: true } : { visible: true, from: at.count, to: at.count }) : { visible: false };
  }
  return visibility;
}
