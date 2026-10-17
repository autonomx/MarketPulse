import { flushTradingDrawingSaves, safeRecordPart, tradingDrawingRecordId } from '../drawings/useTradingDrawings';
import { tradingApi } from '../tradingApi';
import { MAX_TRADING_TABS, duplicatedChartId, newTradingTabId, useTradingStore, type TradingTabState } from '../tradingStore';
import type { TradingDocument } from '../tradingTypes';
import { currentTradingWorkspaceScopeId, type TradingWorkspacePersistence } from './useTradingWorkspacePersistence';

const INSTRUMENT_MARKER = 'instrument-';

/** The record-id prefix of a workspace tab's drawing documents (see `tradingDrawingRecordId`). */
function drawingScopePrefix(workspaceId: string, tabScopeId: string): string {
  const sample = tradingDrawingRecordId('', `${workspaceId}:${tabScopeId}`);
  return sample.slice(0, sample.length - INSTRUMENT_MARKER.length);
}

/**
 * The drawing documents to create so a duplicated workspace starts with the source's drawings.
 * Drawing documents are scoped by workspace and tab; the duplicate keeps the tab ids, so each
 * source record maps to the same record id under the new workspace.
 */
export function workspaceDrawingCopies(
  records: readonly TradingDocument[],
  sourceWorkspaceId: string,
  targetWorkspaceId: string,
  tabIds: readonly string[],
): Array<{ recordId: string; payload: Record<string, unknown> }> {
  const scopes = [...new Set([...tabIds, 'global'])];
  const copies: Array<{ recordId: string; payload: Record<string, unknown> }> = [];
  for (const record of records) {
    if (record.status !== 'active') continue;
    for (const scope of scopes) {
      const sourcePrefix = drawingScopePrefix(sourceWorkspaceId, scope);
      const rest = record.record_id.startsWith(sourcePrefix) ? record.record_id.slice(sourcePrefix.length) : '';
      // The tab's shared drawings, or a chart's own while drawings don't sync (`chart-<id>-instrument-...`, TVP-3.8).
      if (!rest.startsWith(INSTRUMENT_MARKER) && !/^chart-.+?-instrument-/.test(rest)) continue;
      const drawings = (record.payload as { drawings?: unknown }).drawings;
      if (!Array.isArray(drawings) || drawings.length === 0) break;
      const recordId = `${drawingScopePrefix(targetWorkspaceId, scope)}${record.record_id.slice(sourcePrefix.length)}`;
      if (recordId.length <= 200) copies.push({ recordId, payload: { ...record.payload } });
      break;
    }
  }
  return copies;
}

/**
 * The drawing documents to create so a duplicated tab (TVP-4.4) starts with the source tab's drawings: the tab's
 * shared ones, and each chart's own (`chart-<id>-...`) under the copy's chart id.
 */
export function tabDrawingCopies(
  records: readonly TradingDocument[],
  workspaceId: string,
  sourceTabId: string,
  targetTabId: string,
  chartIds: ReadonlyMap<string, string>,
): Array<{ recordId: string; payload: Record<string, unknown> }> {
  const sourcePrefix = drawingScopePrefix(workspaceId, sourceTabId);
  const targetPrefix = drawingScopePrefix(workspaceId, targetTabId);
  const copies: Array<{ recordId: string; payload: Record<string, unknown> }> = [];
  for (const record of records) {
    if (record.status !== 'active' || !record.record_id.startsWith(sourcePrefix)) continue;
    const drawings = (record.payload as { drawings?: unknown }).drawings;
    if (!Array.isArray(drawings) || drawings.length === 0) continue;
    const rest = record.record_id.slice(sourcePrefix.length);
    let mapped: string | null = rest.startsWith(INSTRUMENT_MARKER) ? rest : null;
    for (const [from, to] of chartIds) {
      const chartPart = `chart-${safeRecordPart(from)}-`;
      if (!mapped && rest.startsWith(`${chartPart}${INSTRUMENT_MARKER}`)) mapped = `chart-${safeRecordPart(to)}-${rest.slice(chartPart.length)}`;
    }
    const recordId = mapped ? `${targetPrefix}${mapped}` : null;
    if (recordId && recordId.length <= 200) copies.push({ recordId, payload: { ...record.payload } });
  }
  return copies;
}

/**
 * Duplicates a tab with its drawings (TVP-4.4): the drawings are copied to the copy's id first, so its charts load
 * them when they mount. A failed drawing copy still duplicates the tab (without them). Resolves to the new tab's id.
 */
let duplicating = false;

export async function duplicateTradingTab(tabId: string): Promise<string | null> {
  const state = useTradingStore.getState();
  const source = state.tabs.find((tab) => tab.tabId === tabId);
  // One at a time (a double click makes one copy), and no drawing copies for a tab that can't be added.
  if (!source || duplicating || state.tabs.length >= MAX_TRADING_TABS) return null;
  duplicating = true;
  try {
    return await duplicateWithDrawings(tabId, source, state);
  } finally {
    duplicating = false;
  }
}

async function duplicateWithDrawings(tabId: string, source: TradingTabState, state: ReturnType<typeof useTradingStore.getState>): Promise<string | null> {
  const targetId = newTradingTabId();
  const charts = tabId === state.activeTabId ? state.charts : source.charts;
  const chartIds = new Map(charts.map((chart) => [chart.chartId, duplicatedChartId(targetId, chart.chartId)]));
  try {
    await flushTradingDrawingSaves();
    const records = await tradingApi.allDocuments('drawings');
    const existing = new Set(records.map((record) => record.record_id));
    const copies = tabDrawingCopies(records, currentTradingWorkspaceScopeId(), tabId, targetId, chartIds).filter((copy) => !existing.has(copy.recordId));
    await Promise.all(copies.map((copy) => tradingApi.createDocument('drawings', copy.recordId, copy.payload)));
  } catch {
    // The tab is still duplicated; its drawings stay on the original.
  }
  return useTradingStore.getState().duplicateTab(tabId, targetId);
}

/** Copies a workspace's drawing documents to a duplicate of it. */
export async function copyWorkspaceDrawings(
  sourceWorkspaceId: string,
  targetWorkspaceId: string,
  tabIds: readonly string[],
): Promise<number> {
  // Pending debounced edits belong in the copy; then read every drawing document, not one page.
  await flushTradingDrawingSaves();
  const records = await tradingApi.allDocuments('drawings');
  const existing = new Set(records.map((record) => record.record_id));
  const copies = workspaceDrawingCopies(records, sourceWorkspaceId, targetWorkspaceId, tabIds)
    .filter((copy) => !existing.has(copy.recordId));
  await Promise.all(copies.map((copy) => tradingApi.createDocument('drawings', copy.recordId, copy.payload)));
  return copies.length;
}

/**
 * Duplicates the active layout (TVP-2.5): a new workspace from the current one, with its tabs
 * and charts (`createWorkspace` copies the live state), and its drawing documents, copied before
 * the duplicate becomes active so its charts load them. Resolves to the number of drawing
 * documents copied; rejects when the drawings could not be copied.
 */
export async function duplicateTradingWorkspace(
  persistence: Pick<TradingWorkspacePersistence, 'createWorkspace'>,
  name: string,
): Promise<number> {
  const sourceId = currentTradingWorkspaceScopeId();
  const tabIds = useTradingStore.getState().tabs.map((tab) => tab.tabId);
  let copied = 0;
  let failure: unknown = null;
  await persistence.createWorkspace(name, async (targetId) => {
    try {
      copied = await copyWorkspaceDrawings(sourceId, targetId, tabIds);
    } catch (error) {
      failure = error ?? new Error('Drawing copy failed');
    }
  });
  if (failure) throw failure;
  return copied;
}
