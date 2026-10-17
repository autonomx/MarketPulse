import type { LiveChatSubmissionInput } from './live-chat-submission-gateway';
import { initializeLiveSpeculationController } from './live-speculation-controller';
import { LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

let liveSpeculationRuntimeInstalled = false;

const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages\/stream$/;
const FINALIZED_SEGMENT_LIMIT = 64;

type SpeculationSegmentDetail = {
  segmentId?: string;
  sourceSequence?: number;
};

export function liveSubmissionRequestMatches(
  submission: LiveChatSubmissionInput,
  input: RequestInfo | URL,
  init?: RequestInit,
): boolean {
  const method = (
    init?.method
    ?? (input instanceof Request ? input.method : 'GET')
  ).toUpperCase();
  if (method !== 'POST') return false;
  const rawUrl = typeof input === 'string' || input instanceof URL
    ? input.toString()
    : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const match = CHAT_STREAM_PATH.exec(url.pathname);
  return Boolean(match && decodeURIComponent(match[1]) === submission.sessionId);
}

/**
 * Install speculation as the inner chat-stream transport.
 *
 * The unified live-audio controller is initialized later in main.tsx and wraps
 * this transport. That ordering is intentional: every normal or synthetic
 * speculative chat response must pass through unified audio before reaching the
 * application so its SSE text can be teed into the TTS phrase pipeline.
 *
 * A previous partial migration registered speculation as a scoped submission
 * interceptor while unified audio remained a window.fetch middleware. The
 * scoped interceptor then returned speculative/fallback responses before the
 * audio middleware could observe them, producing valid LLM text with zero TTS
 * phrases. Keep one composed chain until both middlewares are migrated together
 * to an explicit transport stack.
 */
export function initializeLiveSpeculationRuntime(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveSpeculationRuntimeInstalled) return () => undefined;
  liveSpeculationRuntimeInstalled = true;

  const finalizedSegments = new Map<string, true>();
  const segmentKey = (event: Event): string | null => {
    const detail = (event as CustomEvent<SpeculationSegmentDetail>).detail;
    if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return null;
    return `${detail.segmentId}:${detail.sourceSequence}`;
  };
  const rememberFinal = (event: Event): void => {
    const key = segmentKey(event);
    if (!key) return;
    finalizedSegments.delete(key);
    finalizedSegments.set(key, true);
    while (finalizedSegments.size > FINALIZED_SEGMENT_LIMIT) {
      const oldest = finalizedSegments.keys().next().value;
      if (typeof oldest !== 'string') break;
      finalizedSegments.delete(oldest);
    }
  };
  const suppressPostFinalUpdate = (event: Event): void => {
    const key = segmentKey(event);
    if (key && finalizedSegments.has(key)) event.stopImmediatePropagation();
  };

  // Kyutai can emit a delayed partial immediately after its authoritative final.
  // Capture the final before the speculation controller sees it, then suppress
  // only later partial/candidate events for that exact segment+sequence. This
  // prevents an accepted speculation from being cancelled by stale STT state.
  window.addEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, rememberFinal, true);
  window.addEventListener(
    LIVE_STT_SPECULATION_PARTIAL_EVENT,
    suppressPostFinalUpdate,
    true,
  );
  window.addEventListener(
    LIVE_STT_SPECULATION_CANDIDATE_EVENT,
    suppressPostFinalUpdate,
    true,
  );

  const cleanupController = initializeLiveSpeculationController();

  return () => {
    cleanupController();
    window.removeEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, rememberFinal, true);
    window.removeEventListener(
      LIVE_STT_SPECULATION_PARTIAL_EVENT,
      suppressPostFinalUpdate,
      true,
    );
    window.removeEventListener(
      LIVE_STT_SPECULATION_CANDIDATE_EVENT,
      suppressPostFinalUpdate,
      true,
    );
    finalizedSegments.clear();
    liveSpeculationRuntimeInstalled = false;
  };
}
