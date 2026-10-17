// Indicator columns in the watchlist (TVP-5.2): any server registry indicator line, valued by the server, sorted like
// the other columns.
import { cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./useTradingAlerts', () => ({ useAlertIndicatorIds: () => new Set(['rsi', 'sma', 'tv-advance-decline-line']) }));

import { tradingApi } from './tradingApi';
import {
  MAX_INDICATOR_COLUMNS,
  indicatorColumnId,
  indicatorColumnLabel,
  indicatorColumnLine,
  readWatchlistView,
  toggleWatchlistColumn,
  watchlistColumn,
  watchlistComparator,
  writeWatchlistView,
  type WatchlistColumnId,
} from './tradingWatchlistColumns';
import { useWatchlistSnapshotsWithIndicators } from './useWatchlistIndicatorValues';
import { WatchlistIndicatorColumnPicker } from './WatchlistIndicatorColumnPicker';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const rsi = indicatorColumnId({ indicatorId: 'rsi', period: 14, output: 'rsi:14' });

describe('watchlist indicator columns (TVP-5.2)', () => {
  it('names a line by indicator, period and output, and reads its value from the snapshot', () => {
    expect(rsi).toBe('indicator:rsi|14|rsi:14');
    expect(indicatorColumnLine(rsi)).toEqual({ indicatorId: 'rsi', period: 14, output: 'rsi:14' });
    expect(indicatorColumnLine('indicator:rsi|x|rsi:14')).toBeNull();
    expect(indicatorColumnLine('indicator:rsi|14|sma:14')).toBeNull();
    expect(indicatorColumnLabel({ indicatorId: 'rsi', period: 14, output: 'rsi:14' })).toBe('RSI 14');
    expect(indicatorColumnLabel({ indicatorId: 'macd', period: 9, output: 'macd:12:26:signal' })).toBe('MACD 9 · signal');
    const column = watchlistColumn(rsi)!;
    expect(column.value({ price: '1', changePercent: 0, indicators: { [rsi]: 71.25 } })).toBe(71.25);
    expect(column.format({ price: '1', changePercent: 0, indicators: { [rsi]: 71.25 } })).toBe('71.25');
    expect(column.format(undefined)).toBe('—');
  });

  it('keeps indicator columns after the built-in ones, in the order added, at most eight', () => {
    const sma = indicatorColumnId({ indicatorId: 'sma', period: 20, output: 'sma:20' });
    let columns: WatchlistColumnId[] = toggleWatchlistColumn(['last'], rsi);
    columns = toggleWatchlistColumn(columns, 'changePercent');
    columns = toggleWatchlistColumn(columns, sma);
    expect(columns).toEqual(['last', 'changePercent', rsi, sma]);
    expect(toggleWatchlistColumn(columns, rsi)).toEqual(['last', 'changePercent', sma]);
    let many: WatchlistColumnId[] = [];
    for (let period = 1; period <= MAX_INDICATOR_COLUMNS + 2; period += 1) many = toggleWatchlistColumn(many, indicatorColumnId({ indicatorId: 'sma', period, output: `sma:${period}` }));
    expect(many).toHaveLength(MAX_INDICATOR_COLUMNS);
  });

  it('is remembered with the view and sorts like any column', () => {
    writeWatchlistView({ columns: ['last', rsi], sort: { key: rsi, direction: 'desc' } });
    expect(readWatchlistView()).toEqual({ columns: ['last', rsi], sort: { key: rsi, direction: 'desc' } });
    const snapshots = { a: { price: '1', changePercent: 0, indicators: { [rsi]: 40 } }, b: { price: '1', changePercent: 0, indicators: { [rsi]: 75 } }, c: { price: '1', changePercent: 0 } };
    const compare = watchlistComparator({ key: rsi, direction: 'desc' }, snapshots, (id) => id)!;
    expect(['a', 'b', 'c'].sort(compare)).toEqual(['b', 'a', 'c']);
  });

  it('adds a column for an indicator line the server computes', () => {
    const onToggleColumn = vi.fn();
    render(<WatchlistIndicatorColumnPicker columns={[]} onToggleColumn={onToggleColumn} />);
    fireEvent.change(screen.getByLabelText('Indicator for a column'), { target: { value: 'sma' } });
    fireEvent.change(screen.getByLabelText('Indicator column period'), { target: { value: '50' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add column' }));
    expect(onToggleColumn).toHaveBeenCalledWith(indicatorColumnId({ indicatorId: 'sma', period: 50, output: 'sma:50' }));
    cleanup();
    // A data series has no period.
    render(<WatchlistIndicatorColumnPicker columns={[rsi]} onToggleColumn={onToggleColumn} />);
    expect(screen.getByRole('menuitemcheckbox', { name: 'RSI 14' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Indicator for a column'), { target: { value: 'tv-advance-decline-line' } });
    expect(screen.queryByLabelText('Indicator column period')).toBeNull();
  });

  it('asks the server for the column values and joins them into the snapshots', async () => {
    const values = vi.spyOn(tradingApi, 'indicatorValues').mockResolvedValue({ 'equity:NASDAQ:AAPL': ['55.5'], 'equity:NASDAQ:MSFT': [null] });
    const prices = { 'equity:NASDAQ:AAPL': { price: '200', changePercent: 1 } };
    const { result } = renderHook(() => useWatchlistSnapshotsWithIndicators(prices, ['equity:NASDAQ:AAPL', 'equity:NASDAQ:MSFT'], '1d', ['last', rsi]));
    await waitFor(() => expect(result.current['equity:NASDAQ:AAPL']?.indicators?.[rsi]).toBe(55.5));
    expect(result.current['equity:NASDAQ:AAPL'].price).toBe('200');
    expect(result.current['equity:NASDAQ:MSFT'].indicators?.[rsi]).toBeNull();
    expect(values).toHaveBeenCalledWith(['equity:NASDAQ:AAPL', 'equity:NASDAQ:MSFT'], '1d', [{ id: rsi, indicator_id: 'rsi', period: 14, output: 'rsi:14' }]);
  });

  it('asks nothing without indicator columns', () => {
    const values = vi.spyOn(tradingApi, 'indicatorValues');
    const prices = { a: { price: '1', changePercent: 0 } };
    const { result } = renderHook(() => useWatchlistSnapshotsWithIndicators(prices, ['a'], '1d', ['last']));
    expect(result.current).toBe(prices);
    expect(values).not.toHaveBeenCalled();
  });
});
