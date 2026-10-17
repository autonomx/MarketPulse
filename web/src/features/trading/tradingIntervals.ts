export type TradingIntervalOption = {
  value: string;
  label: string;
  compactLabel: string;
};

export type TradingIntervalGroup = {
  label: string;
  options: readonly TradingIntervalOption[];
};

const options = (items: readonly [string, string, string][]): readonly TradingIntervalOption[] => (
  items.map(([value, label, compactLabel]) => ({ value, label, compactLabel }))
);

export const TRADING_VIEW_INTERVAL_GROUPS: readonly TradingIntervalGroup[] = [
  {
    label: 'Ticks',
    options: options([
      ['1t', '1 tick', '1T'],
      ['10t', '10 ticks', '10T'],
      ['100t', '100 ticks', '100T'],
      ['1000t', '1000 ticks', '1000T'],
    ]),
  },
  {
    label: 'Seconds',
    options: options([
      ['1s', '1 second', '1s'],
      ['5s', '5 seconds', '5s'],
      ['10s', '10 seconds', '10s'],
      ['15s', '15 seconds', '15s'],
      ['30s', '30 seconds', '30s'],
      ['45s', '45 seconds', '45s'],
    ]),
  },
  {
    label: 'Minutes',
    options: options([
      ['1m', '1 minute', '1m'],
      ['2m', '2 minutes', '2m'],
      ['3m', '3 minutes', '3m'],
      ['5m', '5 minutes', '5m'],
      ['10m', '10 minutes', '10m'],
      ['15m', '15 minutes', '15m'],
      ['30m', '30 minutes', '30m'],
      ['45m', '45 minutes', '45m'],
    ]),
  },
  {
    label: 'Hours',
    options: options([
      ['1h', '1 hour', '1H'],
      ['2h', '2 hours', '2H'],
      ['3h', '3 hours', '3H'],
      ['4h', '4 hours', '4H'],
      ['6h', '6 hours', '6H'],
      ['7h', '7 hours', '7H'],
      ['8h', '8 hours', '8H'],
      ['12h', '12 hours', '12H'],
      ['20h', '20 hours', '20H'],
      ['24h', '24 hours', '24H'],
    ]),
  },
  {
    label: 'Days',
    options: options([
      ['1d', '1 day', '1D'],
      ['2d', '2 days', '2D'],
      ['3d', '3 days', '3D'],
      ['4d', '4 days', '4D'],
      ['5d', '5 days', '5D'],
      ['6d', '6 days', '6D'],
    ]),
  },
  {
    label: 'Weeks',
    options: options([
      ['1w', '1 week', '1W'],
      ['2w', '2 weeks', '2W'],
      ['3w', '3 weeks', '3W'],
      ['6w', '6 weeks', '6W'],
    ]),
  },
  {
    label: 'Months',
    options: options([
      ['1mo', '1 month', '1M'],
      ['2mo', '2 months', '2M'],
      ['3mo', '3 months', '3M'],
      ['6mo', '6 months', '6M'],
      ['12mo', '12 months', '12M'],
    ]),
  },
  {
    label: 'Ranges',
    options: options([
      ['1r', '1 range', '1R'],
      ['10r', '10 ranges', '10R'],
      ['100r', '100 ranges', '100R'],
      ['1000r', '1000 ranges', '1000R'],
    ]),
  },
] as const;

const optionByValue = new Map(
  TRADING_VIEW_INTERVAL_GROUPS.flatMap((group) => group.options).map((option) => [option.value, option]),
);

/** Interval units: ticks, seconds, minutes, hours, days, weeks, months and ranges. */
export type TradingIntervalUnit = 't' | 's' | 'm' | 'h' | 'd' | 'w' | 'mo' | 'r';

type UnitSpec = { singular: string; plural: string; compact: string; max: number };

const UNIT_SPECS: Record<TradingIntervalUnit, UnitSpec> = {
  t: { singular: 'tick', plural: 'ticks', compact: 'T', max: 100_000 },
  s: { singular: 'second', plural: 'seconds', compact: 's', max: 3_600 },
  m: { singular: 'minute', plural: 'minutes', compact: 'm', max: 1_440 },
  h: { singular: 'hour', plural: 'hours', compact: 'H', max: 168 },
  d: { singular: 'day', plural: 'days', compact: 'D', max: 365 },
  w: { singular: 'week', plural: 'weeks', compact: 'W', max: 52 },
  mo: { singular: 'month', plural: 'months', compact: 'M', max: 24 },
  r: { singular: 'range', plural: 'ranges', compact: 'R', max: 100_000 },
};

const CANONICAL_INTERVAL = /^([1-9]\d*)(mo|t|s|m|h|d|w|r)$/;

export type ParsedTradingInterval = { value: string; count: number; unit: TradingIntervalUnit };
export type TradingIntervalParseResult =
  | ({ ok: true } & ParsedTradingInterval)
  | { ok: false; error: string };

function canonicalInterval(interval: string): ParsedTradingInterval | null {
  const match = CANONICAL_INTERVAL.exec(interval);
  if (!match) return null;
  return { value: interval, count: Number(match[1]), unit: match[2] as TradingIntervalUnit };
}

function unitFromInput(raw: string): TradingIntervalUnit | null {
  // TradingView convention: lower-case `m` is minutes, upper-case `M` is months.
  if (raw === 'M' || raw.toLowerCase() === 'mo') return 'mo';
  if (raw === 'm' || raw === '') return 'm';
  const lower = raw.toLowerCase();
  return lower === 't' || lower === 's' || lower === 'h' || lower === 'd' || lower === 'w' || lower === 'r'
    ? lower
    : null;
}

/**
 * Parses what a user types as an interval: a count and a unit (t/s/m/h/D/W/M/R), e.g. `7m`, `3h`,
 * `2D`, `500t`, `10R`. A bare number is minutes and a bare unit is one of it (`D` is one day).
 * Returns the canonical interval value (`7m`, `3h`, `2d`, `500t`, `10r`, `3mo`).
 */
export function parseTradingInterval(input: string): TradingIntervalParseResult {
  const text = input.replace(/\s+/g, '');
  if (!text) return { ok: false, error: 'Type a number and a unit, for example 7m, 3h or 2D.' };
  const match = /^(\d*)([a-zA-Z]{0,2})$/.exec(text);
  const unit = match ? unitFromInput(match[2]) : null;
  if (!match || !unit || (match[1] === '' && match[2] === '')) {
    return { ok: false, error: `"${input.trim()}" is not an interval. Use a number and t, s, m, h, D, W, M or R.` };
  }
  const count = match[1] === '' ? 1 : Number(match[1]);
  const spec = UNIT_SPECS[unit];
  if (!Number.isSafeInteger(count) || count < 1) return { ok: false, error: 'The interval count must be at least 1.' };
  if (count > spec.max) return { ok: false, error: `Use at most ${spec.max.toLocaleString('en-US')} ${spec.plural}.` };
  return { ok: true, value: `${count}${unit}`, count, unit };
}

export function intervalMenuLabel(interval: string): string {
  const known = optionByValue.get(interval)?.label;
  if (known) return known;
  const parsed = canonicalInterval(interval);
  if (!parsed) return interval.toUpperCase();
  const spec = UNIT_SPECS[parsed.unit];
  return `${parsed.count} ${parsed.count === 1 ? spec.singular : spec.plural}`;
}

export function intervalCompactLabel(interval: string): string {
  const known = optionByValue.get(interval)?.compactLabel;
  if (known) return known;
  const parsed = canonicalInterval(interval);
  return parsed ? `${parsed.count}${UNIT_SPECS[parsed.unit].compact}` : interval.toUpperCase();
}

/** The interval's length in milliseconds; null for tick and range intervals, which have none. */
export function tradingIntervalDurationMs(interval: string): number | null {
  const parsed = canonicalInterval(interval);
  if (!parsed) return null;
  if (parsed.unit === 's') return parsed.count * 1_000;
  const minutes = intervalMinutesValue(interval);
  return minutes === null ? null : minutes * 60_000;
}

/** Whether bars of this interval are shorter than a day. */
export function isIntradayInterval(interval: string): boolean {
  const parsed = canonicalInterval(interval);
  if (!parsed) return false;
  if (parsed.unit === 't' || parsed.unit === 'r' || parsed.unit === 's') return true;
  const minutes = intervalMinutesValue(interval);
  return minutes !== null && minutes < 1_440;
}

const UNIT_ORDER: Record<TradingIntervalUnit, number> = { t: 0, s: 1, m: 1, h: 1, d: 1, w: 1, mo: 1, r: 2 };

/** Intervals in display order: ticks, then time intervals by length, then ranges. */
export function sortTradingIntervals(intervals: readonly string[]): string[] {
  const key = (interval: string): [number, number] => {
    const parsed = canonicalInterval(interval);
    if (!parsed) return [3, 0];
    return [UNIT_ORDER[parsed.unit], tradingIntervalDurationMs(interval) ?? parsed.count];
  };
  return [...new Set(intervals)].sort((left, right) => {
    const [leftGroup, leftSize] = key(left);
    const [rightGroup, rightSize] = key(right);
    return leftGroup - rightGroup || leftSize - rightSize;
  });
}

export const DEFAULT_FAVORITE_INTERVALS: readonly string[] = ['1h', '2h', '4h'];
export const MAX_FAVORITE_INTERVALS = 24;

/** Keeps the canonical, distinct intervals of a stored favourites list. */
export function normalizeFavoriteIntervals(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const intervals = value.filter((item): item is string => typeof item === 'string' && canonicalInterval(item) !== null);
  return sortTradingIntervals(intervals).slice(0, MAX_FAVORITE_INTERVALS);
}

function intervalMinutesValue(interval: string): number | null {
  const match = interval.match(/^(\d+)(mo|m|h|d|w)$/i);
  if (!match) return null;
  const amount = Number(match[1]);
  const unit = match[2].toLowerCase();
  if (unit === 'mo') return amount * 43_200;
  if (unit === 'm') return amount;
  if (unit === 'h') return amount * 60;
  if (unit === 'd') return amount * 1_440;
  return amount * 10_080;
}

export function tradingIntervalMinutes(interval: string): number | null {
  return intervalMinutesValue(interval);
}

export function aggregationBaseInterval(
  targetInterval: string,
  supportedIntervals: readonly string[],
): string | null {
  if (supportedIntervals.includes(targetInterval)) return targetInterval;
  const targetMinutes = intervalMinutesValue(targetInterval);
  if (targetMinutes == null) return null;
  const candidates = supportedIntervals
    .map((candidate) => ({ candidate, minutes: intervalMinutesValue(candidate) }))
    .filter((item): item is { candidate: string; minutes: number } => (
      item.minutes != null
      && item.minutes <= targetMinutes
      && Number.isInteger(targetMinutes / item.minutes)
    ))
    .sort((left, right) => right.minutes - left.minutes);
  return candidates[0]?.candidate ?? null;
}

export function isIntervalAvailable(interval: string, supportedIntervals: readonly string[]): boolean {
  return aggregationBaseInterval(interval, supportedIntervals) !== null;
}

export type TradingIntervalAvailability = {
  available: boolean;
  /** The feed interval the server fetches; differs from the interval when the server aggregates it. */
  baseInterval: string | null;
  reason: string | null;
};

/**
 * Whether the feed can serve an interval. The server serves a feed interval directly and
 * aggregates any whole multiple of one (`providers/aggregation.py`), so `7m` comes from `1m`
 * and `3h` from `1h`. Second, tick and range bars need trade-level data that no feed provides.
 */
export function intervalAvailability(
  interval: string,
  supportedIntervals: readonly string[],
  feedName = 'the selected feed',
): TradingIntervalAvailability {
  const baseInterval = aggregationBaseInterval(interval, supportedIntervals);
  if (baseInterval !== null) return { available: true, baseInterval, reason: null };
  const parsed = canonicalInterval(interval);
  if (parsed && (parsed.unit === 's' || parsed.unit === 't' || parsed.unit === 'r')) {
    const plural = UNIT_SPECS[parsed.unit].plural;
    return {
      available: false,
      baseInterval: null,
      reason: `${plural[0].toUpperCase()}${plural.slice(1)} need trade-level data, which ${feedName} does not provide.`,
    };
  }
  return {
    available: false,
    baseInterval: null,
    reason: `${intervalMenuLabel(interval)} is not a whole multiple of an interval ${feedName} serves.`,
  };
}
