import type { QueryClient } from '@tanstack/react-query';

import { registerFetchMiddleware } from '../../../api/fetchPipeline';
import { emitOmnixEvent, LIVE_CALL_DIAGNOSTIC_EVENT, LIVE_CHAT_SESSION_CHANGED_EVENT } from '../../../events/bus';

type LiveCallDiagnosticDetail = {
  event?: unknown;
  details?: Record<string, unknown>;
};

const SESSION_PATH = /^\/api\/chat\/sessions\/([^/]+)(?:$|\/)/;
const SESSION_RECONCILIATION_EVENTS = new Set([
  'turn_finished',
  'turn_stopped',
  'turn_failed_final',
]);

let installed = false;
let selectedSessionId: string | null = null;
let workspaceQueryClient: QueryClient | null = null;

export function sessionIdFromChatRequest(input: RequestInfo | URL): string | null {
  const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const pathname = new URL(raw, window.location.origin).pathname;
  const match = SESSION_PATH.exec(pathname);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return match[1];
  }
}

/**
 * Tracks the chat session live requests use and reconciles Chat's queries
 * when a live response turn ends. The Live Chat view itself is a Chat view.
 */
export function initializeLiveChatWorkspace(queryClient: QueryClient): () => void {
  workspaceQueryClient = queryClient;
  if (installed) return () => undefined;
  installed = true;

  const removeMiddleware = registerFetchMiddleware('live-chat-workspace', async (input, init, next) => {
    const sessionId = sessionIdFromChatRequest(input);
    const response = await next(input, init);
    if (response.ok && sessionId && sessionId !== selectedSessionId) {
      selectedSessionId = sessionId;
      emitOmnixEvent(LIVE_CHAT_SESSION_CHANGED_EVENT, { sessionId });
    }
    return response;
  });

  const handleLiveCallDiagnostic = (event: Event) => {
    const detail = (event as CustomEvent<LiveCallDiagnosticDetail>).detail;
    const eventName = typeof detail?.event === 'string' ? detail.event : '';
    if (!SESSION_RECONCILIATION_EVENTS.has(eventName)) return;
    if (detail?.details?.turn_kind !== 'response') return;
    const sessionId = selectedSessionId;
    const client = workspaceQueryClient;
    if (!sessionId || !client) return;

    // Live voice deliberately avoids projecting a potentially large session
    // while first audio is on the critical path. Reconcile from persisted chat
    // state once the response turn is terminal so interrupted or otherwise
    // deferred turns cannot leave the visible chat one turn behind.
    void client.invalidateQueries({
      queryKey: ['feature', 'chatbot', 'session', sessionId],
      exact: true,
    });
    void client.invalidateQueries({
      queryKey: ['feature', 'chatbot', 'sessions'],
      exact: true,
    });
  };
  window.addEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleLiveCallDiagnostic);

  return () => {
    window.removeEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleLiveCallDiagnostic);
    removeMiddleware();
    selectedSessionId = null;
    workspaceQueryClient = null;
    installed = false;
  };
}
