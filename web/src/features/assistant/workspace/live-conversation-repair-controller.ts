 
 
import { registerFetchMiddleware } from '../../../api/fetchPipeline';
import {
  planConversationRepair,
  type LiveConversationRepairContext,
} from './live-conversation-repair';
import { liveVoiceTranscriptStore } from './live-voice-transcript-store';
import { ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_CONVERSATION_REPAIR_PLANNED_EVENT } from '../../../events/bus';

const CONTEXT_MESSAGE_PATH = /\/api\/assistant\/context\/chat\/sessions\/[^/]+\/messages(?:\/stream)?$/;

type OverlapPerfDetail = {
  stage?: unknown;
  intent?: unknown;
  reason?: unknown;
  confidence?: unknown;
  transcript?: unknown;
};

let pendingRepair: LiveConversationRepairContext | null = null;
let installed = false;

export function initializeLiveConversationRepairController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (installed) return () => undefined;
  installed = true;

  const removeMiddleware = registerFetchMiddleware('live-conversation-repair', (input, init, next) => {
    const injection = injectRepairIntoRequest(input, init, pendingRepair);
    if (injection.consumed) pendingRepair = null;
    return next(injection.input, injection.init);
  });

  const handlePerf = (event: Event) => {
    const detail = (event as CustomEvent<OverlapPerfDetail>).detail;
    if (detail?.stage !== 'overlap_classified') return;
    const intent = typeof detail.intent === 'string' ? detail.intent : null;
    if (intent === 'backchannel' || intent === 'noise' || intent === 'uncertain') return;
    const transcript = typeof detail.transcript === 'string' && detail.transcript.trim()
      ? detail.transcript.trim()
      : currentLiveTranscript();
    const repair = planConversationRepair({
      transcript,
      overlapIntent: intent,
      overlapReason: typeof detail.reason === 'string' ? detail.reason : null,
      confidence: typeof detail.confidence === 'number' ? detail.confidence : null,
      assistantWasInterrupted: true,
    });
    if (!repair) return;
    pendingRepair = repair;
    emitOmnixEvent(LIVE_CONVERSATION_REPAIR_PLANNED_EVENT, repair);
  };
  const clear = () => { pendingRepair = null; };

  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, clear);

  return () => {
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, clear);
    removeMiddleware();
    pendingRepair = null;
    installed = false;
  };
}

export function injectRepairIntoRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  repair: LiveConversationRepairContext | null,
): { input: RequestInfo | URL; init?: RequestInit; consumed: boolean } {
  if (!repair || !init?.body || typeof init.body !== 'string') return { input, init, consumed: false };
  const raw = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const pathname = new URL(raw, window.location.origin).pathname;
  if (!CONTEXT_MESSAGE_PATH.test(pathname) || (init.method ?? 'GET').toUpperCase() !== 'POST') {
    return { input, init, consumed: false };
  }
  try {
    const payload = JSON.parse(init.body) as Record<string, unknown>;
    if (typeof payload.content !== 'string' || !payload.content.trim()) return { input, init, consumed: false };
    return {
      input,
      init: { ...init, body: JSON.stringify({ ...payload, live_repair: repair }) },
      consumed: true,
    };
  } catch {
    return { input, init, consumed: false };
  }
}

export function currentLiveTranscript(root: ParentNode = document): string {
  const draft = root.querySelector<HTMLElement>('.assistant-live-draft p')?.textContent?.trim();
  if (draft && !draft.startsWith('Start Live Voice')) return draft;
  return liveVoiceTranscriptStore.draftText();
}
