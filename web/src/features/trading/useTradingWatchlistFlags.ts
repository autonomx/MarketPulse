import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { tradingApi } from './tradingApi';
import type { TradingDocument } from './tradingTypes';
import {
  WATCHLIST_FLAGS_RECORD_ID,
  emptyWatchlistFlags,
  readWatchlistFlags,
  setWatchlistFlags,
  watchlistFlagMap,
  type WatchlistFlagColor,
  type WatchlistFlagsPayload,
} from './tradingWatchlistModel';

type FlagsStatus = 'loading' | 'ready' | 'saving' | 'error';
type FlagsState = { payload: WatchlistFlagsPayload; status: FlagsStatus };

function isConflict(error: unknown): boolean {
  return error instanceof Error && error.message.includes('(409)');
}

async function latestFlagsRecord(): Promise<TradingDocument | null> {
  const records = await tradingApi.documents('watchlist-flags');
  return records.find((record) => record.record_id === WATCHLIST_FLAGS_RECORD_ID) ?? null;
}

// One flags document for the page (TVP-5.1): the watchlist, the chart and the screener read and flag through it, so
// a flag set anywhere shows everywhere at once.
let state: FlagsState = { payload: emptyWatchlistFlags(), status: 'loading' };
let record: TradingDocument | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function update(next: Partial<FlagsState>): void {
  state = { ...state, ...next };
  for (const listener of listeners) listener();
}

function load(): Promise<void> {
  loading ??= latestFlagsRecord().then((loaded) => {
    record = loaded;
    update({ payload: readWatchlistFlags(loaded?.payload), status: 'ready' });
  }).catch(() => {
    loading = null; // a later mount tries again
    update({ status: 'error' });
  });
  return loading;
}

/**
 * Flags or unflags symbols. Writes are applied as operations, so a revision conflict (another tab flagged a symbol)
 * re-applies the change to the latest document instead of overwriting it.
 */
export async function setTradingWatchlistFlag(instrumentIds: readonly string[], color: WatchlistFlagColor | null): Promise<void> {
  if (instrumentIds.length === 0) return;
  await load();
  const previous = { record, payload: readWatchlistFlags(record?.payload) };
  update({ payload: setWatchlistFlags(state.payload, instrumentIds, color), status: 'saving' });
  let current = record;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const next = setWatchlistFlags(readWatchlistFlags(current?.payload), instrumentIds, color);
    try {
      const saved = current
        ? await tradingApi.updateDocument('watchlist-flags', current, next as unknown as Record<string, unknown>)
        : await tradingApi.createDocument('watchlist-flags', WATCHLIST_FLAGS_RECORD_ID, next as unknown as Record<string, unknown>);
      record = saved;
      update({ payload: readWatchlistFlags(saved.payload), status: 'ready' });
      return;
    } catch (error) {
      if (!isConflict(error) || attempt === 3) break;
      current = await latestFlagsRecord().catch(() => current);
    }
  }
  record = previous.record;
  update({ payload: previous.payload, status: 'error' });
}

/** Forgets the loaded document (tests; a signed-out page). */
export function resetTradingWatchlistFlags(): void {
  state = { payload: emptyWatchlistFlags(), status: 'loading' };
  record = null;
  loading = null;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The user's colour flags (TVP-5.1), shared by every component that shows or sets them. */
export function useTradingWatchlistFlags() {
  const current = useSyncExternalStore(subscribe, () => state, () => state);
  useEffect(() => {
    void load();
  }, []);
  const flags = useMemo(() => watchlistFlagMap(current.payload), [current.payload]);
  return { payload: current.payload, flags, status: current.status, setFlag: setTradingWatchlistFlag };
}
