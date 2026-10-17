import { afterEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_WATCHLIST_COLUMNS,
  columnTone,
  nextWatchlistSort,
  readWatchlistView,
  toggleWatchlistColumn,
  watchlistColumn,
  watchlistComparator,
  writeWatchlistView,
  type WatchlistSnapshot,
} from './tradingWatchlistColumns';

afterEach(() => window.localStorage.clear());

const snapshots: Record<string, WatchlistSnapshot> = {
  a: { price: '10', changePercent: 1, volume: 500, high: 11 },
  b: { price: '30', changePercent: -2, volume: null, high: 31 },
  c: { price: '20', changePercent: 3, volume: 900, high: null },
};
const symbols: Record<string, string> = { a: 'MSFT', b: 'AAPL', c: 'NVDA' };
const order = (comparator: ((left: string, right: string) => number) | undefined) =>
  ['a', 'b', 'c'].sort((left, right) => comparator?.(left, right) ?? 0);

describe('watchlist columns', () => {
  it('sorts by any column with empty values last in both directions', () => {
    expect(order(watchlistComparator({ key: 'last', direction: 'desc' }, snapshots, (id) => symbols[id]))).toEqual(['b', 'c', 'a']);
    expect(order(watchlistComparator({ key: 'volume', direction: 'desc' }, snapshots, (id) => symbols[id]))).toEqual(['c', 'a', 'b']);
    expect(order(watchlistComparator({ key: 'volume', direction: 'asc' }, snapshots, (id) => symbols[id]))).toEqual(['a', 'c', 'b']);
    expect(order(watchlistComparator({ key: 'high', direction: 'asc' }, snapshots, (id) => symbols[id]))).toEqual(['a', 'b', 'c']);
    expect(order(watchlistComparator({ key: 'symbol', direction: 'asc' }, snapshots, (id) => symbols[id]))).toEqual(['b', 'a', 'c']);
    expect(watchlistComparator(null, snapshots, (id) => symbols[id])).toBeUndefined();
  });

  it('cycles a header through its first direction, the other direction and the list order', () => {
    expect(nextWatchlistSort(null, 'volume')).toEqual({ key: 'volume', direction: 'desc' });
    expect(nextWatchlistSort({ key: 'volume', direction: 'desc' }, 'volume')).toEqual({ key: 'volume', direction: 'asc' });
    expect(nextWatchlistSort({ key: 'volume', direction: 'asc' }, 'volume')).toBeNull();
    expect(nextWatchlistSort({ key: 'volume', direction: 'asc' }, 'symbol')).toEqual({ key: 'symbol', direction: 'asc' });
  });

  it('keeps chosen columns in the chooser order', () => {
    expect(toggleWatchlistColumn(['changePercent', 'last'], 'volume')).toEqual(['last', 'changePercent', 'volume']);
    expect(toggleWatchlistColumn(['last', 'changePercent'], 'last')).toEqual(['changePercent']);
  });

  it('formats and colours values', () => {
    const change = watchlistColumn('change');
    const snapshot: WatchlistSnapshot = { price: '110', changePercent: 10, change: 10, volume: 1_250_000, relativeVolume: 1.5 };
    expect(change?.format(snapshot)).toBe('+10.00');
    expect(change && columnTone(change, snapshot)).toBe('positive');
    expect(watchlistColumn('volume')?.format(snapshot)).toBe('1.25M');
    expect(watchlistColumn('relativeVolume')?.format(snapshot)).toBe('1.50×');
    expect(watchlistColumn('extendedPrice')?.format(snapshot)).toBe('—');
  });

  it('remembers the view and ignores unknown stored columns', () => {
    expect(readWatchlistView()).toEqual({ columns: [...DEFAULT_WATCHLIST_COLUMNS], sort: null });
    writeWatchlistView({ columns: ['volume', 'high'], sort: { key: 'high', direction: 'asc' } });
    expect(readWatchlistView()).toEqual({ columns: ['volume', 'high'], sort: { key: 'high', direction: 'asc' } });
    window.localStorage.setItem('omnix.trading.watchlist-view', JSON.stringify({ columns: ['rsi', 'low'], sort: { key: 'rsi', direction: 'up' } }));
    expect(readWatchlistView()).toEqual({ columns: ['low'], sort: null });
  });
});
