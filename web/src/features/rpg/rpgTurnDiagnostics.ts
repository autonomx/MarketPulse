import { useEffect, useState } from 'react';

export interface RpgTurnClientMilestones {
  requestStartedMs: number;
  headersReceivedMs?: number;
  bodyReadMs?: number;
  jsonParsedMs?: number;
  storeUpdatedMs?: number;
  reactCommittedMs?: number;
  visibleMs?: number;
}

export interface RpgTurnDiagnosticsSnapshot {
  sessionId: string;
  submissionId: string;
  interactionId?: string;
  traceId?: string;
  responseBytes?: number;
  serverAttributionPercent?: number;
  serverTiming?: string;
  changedDomains: string[];
  client: RpgTurnClientMilestones & {
    requestToHeadersMs?: number;
    headersToBodyMs?: number;
    bodyToParseMs?: number;
    parseToStoreMs?: number;
    storeToCommitMs?: number;
    commitToVisibleMs?: number;
    requestToVisibleMs?: number;
  };
  serverPayloadTiming?: Record<string, number>;
  serverPerformance?: Record<string, unknown>;
}

interface CompleteDiagnosticsInput {
  sessionId: string;
  submissionId: string;
  interactionId?: string;
  traceId?: string | null;
  responseHeaders: Headers;
  milestones: RpgTurnClientMilestones;
  changedDomains?: string[];
  serverPayloadTiming?: Record<string, number>;
  serverPerformance?: Record<string, unknown>;
}

const diagnosticsBySession = new Map<string, RpgTurnDiagnosticsSnapshot[]>();
const listeners = new Set<() => void>();
const MAX_DIAGNOSTICS_PER_SESSION = 12;

export function nowRpgClientMs(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

export function beginRpgTurnDiagnostics(
  sessionId: string,
  submissionId: string,
  requestStartedMs: number,
): void {
  const current = diagnosticsBySession.get(sessionId) || [];
  const next = current.filter((item) => item.submissionId !== submissionId);
  next.push({
    sessionId,
    submissionId,
    changedDomains: [],
    client: { requestStartedMs },
  });
  setDiagnostics(sessionId, next);
}

export function completeRpgTurnDiagnostics(input: CompleteDiagnosticsInput): void {
  const current = diagnosticsBySession.get(input.sessionId) || [];
  const prior = current.find((item) => item.submissionId === input.submissionId);
  const milestones = {
    ...input.milestones,
    requestStartedMs: prior?.client.requestStartedMs ?? input.milestones.requestStartedMs,
  };
  const snapshot: RpgTurnDiagnosticsSnapshot = {
    sessionId: input.sessionId,
    submissionId: input.submissionId,
    interactionId: input.interactionId,
    traceId: input.traceId?.trim() || input.responseHeaders.get('X-Omnix-Rpg-Trace-Id')?.trim() || undefined,
    responseBytes: numberHeader(input.responseHeaders, 'X-Omnix-Rpg-Response-Bytes'),
    serverAttributionPercent: numberHeader(input.responseHeaders, 'X-Omnix-Rpg-Attribution-Pct'),
    serverTiming: input.responseHeaders.get('Server-Timing') || undefined,
    changedDomains: [...(input.changedDomains || [])],
    client: withDurations(milestones),
    serverPayloadTiming: input.serverPayloadTiming,
    serverPerformance: input.serverPerformance,
  };
  setDiagnostics(input.sessionId, [
    ...current.filter((item) => item.submissionId !== input.submissionId),
    snapshot,
  ]);
}

export function markRpgTurnReactCommitted(sessionId: string, interactionIds: string[]): void {
  markMilestone(sessionId, interactionIds, 'reactCommittedMs');
}

export function markRpgTurnVisible(sessionId: string, interactionIds: string[]): void {
  markMilestone(sessionId, interactionIds, 'visibleMs');
}

export function getRpgTurnDiagnostics(sessionId: string): RpgTurnDiagnosticsSnapshot[] {
  return [...(diagnosticsBySession.get(sessionId) || [])];
}

export function latestRpgTurnDiagnostics(sessionId: string): RpgTurnDiagnosticsSnapshot | undefined {
  return getRpgTurnDiagnostics(sessionId).at(-1);
}

export function useLatestRpgTurnDiagnostics(sessionId: string): RpgTurnDiagnosticsSnapshot | undefined {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((value) => value + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return latestRpgTurnDiagnostics(sessionId);
}

export function resetRpgTurnDiagnosticsForTests(): void {
  diagnosticsBySession.clear();
  listeners.clear();
}

export function rpgDiagnosticsEnabled(): boolean {
  if (typeof window === 'undefined') return false;
  const queryEnabled = new URLSearchParams(window.location.search).get('rpgDiagnostics') === '1';
  const localDevelopment = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
  let stored = false;
  try {
    stored = window.localStorage.getItem('omnix:rpg-diagnostics') === '1';
  } catch {
    stored = false;
  }
  return localDevelopment || queryEnabled || stored;
}

function markMilestone(
  sessionId: string,
  interactionIds: string[],
  key: 'reactCommittedMs' | 'visibleMs',
): void {
  const ids = new Set(interactionIds.filter(Boolean));
  if (!ids.size) return;
  const now = nowRpgClientMs();
  const current = diagnosticsBySession.get(sessionId) || [];
  let changed = false;
  const next = current.map((item) => {
    if (!item.interactionId || !ids.has(item.interactionId) || item.client[key] !== undefined) return item;
    changed = true;
    return { ...item, client: withDurations({ ...item.client, [key]: now }) };
  });
  if (changed) setDiagnostics(sessionId, next);
}

function withDurations(client: RpgTurnClientMilestones): RpgTurnDiagnosticsSnapshot['client'] {
  return {
    ...client,
    requestToHeadersMs: delta(client.requestStartedMs, client.headersReceivedMs),
    headersToBodyMs: delta(client.headersReceivedMs, client.bodyReadMs),
    bodyToParseMs: delta(client.bodyReadMs, client.jsonParsedMs),
    parseToStoreMs: delta(client.jsonParsedMs, client.storeUpdatedMs),
    storeToCommitMs: delta(client.storeUpdatedMs, client.reactCommittedMs),
    commitToVisibleMs: delta(client.reactCommittedMs, client.visibleMs),
    requestToVisibleMs: delta(client.requestStartedMs, client.visibleMs),
  };
}

function delta(start: number | undefined, finish: number | undefined): number | undefined {
  if (start === undefined || finish === undefined) return undefined;
  return Math.max(0, Number((finish - start).toFixed(3)));
}

function numberHeader(headers: Headers, name: string): number | undefined {
  const raw = headers.get(name);
  if (!raw) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
}

function setDiagnostics(sessionId: string, snapshots: RpgTurnDiagnosticsSnapshot[]): void {
  diagnosticsBySession.set(sessionId, snapshots.slice(-MAX_DIAGNOSTICS_PER_SESSION));
  for (const listener of listeners) listener();
}
