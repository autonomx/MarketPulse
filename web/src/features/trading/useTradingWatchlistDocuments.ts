import { useEffect, useMemo, useRef, useState } from 'react';
import { tradingWindowPresence } from './windowPresence';
import { tradingApi } from './tradingApi';
import type { CanonicalInstrument, TradingDocument } from './tradingTypes';
import {
  flagListColor,
  isNewerWatchlistPayload,
  newWatchlistPayload,
  serializeWatchlist,
  upgradeWatchlistPayload,
  watchlistSymbolsNeedRewrite,
  type WatchlistPayload,
} from './tradingWatchlistModel';

export type WatchlistSaveStatus = 'loading' | 'saved' | 'saving' | 'conflict' | 'error';

/** A change to a list, applied to whichever revision is current when it is saved. */
export type WatchlistOperation = (payload: WatchlistPayload) => WatchlistPayload;

const MAX_CONFLICT_RETRIES = 3;

type PendingOperation = { id: number; operation: WatchlistOperation };
type SaveResult = 'saved' | 'conflict' | 'error';

/** What the list shows: the last server revision with the unsaved operations applied in order. */
function withPending(record: TradingDocument, pending: readonly PendingOperation[] | undefined): TradingDocument {
  if (!pending?.length || isNewerWatchlistPayload(record.payload)) return record;
  const payload = pending.reduce((current, entry) => entry.operation(current), upgradeWatchlistPayload(record.payload));
  return { ...record, payload: serializeWatchlist(payload) };
}

function isConflictError(error: unknown): boolean {
  return error instanceof Error && error.message.includes('(409)');
}

function defaultWatchlistInstrumentIds(instruments: CanonicalInstrument[]): string[] {
  const equities = instruments
    .filter((instrument) => instrument.asset_class === 'equity')
    .map((instrument) => instrument.instrument_id);
  const crypto = instruments
    .filter((instrument) => instrument.asset_class === 'crypto' && instrument.venue === 'BINANCE')
    .map((instrument) => instrument.instrument_id);
  return [...equities, ...crypto];
}

/**
 * The user's watchlist documents and the selected list. `selectedListId` is a
 * watchlist record id or a generated flag list (`flag:<colour>`); `selected`
 * is null while a flag list is shown.
 */
export function useTradingWatchlistDocuments(instruments: CanonicalInstrument[]) {
  // Server state and unsaved operations are kept apart: a save always applies
  // its own operation to the last revision the server returned, never to the
  // optimistic copy, so an operation is persisted at most once.
  const [serverRecords, setServerRecords] = useState<TradingDocument[]>([]);
  const [pending, setPending] = useState<Record<string, PendingOperation[]>>({});
  const [selectedListId, setSelectedListId] = useState('');
  const [status, setStatus] = useState<WatchlistSaveStatus>('loading');
  const serverById = useRef(new Map<string, TradingDocument>());
  const queues = useRef(new Map<string, Promise<void>>());
  const nextOperationId = useRef(0);
  const batchFailure = useRef<SaveResult | null>(null);
  const records = useMemo(
    () => serverRecords.map((record) => withPending(record, pending[record.record_id])),
    [pending, serverRecords],
  );
  const flagColor = flagListColor(selectedListId);
  const selected = flagColor ? null : records.find((record) => record.record_id === selectedListId) ?? records[0] ?? null;
  const current = useMemo(() => upgradeWatchlistPayload(selected?.payload), [selected]);
  // Written by a newer Omnix: shown from its `instrumentIds`, never saved over.
  const readOnly = selected != null && isNewerWatchlistPayload(selected.payload);

  useEffect(() => {
    let cancelled = false;
    void tradingApi.documents('watchlists').then(async (loaded) => {
      if (cancelled) return;
      let next = loaded;
      if (next.length === 0) {
        const created = await tradingApi.createDocument(
          'watchlists',
          'default',
          serializeWatchlist(newWatchlistPayload('Default Watchlist', defaultWatchlistInstrumentIds(instruments))),
        );
        next = [created];
      } else {
        const defaultRecord = next.find((record) => record.record_id === 'default');
        // Built-in symbols are only seed data for a newly created watchlist.
        // Re-adding them here would resurrect symbols the user deliberately
        // removed after a reload or server restart. Only legacy crypto ids and
        // duplicates are rewritten.
        if (defaultRecord && !isNewerWatchlistPayload(defaultRecord.payload) && watchlistSymbolsNeedRewrite(defaultRecord.payload)) {
          const upgraded = serializeWatchlist(upgradeWatchlistPayload(defaultRecord.payload));
          try {
            const updated = await tradingApi.updateDocument('watchlists', defaultRecord, upgraded);
            next = next.map((record) => record.record_id === updated.record_id ? updated : record);
          } catch {
            next = next.map((record) => record.record_id === defaultRecord.record_id
              ? { ...record, payload: upgraded }
              : record);
          }
        }
      }
      if (cancelled) return;
      setAllServerRecords(next);
      setSelectedListId((currentListId) => (
        flagListColor(currentListId) || next.some((record) => record.record_id === currentListId)
          ? currentListId
          : next[0]?.record_id ?? ''
      ));
      setStatus('saved');
    }).catch(() => !cancelled && setStatus('error'));
    return () => { cancelled = true; };
  }, [instruments]);

  // A watchlist saved in another Trading window (TVP-4.3): take its revision; changes queued here apply on top of it.
  useEffect(() => tradingWindowPresence().onOtherWindowSaved((kind, id, revision) => {
    if (kind !== 'watchlist' || (serverById.current.get(id)?.revision ?? -1) >= revision) return;
    void tradingApi.documents('watchlists').then((loaded) => {
      const latest = loaded.find((record) => record.record_id === id);
      // Archived in the other window: the lists as they are now.
      if (!latest) return void (serverById.current.has(id) && setAllServerRecords(loaded));
      // Never back to an older revision (this window may have saved a newer one meanwhile).
      if ((serverById.current.get(id)?.revision ?? -1) >= latest.revision) return;
      if (serverById.current.has(id)) setServerRecord(latest);
      else setAllServerRecords(loaded);
    }).catch(() => undefined);
  }), []);

  function setAllServerRecords(next: TradingDocument[]) {
    serverById.current = new Map(next.map((record) => [record.record_id, record]));
    setServerRecords(next);
  }

  const setServerRecord = (record: TradingDocument) => {
    serverById.current.set(record.record_id, record);
    setServerRecords((items) => items.map((item) => item.record_id === record.record_id ? record : item));
  };

  /**
   * Save one operation on the last server revision. On a revision conflict
   * the latest revision is loaded and the operation applied to it, so a
   * change made elsewhere (another tab, another device) is kept.
   */
  const saveOperation = async (recordId: string, operation: WatchlistOperation): Promise<SaveResult> => {
    for (let attempt = 0; ; attempt += 1) {
      const record = serverById.current.get(recordId);
      if (!record || isNewerWatchlistPayload(record.payload)) return 'conflict';
      try {
        const saved = await tradingApi.updateDocument('watchlists', record, serializeWatchlist(operation(upgradeWatchlistPayload(record.payload))));
        setServerRecord(saved);
        tradingWindowPresence().announceSaved('watchlist', saved.record_id, saved.revision);
        return 'saved';
      } catch (error) {
        if (!isConflictError(error)) return 'error';
        const latest = attempt < MAX_CONFLICT_RETRIES
          ? (await tradingApi.documents('watchlists').catch(() => [])).find((item) => item.record_id === recordId)
          : undefined;
        if (!latest) return 'conflict';
        setServerRecord(latest);
      }
    }
  };

  /**
   * Show a change at once and queue its save. Saves of one list run one at a
   * time in order; a failed operation is dropped, so the list falls back to
   * the server state plus the operations still queued. Read-only lists
   * ignore changes.
   */
  const commit = (operation: WatchlistOperation) => {
    if (!selected || readOnly) return;
    const recordId = selected.record_id;
    const entry: PendingOperation = { id: nextOperationId.current, operation };
    nextOperationId.current += 1;
    setPending((current) => ({ ...current, [recordId]: [...(current[recordId] ?? []), entry] }));
    setStatus('saving');
    const run: Promise<void> = (queues.current.get(recordId) ?? Promise.resolve()).then(async () => {
      const result = await saveOperation(recordId, operation);
      setPending((current) => ({ ...current, [recordId]: (current[recordId] ?? []).filter((item) => item.id !== entry.id) }));
      if (result !== 'saved') batchFailure.current = result;
      if (queues.current.get(recordId) === run) {
        setStatus(batchFailure.current ?? 'saved');
        batchFailure.current = null;
      }
    });
    queues.current.set(recordId, run);
  };

  /** Create and select a watchlist; false when it could not be saved. */
  const create = async (payload: WatchlistPayload = newWatchlistPayload(`Watchlist ${records.length + 1}`)): Promise<boolean> => {
    setStatus('saving');
    try {
      const record = await tradingApi.createDocument('watchlists', `watchlist-${Date.now()}`, serializeWatchlist(payload));
      serverById.current.set(record.record_id, record);
      setServerRecords((items) => [...items, record]);
      setSelectedListId(record.record_id);
      tradingWindowPresence().announceSaved('watchlist', record.record_id, record.revision);
      setStatus('saved');
      return true;
    } catch {
      setStatus('error');
      return false;
    }
  };

  const archive = async () => {
    if (!selected || records.length <= 1) return;
    setStatus('saving');
    try {
      await tradingApi.archiveDocument('watchlists', selected);
      tradingWindowPresence().announceSaved('watchlist', selected.record_id, selected.revision + 1);
      const next = serverRecords.filter((item) => item.record_id !== selected.record_id);
      setAllServerRecords(next);
      setSelectedListId(next[0]?.record_id ?? '');
      setStatus('saved');
    } catch (error) {
      setStatus(isConflictError(error) ? 'conflict' : 'error');
    }
  };

  return {
    records,
    selectedListId,
    setSelectedListId,
    flagColor,
    selected,
    current,
    readOnly,
    status,
    commit,
    create,
    archive,
  };
}
