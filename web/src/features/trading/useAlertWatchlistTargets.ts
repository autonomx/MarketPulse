/**
 * Watchlist alerts (TVP-1.7): an alert's "Applies to" choice between the chart's symbol and a watchlist, whose
 * alerts carry the instrument `watchlist:<record id>` and run on every symbol of the list.
 */
import { useCallback, useEffect, useState } from 'react';
import { tradingApi } from './tradingApi';

export const WATCHLIST_ALERT_PREFIX = 'watchlist:';

/** The watchlist a watchlist alert's instrument names, or null. */
export function watchlistIdOf(instrumentId: string | null | undefined): string | null {
  return instrumentId?.startsWith(WATCHLIST_ALERT_PREFIX) ? instrumentId.slice(WATCHLIST_ALERT_PREFIX.length) || null : null;
}

/** What an alert on a list evaluates, from the server's capacity: the list, the default limit and the providers' cap. */
export function watchlistCapacityNote(capacity: { symbol_count: number; provider_cap: number; default_limit: number }): string {
  const evaluated = Math.min(capacity.symbol_count, capacity.default_limit, capacity.provider_cap);
  const capped = capacity.provider_cap < Math.min(capacity.symbol_count, capacity.default_limit) ? ` (at most ${capacity.provider_cap} within its providers' request budgets)` : '';
  return `Runs on ${evaluated} of the list's ${capacity.symbol_count} symbols${capped}, each firing on its own.`;
}

export function useAlertWatchlistTargets(instrumentId: string, symbol: string, target: string | undefined) {
  const [lists, setLists] = useState<Array<{ id: string; name: string }>>([]);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void tradingApi.documents('watchlists')
      .then((records) => { if (live) setLists(records.map((record) => ({ id: record.record_id, name: String((record.payload as { name?: unknown }).name ?? record.record_id) }))); })
      .catch(() => { if (live) setLists([]); });
    return () => { live = false; };
  }, []);
  const watchlistId = watchlistIdOf(target);
  useEffect(() => {
    setNote(null);
    if (!watchlistId) return undefined;
    let live = true;
    void tradingApi.watchlistAlertCapacity(watchlistId)
      .then((capacity) => { if (live) setNote(watchlistCapacityNote(capacity)); })
      .catch(() => { if (live) setNote(null); });
    return () => { live = false; };
  }, [watchlistId]);
  const choices = [
    { value: instrumentId, label: `This symbol (${symbol})` },
    ...lists.map((list) => ({ value: `${WATCHLIST_ALERT_PREFIX}${list.id}`, label: `Watchlist: ${list.name}` })),
  ];
  /** An alert's list label ("List: Tech"), or null for an alert on one symbol. */
  const labelFor = useCallback((alertInstrumentId: string): string | null => {
    const id = watchlistIdOf(alertInstrumentId);
    return id ? `List: ${lists.find((list) => list.id === id)?.name ?? id}` : null;
  }, [lists]);
  return { choices, note, labelFor };
}
