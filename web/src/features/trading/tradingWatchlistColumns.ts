import { tradingViewBuiltInDefinition } from './indicators/tradingViewBuiltIns';
import { formatWatchlistPrice } from './tradingWatchlistPresentation';

/**
 * Watchlist columns (TVP-5.2). Every column reads one value from the symbol's
 * snapshot, so sorting works the same way for all of them.
 *
 * Indicator columns name a server registry indicator, its period and line
 * (`indicator:<id>|<period>|<output>`); their values come from the server
 * (`/api/trading/indicators/latest`) into the snapshot's `indicators`.
 */
export type WatchlistSnapshot = {
  price: string | null;
  changePercent: number | null;
  change?: number | null;
  volume?: number | null;
  relativeVolume?: number | null;
  high?: number | null;
  low?: number | null;
  extendedPrice?: string | null;
  /** Indicator column values by column id. */
  indicators?: Readonly<Record<string, number | null>>;
};

export type WatchlistIndicatorColumnId = `indicator:${string}`;

export type WatchlistColumnId =
  | 'last'
  | 'change'
  | 'changePercent'
  | 'volume'
  | 'relativeVolume'
  | 'extendedPrice'
  | 'high'
  | 'low'
  | WatchlistIndicatorColumnId;

export type WatchlistColumnTone = 'positive' | 'negative' | 'neutral' | null;

export type WatchlistColumnDefinition = {
  id: WatchlistColumnId;
  /** Header text. */
  label: string;
  /** Used in the column chooser and in sort button labels. */
  description: string;
  /** Header tooltip when the label needs explaining. */
  hint?: string;
  /** Wide columns hold prices and volumes; narrow ones hold changes and ratios. */
  width: 'wide' | 'narrow';
  value: (snapshot: WatchlistSnapshot | undefined) => number | null;
  format: (snapshot: WatchlistSnapshot | undefined) => string;
  /** Colours the cell by sign (change columns). */
  signed?: boolean;
};

const EMPTY = '—';

function numeric(value: string | number | null | undefined): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatSigned(value: number | null, suffix = '', digits = 2): string {
  if (value == null) return EMPTY;
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}${suffix}`;
}

const VOLUME_FORMATTER = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });

function formatPriceValue(value: number | null | undefined): string {
  return value == null ? EMPTY : formatWatchlistPrice(String(value));
}

export const WATCHLIST_COLUMNS: readonly WatchlistColumnDefinition[] = [
  {
    id: 'last',
    label: 'Last',
    description: 'last price',
    width: 'wide',
    value: (snapshot) => numeric(snapshot?.price),
    format: (snapshot) => formatWatchlistPrice(snapshot?.price),
  },
  {
    id: 'change',
    label: 'Chg',
    description: 'change',
    width: 'narrow',
    signed: true,
    value: (snapshot) => snapshot?.change ?? null,
    format: (snapshot) => formatSigned(snapshot?.change ?? null),
  },
  {
    id: 'changePercent',
    label: 'Chg%',
    description: 'change percentage',
    width: 'narrow',
    signed: true,
    value: (snapshot) => snapshot?.changePercent ?? null,
    format: (snapshot) => formatSigned(snapshot?.changePercent ?? null, '%'),
  },
  {
    id: 'volume',
    label: 'Vol',
    description: 'volume',
    width: 'wide',
    value: (snapshot) => snapshot?.volume ?? null,
    format: (snapshot) => snapshot?.volume == null ? EMPTY : VOLUME_FORMATTER.format(snapshot.volume),
  },
  {
    id: 'relativeVolume',
    label: 'Rel Vol',
    description: 'relative volume',
    // The latest bar is usually still forming, so early in a bar this reads low.
    hint: 'Volume of the latest (still forming) bar over the average of the 20 bars before it',
    width: 'narrow',
    value: (snapshot) => snapshot?.relativeVolume ?? null,
    format: (snapshot) => snapshot?.relativeVolume == null ? EMPTY : `${snapshot.relativeVolume.toFixed(2)}×`,
  },
  {
    id: 'extendedPrice',
    label: 'Ext',
    description: 'extended-hours price',
    width: 'wide',
    value: (snapshot) => numeric(snapshot?.extendedPrice),
    format: (snapshot) => snapshot?.extendedPrice ? formatWatchlistPrice(snapshot.extendedPrice) : EMPTY,
  },
  {
    id: 'high',
    label: 'High',
    description: 'high',
    width: 'wide',
    value: (snapshot) => snapshot?.high ?? null,
    format: (snapshot) => formatPriceValue(snapshot?.high),
  },
  {
    id: 'low',
    label: 'Low',
    description: 'low',
    width: 'wide',
    value: (snapshot) => snapshot?.low ?? null,
    format: (snapshot) => formatPriceValue(snapshot?.low),
  },
];

export const DEFAULT_WATCHLIST_COLUMNS: readonly WatchlistColumnId[] = ['last', 'changePercent'];

const COLUMN_BY_ID = new Map(WATCHLIST_COLUMNS.map((column) => [column.id, column]));

/** An indicator column's line: a server registry indicator, its period and the output key it draws. */
export type WatchlistIndicatorLine = { indicatorId: string; period: number; output: string };

export const MAX_INDICATOR_COLUMNS = 8;
const INDICATOR_PREFIX = 'indicator:';

export function indicatorColumnId(line: WatchlistIndicatorLine): WatchlistIndicatorColumnId {
  return `${INDICATOR_PREFIX}${line.indicatorId}|${line.period}|${line.output}`;
}

export function indicatorColumnLine(id: string): WatchlistIndicatorLine | null {
  if (!id.startsWith(INDICATOR_PREFIX)) return null;
  const [indicatorId, period, ...output] = id.slice(INDICATOR_PREFIX.length).split('|');
  const length = Number(period);
  const key = output.join('|');
  return indicatorId && Number.isFinite(length) && length > 0 && key.startsWith(`${indicatorId}:`) ? { indicatorId, period: length, output: key } : null;
}

/** "RSI 14", or "MACD 9 · signal" for a line other than the indicator's first. */
export function indicatorColumnLabel(line: WatchlistIndicatorLine): string {
  const full = tradingViewBuiltInDefinition(line.indicatorId)?.name ?? line.indicatorId.toUpperCase();
  // "Relative Strength Index (RSI)" is "RSI" in a narrow header.
  const name = /\(([^()]+)\)/.exec(full)?.[1] ?? full;
  const last = line.output.split(':').at(-1) ?? '';
  const main = last === String(line.period) || last === '' || line.output === `${line.indicatorId}:${line.period}`;
  return `${name} ${line.period}${main ? '' : ` · ${last}`}`;
}

function indicatorColumn(id: WatchlistIndicatorColumnId): WatchlistColumnDefinition | undefined {
  const line = indicatorColumnLine(id);
  if (!line) return undefined;
  const label = indicatorColumnLabel(line);
  const value = (snapshot: WatchlistSnapshot | undefined) => snapshot?.indicators?.[id] ?? null;
  return {
    id,
    label,
    description: `${label} (indicator)`,
    hint: `${label} on the latest bar of the watchlist's interval`,
    width: 'narrow',
    value,
    format: (snapshot) => {
      const number = value(snapshot);
      return number == null ? EMPTY : number.toLocaleString(undefined, { maximumFractionDigits: Math.abs(number) >= 100 ? 2 : 4 });
    },
  };
}

export function watchlistColumn(id: WatchlistColumnId): WatchlistColumnDefinition | undefined {
  return COLUMN_BY_ID.get(id) ?? (id.startsWith(INDICATOR_PREFIX) ? indicatorColumn(id as WatchlistIndicatorColumnId) : undefined);
}

export function isIndicatorColumnId(id: WatchlistColumnId): id is WatchlistIndicatorColumnId {
  return indicatorColumnLine(id) !== null;
}

export function columnTone(column: WatchlistColumnDefinition, snapshot: WatchlistSnapshot | undefined): WatchlistColumnTone {
  if (!column.signed) return null;
  const value = column.value(snapshot);
  if (value == null) return null;
  return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
}

/**
 * Chosen columns in the chooser's order, then the indicator columns in the order they were added (at most
 * MAX_INDICATOR_COLUMNS); the symbol column is always shown.
 */
export function toggleWatchlistColumn(columns: readonly WatchlistColumnId[], id: WatchlistColumnId): WatchlistColumnId[] {
  const chosen = new Set(columns);
  if (chosen.has(id)) chosen.delete(id);
  else chosen.add(id);
  const indicators = [...columns.filter(isIndicatorColumnId), ...(isIndicatorColumnId(id) && !columns.includes(id) ? [id] : [])]
    .filter((columnId) => chosen.has(columnId))
    .slice(0, MAX_INDICATOR_COLUMNS);
  return [...WATCHLIST_COLUMNS.map((column) => column.id).filter((columnId) => chosen.has(columnId)), ...indicators];
}

export type WatchlistSortKey = 'symbol' | WatchlistColumnId;
export type WatchlistSort = { key: WatchlistSortKey; direction: 'asc' | 'desc' } | null;

/** Values sort high to low first, symbols A to Z first; the third click restores the list order. */
export function nextWatchlistSort(current: WatchlistSort, key: WatchlistSortKey): WatchlistSort {
  const first = key === 'symbol' ? 'asc' : 'desc';
  if (current?.key !== key) return { key, direction: first };
  if (current.direction === first) return { key, direction: first === 'asc' ? 'desc' : 'asc' };
  return null;
}

/** Empty values always sort last; ties keep the list order (the caller's stable fallback). */
export function watchlistComparator(
  sort: WatchlistSort,
  snapshots: Readonly<Record<string, WatchlistSnapshot | undefined>>,
  symbolFor: (instrumentId: string) => string,
): ((left: string, right: string) => number) | undefined {
  if (!sort) return undefined;
  const sign = sort.direction === 'asc' ? 1 : -1;
  if (sort.key === 'symbol') {
    return (left, right) => sign * symbolFor(left).localeCompare(symbolFor(right));
  }
  const column = watchlistColumn(sort.key);
  if (!column) return undefined;
  return (left, right) => {
    const leftValue = column.value(snapshots[left]);
    const rightValue = column.value(snapshots[right]);
    if (leftValue == null && rightValue == null) return 0;
    if (leftValue == null) return 1;
    if (rightValue == null) return -1;
    return sign * (leftValue - rightValue);
  };
}

/* The chosen columns and sort are a per-browser view preference. */

const VIEW_STORAGE_KEY = 'omnix.trading.watchlist-view';

export type WatchlistView = { columns: WatchlistColumnId[]; sort: WatchlistSort };

function isColumnId(value: unknown): value is WatchlistColumnId {
  return typeof value === 'string' && (COLUMN_BY_ID.has(value as WatchlistColumnId) || indicatorColumnLine(value) !== null);
}

export function readWatchlistView(): WatchlistView {
  const fallback: WatchlistView = { columns: [...DEFAULT_WATCHLIST_COLUMNS], sort: null };
  try {
    const stored = JSON.parse(window.localStorage.getItem(VIEW_STORAGE_KEY) ?? 'null') as Partial<WatchlistView> | null;
    if (!stored || typeof stored !== 'object') return fallback;
    const columns = Array.isArray(stored.columns) ? stored.columns.filter(isColumnId) : fallback.columns;
    const sort = stored.sort
      && (stored.sort.key === 'symbol' || isColumnId(stored.sort.key))
      && (stored.sort.direction === 'asc' || stored.sort.direction === 'desc')
      ? { key: stored.sort.key, direction: stored.sort.direction }
      : null;
    return { columns, sort };
  } catch {
    return fallback;
  }
}

export function writeWatchlistView(view: WatchlistView): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(view));
  } catch {
    // Storage can be unavailable (private mode); the view then lasts for the session.
  }
}
