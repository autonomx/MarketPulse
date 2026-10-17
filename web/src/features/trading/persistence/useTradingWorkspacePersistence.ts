import { useCallback, useEffect, useRef, useState } from 'react';
import { tradingApi } from '../tradingApi';
import { freshTradingSessionState, useTradingStore } from '../tradingStore';
import type { TradingDocument } from '../tradingTypes';
import { tradingDraftRecovery } from './draftRecovery';
import { DEFAULT_FAVORITE_INTERVALS } from '../tradingIntervals';
import { forgetRequestedTradingWindow, requestedTradingWindow, tradingWindowPresence } from '../windowPresence';
import {
  parseTradingWorkspace,
  serializeTradingWorkspace,
  type TradingWorkspacePayload,
} from './workspaceDocument';

export type WorkspacePersistenceStatus = 'loading' | 'saved' | 'saving' | 'draft' | 'conflict' | 'error';
export type WorkspaceImportResult = 'imported' | 'invalid' | 'error';
export type TradingWorkspaceSummary = { workspaceId: string; name: string; revision: number };

type ConflictState = {
  localPayload: TradingWorkspacePayload;
  serverRecord: TradingDocument | null;
  serverPayload: TradingWorkspacePayload | null;
};

export type TradingWorkspacePersistence = {
  status: WorkspacePersistenceStatus;
  workspaces: TradingWorkspaceSummary[];
  activeWorkspaceId: string;
  activeWorkspaceName: string;
  hasConflict: boolean;
  selectWorkspace: (workspaceId: string) => Promise<void>;
  /** `prepare` runs after the new workspace document exists and before it becomes active (TVP-2.5 duplicates drawings there); false when it was not created. */
  createWorkspace: (name: string, prepare?: (workspaceId: string) => Promise<void>) => Promise<boolean>;
  /** Saves an exported workspace file as a new workspace and opens it: 'invalid' when it is not one. */
  importWorkspace: (value: unknown, name: string) => Promise<WorkspaceImportResult>;
  renameWorkspace: (name: string) => Promise<void>;
  deleteWorkspace: () => Promise<void>;
  resolveConflict: (resolution: 'reload' | 'overwrite') => Promise<void>;
  /** Saves the open workspace now instead of after the edit delay. */
  saveNow: () => Promise<void>;
};

let activeWorkspaceScopeId = 'workspace-uninitialized';

/** Return the active persisted workspace namespace for tab-scoped child data. */
export function currentTradingWorkspaceScopeId(): string {
  return activeWorkspaceScopeId;
}

function setTradingWorkspaceScopeId(workspaceId: string): void {
  activeWorkspaceScopeId = workspaceId.trim() || 'workspace-uninitialized';
}

function safeWorkspace(record: TradingDocument | null): TradingWorkspacePayload | null {
  return record ? parseTradingWorkspace(record.payload) : null;
}

function summary(record: TradingDocument): TradingWorkspaceSummary {
  return {
    workspaceId: record.record_id,
    name: safeWorkspace(record)?.name ?? record.record_id,
    revision: record.revision,
  };
}

function workspaceId(name: string): string {
  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'workspace';
  return `${slug}-${crypto.randomUUID().slice(0, 8)}`;
}

/**
 * Puts a stored workspace into the store (without saving for it). `keepSession` keeps this window's session-only
 * state, replay and closed tabs (a reload of what another window saved).
 */
function applyWorkspace(value: unknown, applyingRef: { current: boolean }, keepSession: boolean, tabId: string | null = null): boolean {
  const payload = parseTradingWorkspace(value);
  if (!payload) return false;
  const tabs = payload.tabs ?? [{
    tabId: 'tab-1',
    name: 'Main Session',
    layout: payload.layout,
    activeChartId: payload.activeChartId,
    charts: payload.charts,
    links: payload.links,
    panels: payload.panels,
  }];
  // The tab this window shows (or was opened on) when the workspace has it, else the stored one.
  const preferred = tabId && tabs.some((tab) => tab.tabId === tabId) ? tabId : payload.activeTabId;
  const activeTabId = preferred && tabs.some((tab) => tab.tabId === preferred) ? preferred : tabs[0].tabId;
  const activeTab = tabs.find((tab) => tab.tabId === activeTabId) ?? tabs[0];
  applyingRef.current = true;
  useTradingStore.setState({
    activeTabId,
    tabs,
    layout: activeTab.layout,
    activeChartId: activeTab.activeChartId,
    ...(keepSession ? {} : freshTradingSessionState()),
    charts: activeTab.charts,
    links: activeTab.links,
    panels: activeTab.panels,
    favoriteInstrumentIds: payload.favoriteInstrumentIds,
    favoriteIntervals: payload.favoriteIntervals ?? [...DEFAULT_FAVORITE_INTERVALS],
  });
  applyingRef.current = false;
  return true;
}

/** An exported workspace file as a workspace named `name`, or null when it is not one. An export carries the charts,
 * not the favourites: those stay this window's. */
function importedWorkspacePayload(value: unknown, name: string): TradingWorkspacePayload | null {
  const cleanName = name.trim();
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  const parsed = source && cleanName ? parseTradingWorkspace({ ...source, name: cleanName }) : null;
  if (!source || !parsed) return null;
  const state = useTradingStore.getState();
  return {
    ...parsed,
    favoriteInstrumentIds: Array.isArray(source.favoriteInstrumentIds) ? parsed.favoriteInstrumentIds : state.favoriteInstrumentIds,
    favoriteIntervals: parsed.favoriteIntervals ?? state.favoriteIntervals,
  };
}

/** The workspace a window starts on: the one it was opened on (`?workspace=`, TVP-4.3), else the main one. */
function initialRecord(records: readonly TradingDocument[]): TradingDocument | null {
  const requested = requestedTradingWindow().workspaceId;
  return records.find((item) => item.record_id === requested) ?? records.find((item) => item.record_id === 'main') ?? records[0] ?? null;
}

type WindowSyncInput = {
  status: WorkspacePersistenceStatus;
  activeIdRef: { current: string };
  recordsRef: { current: Map<string, TradingDocument> };
  cancelledRef: { current: boolean };
  applyingRef: { current: boolean };
  hydrate: (value: unknown, keepSession?: boolean, tabId?: string | null) => boolean;
};

/**
 * Several windows (TVP-4.3, 4.4):
 * - another window saved this workspace: reload it when this one has no edits of its own (an edit made here meanwhile
 *   meets the usual revision conflict instead), keeping the tab this window shows;
 * - leaving with edits not yet saved asks first (the draft is also kept for recovery).
 */
function useWorkspaceWindowSync({ status, activeIdRef, recordsRef, cancelledRef, applyingRef, hydrate }: WindowSyncInput): void {
  const statusRef = useRef<WorkspacePersistenceStatus>(status);
  useEffect(() => {
    statusRef.current = status;
  }, [status, statusRef]);

  useEffect(() => tradingWindowPresence().onOtherWindowSaved((kind, id, revision) => {
    if (kind !== 'workspace' || id !== activeIdRef.current || statusRef.current !== 'saved') return;
    if ((recordsRef.current.get(id)?.revision ?? -1) >= revision) return;
    // Fetched without touching the known revision: only a reload takes it, so an edit made here meanwhile saves on
    // the old revision and meets the revision conflict instead of overwriting the other window's save.
    void tradingApi.documents('workspaces').then((records) => {
      const latest = records.find((record) => record.record_id === id);
      if (!latest || cancelledRef.current || id !== activeIdRef.current || statusRef.current !== 'saved') return;
      if ((recordsRef.current.get(id)?.revision ?? -1) >= latest.revision) return;
      recordsRef.current.set(id, latest);
      hydrate(latest.payload, true, useTradingStore.getState().activeTabId);
    }, () => undefined);
  }), [activeIdRef, applyingRef, cancelledRef, hydrate, recordsRef, statusRef]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (statusRef.current !== 'draft' && statusRef.current !== 'saving' && statusRef.current !== 'conflict') return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [statusRef]);
}

export function useTradingWorkspacePersistence(): TradingWorkspacePersistence {
  const [status, setStatus] = useState<WorkspacePersistenceStatus>('loading');
  const [workspaces, setWorkspaces] = useState<TradingWorkspaceSummary[]>([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState('');
  const recordsRef = useRef<Map<string, TradingDocument>>(new Map());
  const activeIdRef = useRef('');
  const conflictRef = useRef<ConflictState | null>(null);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const applyingRef = useRef(false);
  const cancelledRef = useRef(false);
  // The payload last saved or loaded: a store change that leaves it as it is saves nothing (TVP-4.3).
  const lastPayloadRef = useRef('');

  const refreshSummaries = useCallback(() => {
    setWorkspaces([...recordsRef.current.values()].map(summary).sort((left, right) => left.name.localeCompare(right.name)));
  }, []);

  const activeName = useCallback(() => {
    const record = recordsRef.current.get(activeIdRef.current) ?? null;
    return safeWorkspace(record)?.name ?? 'Main Workspace';
  }, []);

  const currentPayload = useCallback((name = activeName()) => {
    const state = useTradingStore.getState();
    return serializeTradingWorkspace({
      name,
      layout: state.layout,
      activeChartId: state.activeChartId,
      charts: state.charts,
      links: state.links,
      panels: state.panels,
      favoriteInstrumentIds: state.favoriteInstrumentIds,
      favoriteIntervals: state.favoriteIntervals,
      activeTabId: state.activeTabId,
      tabs: state.tabs,
    });
  }, [activeName]);

  /** Loads a stored workspace, showing `tabId` when it has it; what it then shows counts as saved. */
  const hydrate = useCallback((value: unknown, keepSession = false, tabId: string | null = null): boolean => {
    if (!applyWorkspace(value, applyingRef, keepSession, tabId)) return false;
    lastPayloadRef.current = JSON.stringify(currentPayload());
    return true;
  }, [currentPayload]);

  const loadLatestRecord = useCallback(async (id: string): Promise<TradingDocument | null> => {
    const records = await tradingApi.documents('workspaces').catch(() => []);
    for (const record of records) recordsRef.current.set(record.record_id, record);
    refreshSummaries();
    return records.find((record) => record.record_id === id) ?? null;
  }, [refreshSummaries]);

  const savePayload = useCallback(async (
    record: TradingDocument,
    payload: TradingWorkspacePayload,
  ): Promise<TradingDocument | null> => {
    setStatus('saving');
    try {
      const saved = await tradingApi.updateDocument('workspaces', record, payload as unknown as Record<string, unknown>);
      if (cancelledRef.current) return null;
      recordsRef.current.set(saved.record_id, saved);
      conflictRef.current = null;
      lastPayloadRef.current = JSON.stringify(payload);
      tradingWindowPresence().announceSaved('workspace', saved.record_id, saved.revision); // other windows reload it (TVP-4.3)
      await tradingDraftRecovery.clear();
      refreshSummaries();
      setStatus('saved');
      return saved;
    } catch (error) {
      if (cancelledRef.current) return null;
      if (error instanceof Error && error.message.includes('(409)')) {
        const latest = await loadLatestRecord(record.record_id);
        conflictRef.current = {
          localPayload: payload,
          serverRecord: latest,
          serverPayload: safeWorkspace(latest),
        };
        setStatus('conflict');
      } else {
        setStatus('error');
      }
      return null;
    }
  }, [loadLatestRecord, refreshSummaries]);

  const saveActive = useCallback(async (): Promise<void> => {
    const id = activeIdRef.current;
    const record = recordsRef.current.get(id);
    if (!record || conflictRef.current) return;
    await savePayload(record, currentPayload());
  }, [currentPayload, savePayload]);

  /** Saves now instead of after the edit delay (Ctrl+S, switching workspaces). */
  const saveNow = useCallback(async (): Promise<void> => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = null;
    await saveActive();
  }, [saveActive]);

  useEffect(() => {
    cancelledRef.current = false;
    // React StrictMode intentionally mounts effects twice in development. The
    // first async initializer must remain cancelled even after the second
    // mount resets the shared cancellation ref; otherwise both initializers
    // can observe an empty list and race to create `main`.
    let disposed = false;
    let unsubscribe: () => void = () => {};

    const initialize = async () => {
      try {
        const [records, draft] = await Promise.all([
          tradingApi.documents('workspaces'),
          tradingDraftRecovery.load().catch(() => null),
        ]);
        if (disposed || cancelledRef.current) return;
        recordsRef.current = new Map(records.map((record) => [record.record_id, record]));
        let record = initialRecord(records);
        if (!record) {
          setTradingWorkspaceScopeId('main');
          if (draft) hydrate(draft);
          const created = await tradingApi.createDocument(
            'workspaces',
            'main',
            currentPayload('Main Workspace') as unknown as Record<string, unknown>,
          );
          if (disposed || cancelledRef.current) return;
          recordsRef.current.set(created.record_id, created);
          record = created;
        }
        if (disposed || cancelledRef.current) return;
        activeIdRef.current = record.record_id;
        setTradingWorkspaceScopeId(record.record_id);
        setActiveWorkspaceId(record.record_id);
        if (!hydrate(record.payload, false, requestedTradingWindow().tabId) && draft) hydrate(draft);
        forgetRequestedTradingWindow();
        refreshSummaries();
        setStatus('saved');

        unsubscribe = useTradingStore.subscribe(() => {
          if (applyingRef.current || cancelledRef.current) return;
          const payload = currentPayload();
          if (JSON.stringify(payload) === lastPayloadRef.current && !conflictRef.current) {
            // Back to what is saved (session-only changes, such as replay or the drawing tool): nothing to save.
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
            return setStatus((current) => (current === 'draft' ? 'saved' : current));
          }
          void tradingDraftRecovery.save(payload).catch(() => undefined);
          if (conflictRef.current) {
            conflictRef.current.localPayload = payload;
            setStatus('conflict');
            return;
          }
          setStatus('draft');
          if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
          saveTimerRef.current = setTimeout(() => void saveActive(), 700);
        });
      } catch {
        if (!disposed && !cancelledRef.current) setStatus('error');
      }
    };

    void initialize();
    return () => {
      disposed = true;
      cancelledRef.current = true;
      unsubscribe();
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [currentPayload, hydrate, refreshSummaries, saveActive]);

  useWorkspaceWindowSync({ status, activeIdRef, recordsRef, cancelledRef, applyingRef, hydrate });
  const selectWorkspace = useCallback(async (id: string) => {
    if (id === activeIdRef.current || !recordsRef.current.has(id)) return;
    await saveNow();
    const record = recordsRef.current.get(id);
    if (!record) return;
    setTradingWorkspaceScopeId(id);
    if (!hydrate(record.payload)) return;
    activeIdRef.current = id;
    setActiveWorkspaceId(id);
    conflictRef.current = null;
    setStatus('saved');
  }, [hydrate, saveNow]);

  /** Saves the open workspace, then opens a new one: a copy of it, or `imported` (a workspace file). */
  const createWorkspace = useCallback(async (
    name: string,
    prepare?: (workspaceId: string) => Promise<void>,
    imported?: TradingWorkspacePayload,
  ): Promise<boolean> => {
    const cleanName = name.trim();
    if (!cleanName) return false;
    await saveNow();
    setStatus('saving');
    try {
      const id = workspaceId(cleanName);
      const payload = imported ?? currentPayload(cleanName);
      const created = await tradingApi.createDocument('workspaces', id, payload as unknown as Record<string, unknown>);
      await prepare?.(id).catch(() => undefined);
      recordsRef.current.set(id, created);
      activeIdRef.current = id;
      setTradingWorkspaceScopeId(id);
      if (imported) hydrate(imported);
      setActiveWorkspaceId(id);
      conflictRef.current = null;
      refreshSummaries();
      setStatus('saved');
      return true;
    } catch {
      setStatus('error');
      return false;
    }
  }, [currentPayload, hydrate, refreshSummaries, saveNow]);

  const importWorkspace = useCallback(async (value: unknown, name: string): Promise<WorkspaceImportResult> => {
    const payload = importedWorkspacePayload(value, name);
    if (!payload) return 'invalid';
    return (await createWorkspace(payload.name, undefined, payload)) ? 'imported' : 'error';
  }, [createWorkspace]);

  const renameWorkspace = useCallback(async (name: string) => {
    const cleanName = name.trim();
    const record = recordsRef.current.get(activeIdRef.current);
    if (!record || !cleanName) return;
    await savePayload(record, currentPayload(cleanName));
  }, [currentPayload, savePayload]);

  const deleteWorkspace = useCallback(async () => {
    if (recordsRef.current.size <= 1) return;
    const current = recordsRef.current.get(activeIdRef.current);
    if (!current) return;
    setStatus('saving');
    try {
      await tradingApi.archiveDocument('workspaces', current);
      recordsRef.current.delete(current.record_id);
      const next = [...recordsRef.current.values()][0];
      activeIdRef.current = next.record_id;
      setTradingWorkspaceScopeId(next.record_id);
      setActiveWorkspaceId(next.record_id);
      hydrate(next.payload);
      conflictRef.current = null;
      refreshSummaries();
      setStatus('saved');
    } catch (error) {
      if (error instanceof Error && error.message.includes('(409)')) {
        await loadLatestRecord(current.record_id);
        setStatus('conflict');
      } else {
        setStatus('error');
      }
    }
  }, [hydrate, loadLatestRecord, refreshSummaries]);

  const resolveConflict = useCallback(async (resolution: 'reload' | 'overwrite') => {
    const conflict = conflictRef.current;
    if (!conflict) return;
    if (resolution === 'reload') {
      if (conflict.serverPayload) hydrate(conflict.serverPayload);
      conflictRef.current = null;
      await tradingDraftRecovery.clear();
      setStatus('saved');
      return;
    }
    if (!conflict.serverRecord) {
      setStatus('error');
      return;
    }
    await savePayload(conflict.serverRecord, conflict.localPayload);
  }, [hydrate, savePayload]);

  const activeWorkspaceName = workspaces.find((workspace) => workspace.workspaceId === activeWorkspaceId)?.name ?? activeName();

  return {
    status,
    workspaces,
    activeWorkspaceId,
    activeWorkspaceName,
    hasConflict: status === 'conflict',
    selectWorkspace,
    createWorkspace,
    importWorkspace,
    renameWorkspace,
    deleteWorkspace,
    resolveConflict, saveNow,
  };
}
