import { useEffect, useMemo, useState } from 'react';
import { POLL_INTERVALS_MS, startPolling } from '../../shared/timers';
import { tradingApi } from './tradingApi';
import { indicatorColumnLine, isIndicatorColumnId, type WatchlistColumnId, type WatchlistSnapshot } from './tradingWatchlistColumns';

/** Symbols one request carries (the server's limit). */
const MAX_SYMBOLS = 200;

export type WatchlistIndicatorValues = Readonly<Record<string, Readonly<Record<string, number | null>>>>;

/**
 * The watchlist's indicator column values (TVP-5.2), by symbol and column id, from the server, refreshed every
 * minute. Empty while there are no indicator columns.
 */
export function useWatchlistIndicatorValues(instrumentIds: readonly string[], interval: string, columnIds: readonly WatchlistColumnId[]): WatchlistIndicatorValues {
  const columns = useMemo(() => columnIds.filter(isIndicatorColumnId), [columnIds]);
  const symbols = useMemo(() => instrumentIds.slice(0, MAX_SYMBOLS), [instrumentIds]);
  // The request by value: the effect runs again only when symbols, interval or columns change, not their arrays.
  const key = JSON.stringify([symbols, interval, columns]);
  const [values, setValues] = useState<WatchlistIndicatorValues>({});

  useEffect(() => {
    const [requested, requestedInterval, requestedColumns] = JSON.parse(key) as [string[], string, WatchlistColumnId[]];
    if (requestedColumns.length === 0 || requested.length === 0) {
      setValues({});
      return undefined;
    }
    let cancelled = false;
    const lines = requestedColumns.flatMap((id) => {
      const line = indicatorColumnLine(id);
      return line ? [{ id, indicator_id: line.indicatorId, period: line.period, output: line.output }] : [];
    });
    const load = () =>
      tradingApi.indicatorValues(requested, requestedInterval, lines).then((rows) => {
        if (cancelled) return;
        setValues(Object.fromEntries(Object.entries(rows).map(([instrumentId, row]) => [
          instrumentId,
          Object.fromEntries(lines.map((line, index) => {
            const value = row[index] == null ? null : Number(row[index]);
            return [line.id, value != null && Number.isFinite(value) ? value : null];
          })),
        ])));
      }).catch(() => undefined); // keep the last values; the next refresh tries again
    void load();
    const stopPolling = startPolling(load, POLL_INTERVALS_MS.watchlistIndicators);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [key]);

  return values;
}

/** The watchlist's snapshots with their indicator column values joined in, so cells and sorting treat them alike. */
export function useWatchlistSnapshotsWithIndicators(
  snapshots: Readonly<Record<string, WatchlistSnapshot>>,
  instrumentIds: readonly string[],
  interval: string,
  columnIds: readonly WatchlistColumnId[],
): Readonly<Record<string, WatchlistSnapshot>> {
  const values = useWatchlistIndicatorValues(instrumentIds, interval, columnIds);
  return useMemo(() => {
    if (Object.keys(values).length === 0) return snapshots;
    const ids = new Set([...Object.keys(snapshots), ...Object.keys(values)]);
    return Object.fromEntries([...ids].map((id) => [id, { ...(id in snapshots ? snapshots[id] : { price: null, changePercent: null }), indicators: values[id] }]));
  }, [snapshots, values]);
}
