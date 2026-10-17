 
import { registerFetchMiddleware } from '../../api/fetchPipeline';
import { useEffect, useState } from 'react';
import type { RpgStoryMessagePreview } from './rpgUiState';
import {
  beginRpgTurnDiagnostics,
  completeRpgTurnDiagnostics,
  nowRpgClientMs,
  resetRpgTurnDiagnosticsForTests,
  type RpgTurnClientMilestones,
} from './rpgTurnDiagnostics';

export type RpgTurnUiStatus = 'optimistic' | 'pending' | 'complete' | 'failed';

export interface RpgTurnUiEntry extends RpgStoryMessagePreview {
  id: string;
  sessionId: string;
  submissionId: string;
  interactionId?: string;
  status: RpgTurnUiStatus;
  messageKind?: string;
  messageIndex?: number;
}

interface RpgVisibleMessageV2 {
  kind?: string;
  speaker_id?: string;
  speaker?: string;
  text?: string;
}

interface RpgVisibleResponseV2 {
  narration?: string;
  messages?: RpgVisibleMessageV2[];
  plain_text?: string;
}

interface RpgTurnResponseV2 {
  ok?: boolean;
  contract_version?: string;
  session_id?: string;
  submission_id?: string;
  interaction_id?: string;
  turn_id?: string;
  trace_id?: string;
  visible_response?: RpgVisibleResponseV2;
  response?: string;
  content?: string;
  state?: {
    changed?: boolean;
    changed_domains?: string[];
  };
  timing?: Record<string, number>;
  performance?: Record<string, unknown>;
}

interface BeginSubmissionInput {
  sessionId: string;
  submissionId: string;
  command: string;
}

interface CompleteSubmissionInput {
  sessionId: string;
  submissionId: string;
  payload: RpgTurnResponseV2;
  responseHeaders?: Headers;
  milestones?: RpgTurnClientMilestones;
}

interface CachedResponse {
  body: string;
  headers: [string, string][];
  status: number;
  statusText: string;
}

const entriesBySession = new Map<string, RpgTurnUiEntry[]>();
const sessionResponseCache = new Map<string, CachedResponse>();
const listeners = new Set<() => void>();
const MAX_ENTRIES_PER_SESSION = 24;
const MAX_SESSION_CACHE_ENTRIES = 12;
let removeTurnMiddleware: (() => void) | null = null;

export function createRpgSubmissionId(): string {
  const cryptoValue = globalThis.crypto;
  if (cryptoValue?.randomUUID) {
    return `submit:${cryptoValue.randomUUID().replace(/-/g, '')}`;
  }
  return `submit:${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

export function beginRpgTurnUiSubmission(input: BeginSubmissionInput): void {
  const current = getRpgTurnUiEntries(input.sessionId).filter(
    (entry) => entry.submissionId !== input.submissionId,
  );
  const playerEntry: RpgTurnUiEntry = {
    id: `${input.submissionId}:player`,
    sessionId: input.sessionId,
    submissionId: input.submissionId,
    status: 'optimistic',
    avatar: 'Y',
    speaker: 'You',
    text: input.command,
    tone: 'player',
    messageKind: 'player',
    messageIndex: 0,
  };
  const pendingEntry: RpgTurnUiEntry = {
    id: `${input.submissionId}:pending`,
    sessionId: input.sessionId,
    submissionId: input.submissionId,
    status: 'pending',
    avatar: 'O',
    speaker: 'Omnix',
    text: 'Considering the scene…',
    tone: 'narrator',
    messageKind: 'pending',
    messageIndex: 0,
  };
  setSessionEntries(input.sessionId, [...current, playerEntry, pendingEntry]);
}

export function completeRpgTurnUiSubmission(input: CompleteSubmissionInput): string[] {
  const current = getRpgTurnUiEntries(input.sessionId);
  const interactionId = input.payload.interaction_id?.trim() || input.submissionId;
  const playerEntry = current.find(
    (entry) => entry.submissionId === input.submissionId && entry.tone === 'player',
  );
  const remaining = current.filter((entry) => entry.submissionId !== input.submissionId);
  const completedPlayer = playerEntry
    ? {
        ...playerEntry,
        id: `${interactionId}:player`,
        interactionId,
        status: 'complete' as const,
      }
    : undefined;
  const responseEntries = visibleEntriesFromPayload(
    input.sessionId,
    input.submissionId,
    interactionId,
    input.payload,
  );
  setSessionEntries(
    input.sessionId,
    [...remaining, ...(completedPlayer ? [completedPlayer] : []), ...responseEntries],
  );

  if (input.milestones && input.responseHeaders) {
    completeRpgTurnDiagnostics({
      sessionId: input.sessionId,
      submissionId: input.submissionId,
      interactionId,
      traceId: input.payload.trace_id,
      responseHeaders: input.responseHeaders,
      milestones: { ...input.milestones, storeUpdatedMs: nowRpgClientMs() },
      changedDomains: input.payload.state?.changed_domains || [],
      serverPayloadTiming: input.payload.timing,
      serverPerformance: input.payload.performance,
    });
  }
  return input.payload.state?.changed_domains || [];
}

export function failRpgTurnUiSubmission(
  sessionId: string,
  submissionId: string,
  errorMessage: string,
): void {
  const current = getRpgTurnUiEntries(sessionId);
  const next = current.map((entry) => {
    if (entry.submissionId !== submissionId) return entry;
    if (entry.tone === 'player') return { ...entry, status: 'failed' as const };
    return {
      ...entry,
      status: 'failed' as const,
      text: errorMessage || 'The turn could not be completed.',
    };
  });
  setSessionEntries(sessionId, next);
}

export function discardRpgTurnUiSubmission(sessionId: string, submissionId: string): void {
  setSessionEntries(
    sessionId,
    getRpgTurnUiEntries(sessionId).filter((entry) => entry.submissionId !== submissionId),
  );
}

export function getRpgTurnUiEntries(sessionId: string): RpgTurnUiEntry[] {
  return [...(entriesBySession.get(sessionId) || [])];
}

export function useRpgTurnUiEntries(sessionId: string): RpgTurnUiEntry[] {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const listener = () => setVersion((value) => value + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return getRpgTurnUiEntries(sessionId);
}

export function mergeRpgTurnUiMessages(
  baseMessages: RpgStoryMessagePreview[],
  entries: RpgTurnUiEntry[],
): RpgStoryMessagePreview[] {
  if (!entries.length) {
    return baseMessages.map((message, index) => withBaseIdentity(message, index));
  }

  const authoritativeInteractionIds = new Set(
    entries
      .filter((entry) => entry.status === 'complete' && entry.interactionId)
      .map((entry) => entry.interactionId as string),
  );
  const completeTurnCount = authoritativeInteractionIds.size;
  const identifiedBase = baseMessages.map((message, index) => withBaseIdentity(message, index));
  const identityFiltered = identifiedBase.filter(
    (message) => !message.interactionId || !authoritativeInteractionIds.has(message.interactionId),
  );
  const baseWithoutReplacedTail = removeLatestBaseTurns(identityFiltered, completeTurnCount);
  const seenIds = new Set(baseWithoutReplacedTail.map((message, index) => storyMessageIdentity(message, index)));
  const merged = [...baseWithoutReplacedTail];
  for (const entry of entries) {
    if (seenIds.has(entry.id)) continue;
    seenIds.add(entry.id);
    merged.push(entry);
  }
  return merged.slice(-40);
}

export function useRpgTurnUiMessages(
  sessionId: string,
  baseMessages: RpgStoryMessagePreview[],
): RpgStoryMessagePreview[] {
  return mergeRpgTurnUiMessages(baseMessages, useRpgTurnUiEntries(sessionId));
}

export function storyMessageIdentity(message: RpgStoryMessagePreview, index: number): string {
  return message.id?.trim()
    || [message.interactionId, message.messageKind, message.messageIndex].filter((value) => value !== undefined && value !== '').join(':')
    || `base:${index}`;
}

export function refreshPathsForChangedDomains(sessionId: string, changedDomains: string[]): string[] {
  const domains = new Set(changedDomains);
  const paths = new Set<string>([`/api/rpg/sessions/${encodeURIComponent(sessionId)}`]);
  if ([...domains].some((domain) => ['location', 'world', 'quests', 'journal'].includes(domain))) {
    paths.add('/api/reports');
  }
  return [...paths];
}

/** Returns a function that removes it. `fetchImpl` replaces the rest of the pipeline (tests). */
export function installRpgTurnUiFetchInterceptor(fetchImpl?: typeof fetch): () => void {
  if (removeTurnMiddleware || typeof globalThis.fetch !== 'function') return () => undefined;

  const remove = registerFetchMiddleware('rpg-turn-ui', async (input, init, next) => {
    const originalFetch = fetchImpl ?? next;
    const requestUrl = new URL(requestUrlString(input), globalThis.location?.origin || 'http://localhost');
    const turnMatch = requestUrl.pathname.match(/^\/api\/rpg\/sessions\/([^/]+)\/turn$/);
    const sessionMatch = requestUrl.pathname.match(/^\/api\/rpg\/sessions\/([^/]+)$/);
    const method = String(init?.method || (isRequest(input) ? input.method : 'GET')).toUpperCase();

    if (turnMatch && method === 'POST') {
      const sessionId = decodeURIComponent(turnMatch[1]);
      const submissionId = createRpgSubmissionId();
      const requestStartedMs = nowRpgClientMs();
      const command = commandFromBody(init?.body);
      const headers = new Headers(isRequest(input) ? input.headers : undefined);
      new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
      headers.set('X-Omnix-Rpg-Submission-Id', submissionId);
      headers.set('X-Omnix-Rpg-Client-Started', new Date().toISOString());
      beginRpgTurnDiagnostics(sessionId, submissionId, requestStartedMs);
      beginRpgTurnUiSubmission({ sessionId, submissionId, command });
      try {
        const response = await originalFetch(input, { ...init, headers });
        const headersReceivedMs = nowRpgClientMs();
        if (!response.ok) {
          if (response.status === 404 || response.status === 405) {
            discardRpgTurnUiSubmission(sessionId, submissionId);
          } else {
            failRpgTurnUiSubmission(sessionId, submissionId, `Turn failed (${response.status}).`);
          }
          return response;
        }
        const parsed = await parseTurnResponse(response.clone(), {
          requestStartedMs,
          headersReceivedMs,
        });
        if (parsed.payload.contract_version !== 'rpg_turn_response_v2') {
          discardRpgTurnUiSubmission(sessionId, submissionId);
          return response;
        }
        const changedDomains = completeRpgTurnUiSubmission({
          sessionId,
          submissionId,
          payload: parsed.payload,
          responseHeaders: response.headers,
          milestones: parsed.milestones,
        });
        for (const path of refreshPathsForChangedDomains(sessionId, changedDomains)) {
          sessionResponseCache.delete(path);
        }
        return response;
      } catch (error) {
        failRpgTurnUiSubmission(
          sessionId,
          submissionId,
          error instanceof Error ? error.message : 'The turn request failed.',
        );
        throw error;
      }
    }

    if (sessionMatch && method === 'GET') {
      const cached = sessionResponseCache.get(requestUrl.pathname);
      if (cached) return restoreResponse(cached);
      const response = await originalFetch(input, init);
      if (response.ok) void cacheSessionResponse(requestUrl.pathname, response.clone());
      return response;
    }

    return originalFetch(input, init);
  });
  removeTurnMiddleware = remove;
  return () => {
    remove();
    if (removeTurnMiddleware === remove) removeTurnMiddleware = null;
  };
}

export function resetRpgTurnUiStoreForTests(): void {
  entriesBySession.clear();
  sessionResponseCache.clear();
  listeners.clear();
  resetRpgTurnDiagnosticsForTests();
  removeTurnMiddleware?.();
  removeTurnMiddleware = null;
}

function visibleEntriesFromPayload(
  sessionId: string,
  submissionId: string,
  interactionId: string,
  payload: RpgTurnResponseV2,
): RpgTurnUiEntry[] {
  const visible = payload.visible_response || {};
  const entries: RpgTurnUiEntry[] = [];
  const narration = visible.narration?.trim();
  if (narration) {
    entries.push({
      id: `${interactionId}:narration`,
      sessionId,
      submissionId,
      interactionId,
      status: 'complete',
      avatar: 'O',
      speaker: 'Omnix (Narrator)',
      text: narration,
      tone: 'narrator',
      messageKind: 'narration',
      messageIndex: 0,
    });
  }
  for (const [index, message] of (visible.messages || []).entries()) {
    const text = message.text?.trim();
    if (!text) continue;
    const speaker = message.speaker?.trim() || 'NPC';
    entries.push({
      id: `${interactionId}:message:${index}`,
      sessionId,
      submissionId,
      interactionId,
      status: 'complete',
      avatar: speaker.charAt(0).toUpperCase() || 'N',
      speaker,
      text,
      tone: message.kind === 'narration' ? 'narrator' : 'npc',
      messageKind: message.kind || 'npc_dialogue',
      messageIndex: index,
    });
  }
  if (!entries.length) {
    const fallback = visible.plain_text?.trim() || payload.response?.trim() || payload.content?.trim();
    if (fallback) {
      entries.push({
        id: `${interactionId}:response`,
        sessionId,
        submissionId,
        interactionId,
        status: 'complete',
        avatar: 'O',
        speaker: 'Omnix',
        text: fallback,
        tone: 'narrator',
        messageKind: 'response',
        messageIndex: 0,
      });
    }
  }
  return entries;
}

function withBaseIdentity(message: RpgStoryMessagePreview, index: number): RpgStoryMessagePreview {
  return message.id ? message : { ...message, id: `base:${index}` };
}

function removeLatestBaseTurns(
  messages: RpgStoryMessagePreview[],
  completedTurnCount: number,
): RpgStoryMessagePreview[] {
  if (completedTurnCount <= 0) return messages;
  const groups: RpgStoryMessagePreview[][] = [];
  for (const message of messages) {
    if (message.tone === 'player' || !groups.length) groups.push([message]);
    else groups[groups.length - 1].push(message);
  }
  return groups.slice(0, Math.max(0, groups.length - completedTurnCount)).flat();
}

function commandFromBody(body: BodyInit | null | undefined): string {
  if (typeof body !== 'string') return 'Continue the adventure.';
  try {
    const payload = JSON.parse(body) as Record<string, unknown>;
    for (const key of ['command', 'player_input', 'text', 'message']) {
      const value = payload[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
  } catch {
    // Non-JSON bodies are passed through without affecting the request.
  }
  return 'Continue the adventure.';
}

async function parseTurnResponse(
  response: Response,
  milestones: RpgTurnClientMilestones,
): Promise<{ payload: RpgTurnResponseV2; milestones: RpgTurnClientMilestones }> {
  try {
    const body = await response.text();
    const bodyReadMs = nowRpgClientMs();
    const payload = JSON.parse(body) as RpgTurnResponseV2;
    const jsonParsedMs = nowRpgClientMs();
    return { payload, milestones: { ...milestones, bodyReadMs, jsonParsedMs } };
  } catch {
    return { payload: {}, milestones: { ...milestones, bodyReadMs: nowRpgClientMs(), jsonParsedMs: nowRpgClientMs() } };
  }
}

function requestUrlString(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function isRequest(input: RequestInfo | URL): input is Request {
  return typeof Request !== 'undefined' && input instanceof Request;
}

async function cacheSessionResponse(pathname: string, response: Response): Promise<void> {
  try {
    sessionResponseCache.set(pathname, {
      body: await response.text(),
      headers: [...response.headers.entries()],
      status: response.status,
      statusText: response.statusText,
    });
    while (sessionResponseCache.size > MAX_SESSION_CACHE_ENTRIES) {
      const oldest = sessionResponseCache.keys().next().value as string | undefined;
      if (!oldest) break;
      sessionResponseCache.delete(oldest);
    }
  } catch {
    // Cache misses fall through to the real refresh and never affect gameplay.
  }
}

function restoreResponse(cached: CachedResponse): Response {
  return new Response(cached.body, {
    status: cached.status,
    statusText: cached.statusText,
    headers: cached.headers,
  });
}

function setSessionEntries(sessionId: string, entries: RpgTurnUiEntry[]): void {
  entriesBySession.set(sessionId, entries.slice(-MAX_ENTRIES_PER_SESSION));
  for (const listener of listeners) listener();
}
