import { useEffect, useState } from 'react';
import { currentTradingWorkspaceScopeId } from '../persistence/useTradingWorkspacePersistence';
import { tradingApi } from '../tradingApi';
import type { TradingDocument } from '../tradingTypes';
import {
  addDrawing,
  cloneDrawings,
  deleteDrawing,
  groupDrawings,
  renameDrawing,
  renameDrawingGroup,
  reorderDrawing,
  moveDrawingTo,
  setDrawingsHidden,
  deleteDrawings,
  deleteAllDrawings,
  deleteSelectedDrawing,
  editDrawing,
  emptyDrawingState,
  moveDrawingPoint,
  pasteDrawings,
  redoDrawing,
  replaceDrawings,
  selectDrawing,
  selectedDrawingIds,
  toggleDrawingSelection,
  translateDrawing,
  undoDrawing,
  updateSelectedDrawing,
  type DrawingPoint,
  type DrawingProperties,
  type DrawingState,
  type DrawingStyle,
  type TradingDrawing,
} from './drawingCommands';
import type { DrawingEditPatch } from './tools/types';
import type { DrawingVisibility } from './drawingVisibility';
import { rememberToolDefaults } from './drawingTemplates';
import { drawingDocumentPayload, mergePreserved, upgradeDrawingDocument, type DrawingDocument } from './drawingDocument';

/** `read-only`: the stored document is from a newer schema; edits stay local and are never saved over it. */
export type DrawingPersistenceStatus = 'loading' | 'saved' | 'saving' | 'conflict' | 'error' | 'read-only';

type DrawingSnapshot = {
  state: DrawingState;
  status: DrawingPersistenceStatus;
  serverState: DrawingState | null;
  /** Stored entries this client can't show; kept and saved back unchanged. */
  preservedCount: number;
};

type DrawingEntry = DrawingSnapshot & {
  preserved: unknown[];
  readOnly: boolean;
  serverDocument: DrawingDocument | null;
  instrumentId: string;
  scopeId?: string;
  record: TradingDocument | null;
  timer: ReturnType<typeof setTimeout> | null;
  listeners: Set<() => void>;
  loadPromise: Promise<void> | null;
  loaded: boolean;
};

const entries = new Map<string, DrawingEntry>();

export function safeRecordPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '-');
}

/** Stable drawing document id. Workspace and tab scope are both part of the key. */
export function tradingDrawingRecordId(instrumentId: string, scopeId?: string): string {
  const scope = scopeId ? `${safeRecordPart(scopeId)}-` : '';
  return `${scope}instrument-${safeRecordPart(instrumentId)}`;
}

function entryFor(instrumentId: string, scopeId?: string): DrawingEntry {
  const entryKey = `${scopeId ?? 'global'}:${instrumentId}`;
  const existing = entries.get(entryKey);
  if (existing) return existing;
  const created: DrawingEntry = {
    instrumentId,
    scopeId,
    state: emptyDrawingState(),
    status: 'loading',
    serverState: null,
    preservedCount: 0,
    preserved: [],
    readOnly: false,
    serverDocument: null,
    record: null,
    timer: null,
    listeners: new Set(),
    loadPromise: null,
    loaded: false,
  };
  entries.set(entryKey, created);
  return created;
}

function snapshot(entry: DrawingEntry): DrawingSnapshot {
  return { state: entry.state, status: entry.status, serverState: entry.serverState, preservedCount: entry.preserved.length };
}

function emit(entry: DrawingEntry): void {
  entry.listeners.forEach((listener) => listener());
}

function documentFrom(record: TradingDocument | null, instrumentId: string): DrawingDocument {
  return upgradeDrawingDocument(record?.payload, instrumentId);
}

/** Adopts a stored document: its drawings, the entries kept verbatim, and whether it may be saved over. */
function adopt(entry: DrawingEntry, document: DrawingDocument): void {
  entry.state = replaceDrawings(document.drawings);
  entry.preserved = document.preserved;
  entry.readOnly = document.readOnly;
}

async function loadEntry(entry: DrawingEntry): Promise<void> {
  if (entry.loaded) return;
  if (entry.loadPromise) return entry.loadPromise;
  entry.loadPromise = (async () => {
    entry.status = 'loading';
    emit(entry);
    try {
      const records = await tradingApi.documents('drawings');
      const record = records.find((item) => item.record_id === tradingDrawingRecordId(entry.instrumentId, entry.scopeId)) ?? null;
      entry.record = record;
      adopt(entry, documentFrom(record, entry.instrumentId));
      entry.serverState = null;
      entry.status = entry.readOnly ? 'read-only' : 'saved';
      entry.loaded = true;
    } catch {
      entry.status = 'error';
    } finally {
      entry.loadPromise = null;
      emit(entry);
    }
  })();
  return entry.loadPromise;
}

function payload(entry: DrawingEntry): Record<string, unknown> {
  return drawingDocumentPayload(entry.instrumentId, entry.state.drawings, entry.preserved);
}

/**
 * Before the first save of a document whose load failed, fetch it: its
 * preserved entries must be saved back, and a newer schema must not be
 * saved over. Returns false when saving must not go ahead.
 */
async function ensureServerDocument(entry: DrawingEntry): Promise<boolean> {
  if (entry.loaded) return true;
  const records = await tradingApi.documents('drawings');
  const latest = records.find((item) => item.record_id === tradingDrawingRecordId(entry.instrumentId, entry.scopeId)) ?? null;
  if (latest) {
    const document = documentFrom(latest, entry.instrumentId);
    entry.record = latest;
    entry.preserved = mergePreserved(entry.preserved, document.preserved);
    entry.readOnly = document.readOnly;
  }
  entry.loaded = true;
  return !entry.readOnly;
}

async function saveEntry(entry: DrawingEntry): Promise<void> {
  try {
    if (!(await ensureServerDocument(entry))) {
      entry.status = 'read-only';
      emit(entry);
      return;
    }
    const saved = entry.record
      ? await tradingApi.updateDocument('drawings', entry.record, payload(entry))
      : await tradingApi.createDocument('drawings', tradingDrawingRecordId(entry.instrumentId, entry.scopeId), payload(entry));
    entry.record = saved;
    entry.serverState = null;
    entry.status = 'saved';
    entry.loaded = true;
  } catch (error) {
    const conflict = error instanceof Error && error.message.includes('(409)');
    if (conflict) {
      const records = await tradingApi.documents('drawings').catch(() => []);
      const latest = records.find((item) => item.record_id === tradingDrawingRecordId(entry.instrumentId, entry.scopeId)) ?? null;
      entry.record = latest ?? entry.record;
      entry.serverDocument = latest ? documentFrom(latest, entry.instrumentId) : null;
      entry.serverState = entry.serverDocument ? replaceDrawings(entry.serverDocument.drawings) : emptyDrawingState();
      entry.status = 'conflict';
    } else {
      entry.status = 'error';
    }
  }
  emit(entry);
}

function persist(entry: DrawingEntry, next: DrawingState): void {
  entry.state = next;
  if (entry.readOnly) {
    entry.status = 'read-only';
    emit(entry);
    return;
  }
  entry.status = 'saving';
  emit(entry);
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => {
    entry.timer = null;
    void saveEntry(entry);
  }, 450);
}

async function resolveConflict(entry: DrawingEntry, resolution: 'reload' | 'overwrite'): Promise<void> {
  if (entry.status !== 'conflict') return;
  if (resolution === 'reload') {
    if (entry.serverDocument) adopt(entry, entry.serverDocument);
    else entry.state = entry.serverState ?? emptyDrawingState();
    entry.serverDocument = null;
    entry.serverState = null;
    entry.status = entry.readOnly ? 'read-only' : 'saved';
    entry.loaded = true;
    emit(entry);
    return;
  }
  if (entry.serverDocument?.readOnly) {
    // Never overwrite a document written by a newer schema.
    entry.status = 'conflict';
    emit(entry);
    return;
  }
  // Overwriting keeps what the server's copy preserved as well as ours.
  if (entry.serverDocument) entry.preserved = mergePreserved(entry.preserved, entry.serverDocument.preserved);
  entry.status = 'saving';
  emit(entry);
  await saveEntry(entry);
}

/** A style or property change becomes the tool's default for its next drawings (TVP-3.8). */
function rememberStyle(state: DrawingState, patch: { style?: unknown; properties?: unknown }): DrawingState {
  if (patch.style === undefined && patch.properties === undefined) return state;
  const drawing = state.drawings.find((item) => item.drawingId === state.selectedId);
  if (drawing) rememberToolDefaults(drawing);
  return state;
}

export function useTradingDrawings(instrumentId: string, tabScopeId?: string) {
  const workspaceScopeId = currentTradingWorkspaceScopeId();
  const scopeId = `${workspaceScopeId}:${tabScopeId ?? 'global'}`;
  const entry = entryFor(instrumentId, scopeId);
  const [current, setCurrent] = useState<DrawingSnapshot>(() => snapshot(entry));

  useEffect(() => {
    const update = () => setCurrent(snapshot(entry));
    entry.listeners.add(update);
    update();
    void loadEntry(entry);
    return () => {
      entry.listeners.delete(update);
    };
  }, [entry]);

  return {
    state: current.state,
    status: current.status,
    preservedCount: current.preservedCount,
    hasConflict: current.status === 'conflict',
    add: (drawing: TradingDrawing) => persist(entry, addDrawing(entry.state, drawing)),
    select: (id: string | null) => {
      entry.state = selectDrawing(entry.state, id);
      emit(entry);
    },
    /** Ctrl+click: adds the drawing to the selection or removes it. */
    toggleSelect: (id: string) => {
      entry.state = toggleDrawingSelection(entry.state, id);
      emit(entry);
    },
    /** The selected drawings, primary last. */
    selected: () => {
      const ids = selectedDrawingIds(entry.state);
      return ids.map((id) => entry.state.drawings.find((drawing) => drawing.drawingId === id)).filter((drawing): drawing is TradingDrawing => Boolean(drawing));
    },
    /** Ctrl+drag: copies of the drawings moved by `from` -> `to`. */
    clone: (ids: readonly string[], from: DrawingPoint, to: DrawingPoint) => persist(entry, cloneDrawings(entry.state, ids, from, to)),
    paste: (copied: readonly TradingDrawing[]) => persist(entry, pasteDrawings(entry.state, copied, instrumentId)),
    movePoint: (id: string, index: number, point: DrawingPoint) => persist(entry, moveDrawingPoint(entry.state, id, index, point)),
    translate: (id: string, from: DrawingPoint, to: DrawingPoint) => persist(entry, translateDrawing(entry.state, id, from, to)),
    updateSelected: (patch: { style?: DrawingStyle; locked?: boolean; hidden?: boolean; text?: string; properties?: DrawingProperties; visibility?: DrawingVisibility }, mergeKey?: string) => (
      persist(entry, rememberStyle(updateSelectedDrawing(entry.state, patch, mergeKey), patch))
    ),
    /** A tool handle's edit of anchors and/or properties, one undo step. */
    edit: (id: string, patch: DrawingEditPatch) => persist(entry, editDrawing(entry.state, id, patch)),
    remove: (id: string) => persist(entry, deleteDrawing(entry.state, id)),
    /** Object tree (TVP-3.8): rename a drawing, group or ungroup drawings, rename a group, change the drawing order. */
    rename: (id: string, name: string) => persist(entry, renameDrawing(entry.state, id, name)),
    setGroup: (ids: readonly string[], group: string | null) => persist(entry, groupDrawings(entry.state, ids, group)),
    renameGroup: (from: string, to: string) => persist(entry, renameDrawingGroup(entry.state, from, to)),
    reorder: (id: string, move: 'up' | 'down' | 'front' | 'back') => persist(entry, reorderDrawing(entry.state, id, move)),
    moveTo: (id: string, targetId: string) => persist(entry, moveDrawingTo(entry.state, id, targetId)),
    setHidden: (ids: readonly string[], hidden: boolean) => persist(entry, setDrawingsHidden(entry.state, ids, hidden)),
    removeMany: (ids: readonly string[]) => persist(entry, deleteDrawings(entry.state, ids)),
    removeSelected: () => persist(entry, deleteSelectedDrawing(entry.state)),
    removeAll: () => persist(entry, deleteAllDrawings(entry.state)),
    undo: () => persist(entry, undoDrawing(entry.state)),
    redo: () => persist(entry, redoDrawing(entry.state)),
    resolveConflict: (resolution: 'reload' | 'overwrite') => resolveConflict(entry, resolution),
  };
}

/** Saves every drawing change still waiting on its debounce, e.g. before a layout is duplicated (TVP-2.5). */
export async function flushTradingDrawingSaves(): Promise<void> {
  const pending = [...entries.values()].filter((entry) => entry.timer !== null);
  await Promise.all(pending.map((entry) => {
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = null;
    return saveEntry(entry);
  }));
}
