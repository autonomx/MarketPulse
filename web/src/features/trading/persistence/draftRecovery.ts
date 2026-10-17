import type { TradingWorkspacePayload } from './workspaceDocument';

const DATABASE = 'omnix-trading-drafts';
const STORE = 'workspace-drafts';
const KEY = 'main';

function openDatabase(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE)) database.createObjectStore(STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Unable to open Trading draft database'));
  });
}

export const tradingDraftRecovery = {
  async load(): Promise<TradingWorkspacePayload | null> {
    const database = await openDatabase();
    if (!database) return null;
    return new Promise<TradingWorkspacePayload | null>((resolve, reject) => {
      const request = database.transaction(STORE, 'readonly').objectStore(STORE).get(KEY);
      request.onsuccess = () => resolve((request.result as TradingWorkspacePayload | undefined) ?? null);
      request.onerror = () => reject(request.error ?? new Error('Unable to read Trading draft'));
    }).finally(() => database.close());
  },

  async save(payload: TradingWorkspacePayload): Promise<void> {
    const database = await openDatabase();
    if (!database) return;
    await new Promise<void>((resolve, reject) => {
      const request = database.transaction(STORE, 'readwrite').objectStore(STORE).put(payload, KEY);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error ?? new Error('Unable to save Trading draft'));
    }).finally(() => database.close());
  },

  async clear(): Promise<void> {
    const database = await openDatabase();
    if (!database) return;
    await new Promise<void>((resolve, reject) => {
      const request = database.transaction(STORE, 'readwrite').objectStore(STORE).delete(KEY);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error ?? new Error('Unable to clear Trading draft'));
    }).finally(() => database.close());
  },
};
