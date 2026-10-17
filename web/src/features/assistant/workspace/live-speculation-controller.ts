 
import { fetchBelow, registerFetchMiddleware, type FetchNext } from '../../../api/fetchPipeline';
import { createLiveSpeechSynthesisOptions } from './live-speech-synthesis-options';
import { StableClauseAccumulator } from './live-voice-clause-stabilizer';
import { speculationEventSchema } from './live-voice-messages';
import { parseJson } from '../../../api/schemas/streams';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

const CHAT_STREAM_PATH = /^\/api\/chat\/sessions\/([^/]+)\/messages\/stream$/;
const MIDDLEWARE = 'live-speculation';
const VOICE_SETTINGS_KEY = 'omnix.chatbot.assistantSettings';
const CORRECTION_PATTERN = /(?:^|\s)(?:uh+|um+|erm+|wait|sorry|actually|correction|no[,. ]+i mean)(?:\s|$)/i;
const WORD_PATTERN = /[\p{L}\p{N}_]+(?:['’][\p{L}\p{N}_]+)?/gu;
const SHORT_COMPLETE_PATTERN = /^(?:yes|no|why|how|when|where|who|what|stop|continue|repeat|explain|start|cancel|hello|hi|hey|thanks)[?!.…]*$/i;
const SINGLE_WORD_SPECULATION_MIN_PROBABILITY = 0.88;
const MAX_ACTIVE_HYPOTHESES = 2;
const MIN_EXTENSION_WORDS = 2;
const SPECULATIVE_TTS_ACCEPT_WAIT_MS = 80;
const TRANSCRIPT_CORRECTION_STABILITY_MS = 24;
export const LIVE_SPECULATION_HANDSHAKE_GRACE_MS = 50;

export type SpeculationHandshakeWaitState = {
  generationReady: boolean;
  bufferedChunks: number;
  responseReady: boolean;
  completed: boolean;
  error: boolean;
};

export function speculationHandshakeWaitBudgetMs(
  state: SpeculationHandshakeWaitState,
): number {
  if (state.generationReady || state.bufferedChunks > 0 || state.completed || state.error) {
    return 0;
  }
  return state.responseReady ? LIVE_SPECULATION_HANDSHAKE_GRACE_MS : 0;
}

type SttPartialDetail = {
  chatSessionId?: string;
  segmentId?: string;
  sourceSequence?: number;
  text?: string;
};

type SttCandidateDetail = SttPartialDetail & {
  probability?: number;
  modelTimeMs?: number;
};

export type SpeculationEvent = {
  type?: string;
  generation_id?: string;
  text?: string;
  content?: string;
  message?: string;
  provider_id?: string | null;
  model_id?: string | null;
};

type AcceptedSpeculation = {
  ok: boolean;
  generation_id: string;
  content: string;
  user_message: Record<string, unknown>;
  session: Record<string, unknown>;
};

type ChatStreamRequestBody = Record<string, unknown> & {
  content?: string;
  provider_id?: string;
  model_id?: string;
  agent_mode?: boolean;
  dry_run?: boolean;
  research_mode?: string | null;
  user_turn_id?: string;
  speech_segment_id?: string;
  live_voice_turn_id?: string;
};

type ActiveSpeculation = {
  sessionId: string;
  segmentId: string;
  sourceSequence: number;
  candidateText: string;
  endpointProbability: number | undefined;
  createdAtMs: number;
  providerId: string | null;
  modelId: string | null;
  generationId: string | null;
  chunks: string[];
  subscribers: Set<() => void>;
  abortController: AbortController;
  startedPromise: Promise<void>;
  resolveStarted: () => void;
  generationPromise: Promise<void>;
  resolveGeneration: () => void;
  finalText: string | null;
  completed: boolean;
  error: string | null;
  handshakeResponseReady: boolean;
  reused: boolean;
  acceptBody: ChatStreamRequestBody | null;
  firstClause: StableClauseAccumulator;
  firstClauseTimer: ReturnType<typeof window.setTimeout> | null;
  prefetchedClause: string | null;
  prefetchStarted: boolean;
  prefetchPromise: Promise<void> | null;
  prefetchAcceptPromise: Promise<void> | null;
};

let removeMiddleware: (() => void) | null = null;
const ownFetch = fetchBelow(MIDDLEWARE);
let activeSpeculations: ActiveSpeculation[] = [];
const partials = new Map<string, string>();
const correctionTimers = new Map<string, ReturnType<typeof window.setTimeout>>();

export function normalizeSpeculationWords(text: string): string[] {
  return [...text.matchAll(WORD_PATTERN)]
    .map((match) => match[0].toLowerCase())
    .map((token) => token.replaceAll('’', "'"));
}

export function transcriptIsSpeculationSafe(text: string): boolean {
  if (CORRECTION_PATTERN.test(text)) return false;
  const words = normalizeSpeculationWords(text);
  return words.length >= 2
    || (words.length === 1 && SHORT_COMPLETE_PATTERN.test(text.trim()));
}

export function speculationCandidateCanStart(
  text: string,
  endpointProbability = 0,
): boolean {
  if (!transcriptIsSpeculationSafe(text)) return false;
  const words = normalizeSpeculationWords(text);
  return words.length >= 2
    || endpointProbability >= SINGLE_WORD_SPECULATION_MIN_PROBABILITY;
}

export function transcriptsCanReuseSpeculation(candidate: string, final: string): boolean {
  if (!transcriptIsSpeculationSafe(candidate)) return false;
  const left = normalizeSpeculationWords(candidate);
  const right = normalizeSpeculationWords(final);
  return left.length === right.length && left.every((word, index) => word === right[index]);
}

export function transcriptExtendsSpeculation(candidate: string, next: string): boolean {
  const left = normalizeSpeculationWords(candidate);
  const right = normalizeSpeculationWords(next);
  return left.length <= right.length && left.every((word, index) => word === right[index]);
}

export function transcriptCorrectionNeedsStability(candidate: string, next: string): boolean {
  return normalizeComparable(candidate) !== normalizeComparable(next)
    && !transcriptExtendsSpeculation(candidate, next);
}

export function shouldStartExtendedHypothesis(
  previousCandidate: string,
  nextCandidate: string,
  endpointProbability = 0,
): boolean {
  if (!speculationCandidateCanStart(nextCandidate, endpointProbability)) return false;
  if (!transcriptExtendsSpeculation(previousCandidate, nextCandidate)) return true;
  const previousWords = normalizeSpeculationWords(previousCandidate).length;
  const nextWords = normalizeSpeculationWords(nextCandidate).length;
  return nextWords - previousWords >= MIN_EXTENSION_WORDS || endpointProbability >= 0.85;
}

export function speculationRequestCanReuse(
  body: ChatStreamRequestBody | null,
  speculativeProviderId: string | null,
  speculativeModelId: string | null,
): boolean {
  if (!body) return false;
  if (body.agent_mode === true || body.dry_run === true || body.research_mode) {
    return false;
  }
  const providerId = typeof body.provider_id === 'string' ? body.provider_id : null;
  const modelId = typeof body.model_id === 'string' ? body.model_id : null;
  if (providerId && providerId !== speculativeProviderId) return false;
  if (modelId && modelId !== speculativeModelId) return false;
  return true;
}

export function speculativeTtsPrefetchWindowOpen(
  _finalText: string | null,
  isNewestHypothesis: boolean,
): boolean {
  // An exact authoritative final makes the retained hypothesis safer, not less
  // useful. Keep its private TTS window open through the chat handoff so the
  // first arriving LLM chunk can still prefetch before normal clause delivery.
  return isNewestHypothesis;
}

export function speculativeTtsPrefetchEnabled(value: string | undefined): boolean {
  return value?.trim().toLowerCase() !== 'false';
}

export function initializeLiveSpeculationController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (removeMiddleware) return () => undefined;
  removeMiddleware = registerFetchMiddleware(MIDDLEWARE, interceptChatStream);

  const handlePartial = (event: Event): void => {
    const detail = (event as CustomEvent<SttPartialDetail>).detail;
    const key = partialKey(detail);
    const text = detail?.text?.trim();
    if (!key || !text || !detail?.chatSessionId || !detail.segmentId
      || typeof detail.sourceSequence !== 'number') return;
    partials.set(key, text);

    const matching = segmentSpeculations(detail.segmentId, detail.sourceSequence);
    const corrected = matching.some((active) => (
      transcriptCorrectionNeedsStability(active.candidateText, text)
    ));
    clearCorrectionTimer(key);
    if (corrected) {
      // Streaming ASR can briefly revise away from the eventual authoritative
      // final while its right-context tail flushes. Keep the completed prior
      // hypothesis (and its private TTS cache) eligible for one bounded window.
      // A persistent correction still receives its own replacement hypothesis.
      const latest = newestSegmentSpeculation(detail.segmentId, detail.sourceSequence);
      const timer = window.setTimeout(() => {
        correctionTimers.delete(key);
        if (partials.get(key)?.trim() !== text) return;
        startHypothesis(
          detail.chatSessionId as string,
          detail.segmentId as string,
          detail.sourceSequence as number,
          text,
          latest?.endpointProbability,
          'stable_partial_correction',
        );
      }, TRANSCRIPT_CORRECTION_STABILITY_MS);
      correctionTimers.set(key, timer);
      return;
    }

    const latest = newestSegmentSpeculation(detail.segmentId, detail.sourceSequence);
    if (latest && shouldStartExtendedHypothesis(
      latest.candidateText,
      text,
      latest.endpointProbability ?? 0,
    )) {
      startHypothesis(
        detail.chatSessionId,
        detail.segmentId,
        detail.sourceSequence,
        text,
        latest.endpointProbability,
        'partial_extension',
      );
    }
  };

  const handleCandidate = (event: Event): void => {
    if (!speculationEnabled()) return;
    const detail = (event as CustomEvent<SttCandidateDetail>).detail;
    const key = partialKey(detail);
    if (!key || !detail.chatSessionId || !detail.segmentId
      || typeof detail.sourceSequence !== 'number') return;
    const candidateText = partials.get(key)?.trim() || detail.text?.trim() || '';
    if (!speculationCandidateCanStart(candidateText, detail.probability ?? 0)) return;
    if (correctionTimers.has(key)) return;
    const latest = newestSegmentSpeculation(detail.segmentId, detail.sourceSequence);
    if (latest && normalizeComparable(latest.candidateText) === normalizeComparable(candidateText)) {
      latest.endpointProbability = detail.probability;
      return;
    }
    if (latest && !shouldStartExtendedHypothesis(
      latest.candidateText,
      candidateText,
      detail.probability ?? 0,
    )) return;
    startHypothesis(
      detail.chatSessionId,
      detail.segmentId,
      detail.sourceSequence,
      candidateText,
      detail.probability,
      'endpoint_candidate',
    );
  };

  const handleFinal = (event: Event): void => {
    const detail = (event as CustomEvent<SttPartialDetail>).detail;
    const finalText = detail?.text?.trim() ?? '';
    if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return;
    const key = partialKey(detail);
    if (key) clearCorrectionTimer(key);
    const matching = segmentSpeculations(detail.segmentId, detail.sourceSequence)
      .filter((active) => transcriptsCanReuseSpeculation(active.candidateText, finalText))
      .sort((left, right) => right.createdAtMs - left.createdAtMs);
    const accepted = matching[0];
    if (!accepted) {
      cancelSegmentSpeculations(detail.segmentId, detail.sourceSequence, 'final_transcript_mismatch');
      return;
    }
    accepted.finalText = finalText;
    clearFirstClauseTimer(accepted);
    segmentSpeculations(detail.segmentId, detail.sourceSequence)
      .filter((active) => active !== accepted)
      .forEach((active) => cancelSpeculation(active, 'final_hypothesis_not_selected'));
    // Once the authoritative final exists, normal accepted TTS owns the lane.
    // Only prefetch work that genuinely started before finalization may be promoted.
    // Starting a new speculative job here can block Faster Qwen, which is serialized.
    const speculativeTtsRestarted = false;
    acceptSpeculativeTts(accepted);
    dispatchPerformance('llm_speculation_final_accepted', {
      sessionId: accepted.sessionId,
      segmentId: accepted.segmentId,
      sourceSequence: accepted.sourceSequence,
      candidateChars: accepted.candidateText.length,
      finalChars: finalText.length,
      retainedHypotheses: matching.length,
      speculativeTtsStarted: accepted.prefetchStarted,
      speculativeTtsRestarted,
      speculativeTtsClauseChars: accepted.prefetchedClause?.length ?? 0,
    });
    notify(accepted);
  };

  const handleDeliverySettled = (event: Event): void => {
    const detail = (event as CustomEvent<SttPartialDetail>).detail;
    const key = partialKey(detail);
    if (key) {
      partials.delete(key);
      clearCorrectionTimer(key);
    }
    if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return;
    segmentSpeculations(detail.segmentId, detail.sourceSequence)
      .filter((active) => !active.reused)
      .forEach((active) => cancelSpeculation(active, 'accepted_final_not_routed_to_chat'));
  };

  window.addEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, handlePartial);
  window.addEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, handleCandidate);
  window.addEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, handleFinal);
  window.addEventListener(
    LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT,
    handleDeliverySettled,
  );

  return () => {
    removeMiddleware?.();
    removeMiddleware = null;
    [...activeSpeculations].forEach((active) => cancelSpeculation(active, 'controller_uninstalled'));
    correctionTimers.forEach((timer) => window.clearTimeout(timer));
    correctionTimers.clear();
    partials.clear();
    window.removeEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, handlePartial);
    window.removeEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, handleCandidate);
    window.removeEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, handleFinal);
    window.removeEventListener(
      LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT,
      handleDeliverySettled,
    );
  };
}

function clearCorrectionTimer(key: string): void {
  const timer = correctionTimers.get(key);
  if (timer !== undefined) window.clearTimeout(timer);
  correctionTimers.delete(key);
}

async function interceptChatStream(input: RequestInfo | URL, init: RequestInit | undefined, next: FetchNext): Promise<Response> {
  const fetchImpl = next;
  const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
  const rawUrl = typeof input === 'string' || input instanceof URL ? input.toString() : input.url;
  const url = new URL(rawUrl, window.location.origin);
  const match = method === 'POST' ? CHAT_STREAM_PATH.exec(url.pathname) : null;
  if (!match) return fetchImpl(input, init);

  const body = await parseRequestBody(input, init);
  const finalText = typeof body?.content === 'string' ? body.content.trim() : '';
  const sessionId = decodeURIComponent(match[1]);
  const active = activeSpeculations
    .filter((candidate) => (
      candidate.sessionId === sessionId
      && candidate.finalText
      && !candidate.reused
      && transcriptsCanReuseSpeculation(candidate.candidateText, finalText)
    ))
    .sort((left, right) => right.createdAtMs - left.createdAtMs)[0];
  if (
    !active
    || active.error
    || !speculationRequestCanReuse(body, active.providerId, active.modelId)
  ) {
    activeSpeculations
      .filter((candidate) => candidate.sessionId === sessionId && candidate.finalText)
      .forEach((candidate) => cancelSpeculation(candidate, 'final_request_not_compatible'));
    return fetchImpl(input, init);
  }

  const handshakeWaitBudgetMs = speculationHandshakeWaitBudgetMs({
    generationReady: Boolean(active.generationId),
    bufferedChunks: active.chunks.length,
    responseReady: active.handshakeResponseReady,
    completed: active.completed,
    error: Boolean(active.error),
  });
  const handshakeWaitStartedAt = performance.now();
  if (handshakeWaitBudgetMs > 0) {
    await Promise.race([
      active.startedPromise,
      delay(handshakeWaitBudgetMs),
    ]);
  }
  const handshakeWaitMs = performance.now() - handshakeWaitStartedAt;
  dispatchPerformance('llm_speculation_handshake_wait_completed', {
    sessionId,
    segmentId: active.segmentId,
    generationId: active.generationId,
    handshakeReady: Boolean(active.generationId),
    handshakeResponseReady: active.handshakeResponseReady,
    handshakeWaitMs,
    handshakeWaitBudgetMs,
    handshakeGraceMs: LIVE_SPECULATION_HANDSHAKE_GRACE_MS,
  });
  if (!active.generationId || active.error) {
    const fallbackReason = active.error
      ? 'speculation_error_at_final_request'
      : 'handshake_not_ready_at_final_request';
    cancelSpeculation(active, fallbackReason);
    dispatchPerformance('llm_speculation_fallback', {
      sessionId,
      segmentId: active.segmentId,
      handshakeWaitMs,
      handshakeWaitBudgetMs,
      reason: fallbackReason,
    });
    return fetchImpl(input, init);
  }
  active.reused = true;
  active.acceptBody = body;
  activeSpeculations
    .filter((candidate) => candidate !== active && candidate.sessionId === sessionId)
    .forEach((candidate) => cancelSpeculation(candidate, 'accepted_hypothesis_selected'));
  dispatchPerformance('llm_speculation_reused', {
    sessionId,
    segmentId: active.segmentId,
    generationId: active.generationId,
    bufferedChunks: active.chunks.length,
    handshakeWaitMs,
    speculativeTtsStarted: active.prefetchStarted,
    speculativeTtsClauseChars: active.prefetchedClause?.length ?? 0,
  });
  return createAcceptedSpeculationResponse(active, fetchImpl);
}

function startHypothesis(
  sessionId: string,
  segmentId: string,
  sourceSequence: number,
  candidateText: string,
  probability: number | undefined,
  trigger: string,
): void {
  if (!speculationEnabled() || !speculationCandidateCanStart(candidateText, probability ?? 0)) return;
  const duplicate = segmentSpeculations(segmentId, sourceSequence).find(
    (active) => normalizeComparable(active.candidateText) === normalizeComparable(candidateText),
  );
  if (duplicate) return;

  const active = createSpeculation(
    sessionId,
    segmentId,
    sourceSequence,
    candidateText,
    probability,
  );
  activeSpeculations.push(active);

  // Only the newest candidate prefetches audio. The previous LLM hypothesis is
  // retained for exact-final reuse, but duplicate Qwen work is cancelled.
  segmentSpeculations(segmentId, sourceSequence)
    .filter((candidate) => candidate !== active)
    .forEach((candidate) => cancelSpeculativeTts(candidate, 'newer_hypothesis'));

  const ordered = segmentSpeculations(segmentId, sourceSequence)
    .sort((left, right) => left.createdAtMs - right.createdAtMs);
  while (ordered.length > MAX_ACTIVE_HYPOTHESES) {
    const oldest = ordered.shift();
    if (oldest) cancelSpeculation(oldest, 'hypothesis_budget_exceeded');
  }

  dispatchPerformance('llm_speculation_hypothesis_started', {
    sessionId,
    segmentId,
    sourceSequence,
    candidateChars: candidateText.length,
    candidateWords: normalizeSpeculationWords(candidateText).length,
    endpointProbability: probability,
    trigger,
    activeHypotheses: segmentSpeculations(segmentId, sourceSequence).length,
  });
  consumeSpeculation(active, probability).catch((error: unknown) => {
    if (!activeSpeculations.includes(active)) return;
    active.error = error instanceof Error ? error.message : 'Speculative generation failed.';
    active.completed = true;
    active.resolveStarted();
    active.resolveGeneration();
    notify(active);
  });
}

function createSpeculation(
  sessionId: string,
  segmentId: string,
  sourceSequence: number,
  candidateText: string,
  endpointProbability: number | undefined,
): ActiveSpeculation {
  let resolveStarted: () => void = () => {};
  let resolveGeneration: () => void = () => {};
  const startedPromise = new Promise<void>((resolve) => { resolveStarted = resolve; });
  const generationPromise = new Promise<void>((resolve) => { resolveGeneration = resolve; });
  return {
    sessionId,
    segmentId,
    sourceSequence,
    candidateText,
    endpointProbability,
    createdAtMs: performance.now(),
    providerId: null,
    modelId: null,
    generationId: null,
    chunks: [],
    subscribers: new Set(),
    abortController: new AbortController(),
    startedPromise,
    resolveStarted,
    generationPromise,
    resolveGeneration,
    finalText: null,
    completed: false,
    error: null,
    handshakeResponseReady: false,
    reused: false,
    acceptBody: null,
    firstClause: new StableClauseAccumulator({
      // A tiny punctuated prefix can finish playing before synthesis of the
      // authoritative remainder begins. Eight characters still opens TTS
      // within the first streamed phrase while covering one decoder chunk.
      firstClauseMinimumCharacters: 8,
      firstClauseStableLookaheadCharacters: 4,
      firstClauseMaximumCharacters: 40,
      firstClauseDeadlineMs: 20,
    }),
    firstClauseTimer: null,
    prefetchedClause: null,
    prefetchStarted: false,
    prefetchPromise: null,
    prefetchAcceptPromise: null,
  };
}

async function consumeSpeculation(active: ActiveSpeculation, probability?: number): Promise<void> {
  const fetchImpl = ownFetch;
  dispatchPerformance('llm_speculation_started', {
    sessionId: active.sessionId,
    segmentId: active.segmentId,
    sourceSequence: active.sourceSequence,
    endpointProbability: probability,
    candidateChars: active.candidateText.length,
  });
  const response = await fetchImpl(
    `/api/live/speculation/sessions/${encodeURIComponent(active.sessionId)}/stream`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content: active.candidateText,
        segment_id: active.segmentId,
        source_sequence: active.sourceSequence,
      }),
      signal: active.abortController.signal,
    },
  );
  if (!response.ok || !response.body) throw new Error(`Speculation stream failed with status ${response.status}.`);
  active.handshakeResponseReady = true;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (!active.abortController.signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      pending += decoder.decode(value, { stream: true });
      const blocks = pending.split(/\n\n/);
      pending = blocks.pop() ?? '';
      for (const block of blocks) applySpeculationEvent(active, parseSseBlock(block));
    }
    pending += decoder.decode();
    if (pending.trim()) applySpeculationEvent(active, parseSseBlock(pending));
  } finally {
    reader.releaseLock();
    active.completed = true;
    active.resolveStarted();
    active.resolveGeneration();
    notify(active);
  }
}

function applySpeculationEvent(active: ActiveSpeculation, event: SpeculationEvent | null): void {
  if (!event) return;
  if (event.type === 'speculation_started' && typeof event.generation_id === 'string') {
    active.generationId = event.generation_id;
    active.providerId = event.provider_id ?? null;
    active.modelId = event.model_id ?? null;
    active.resolveStarted();
    if (active.prefetchedClause) startSpeculativeTtsPrefetch(active, active.prefetchedClause);
    return;
  }
  if (event.type === 'text_chunk' && typeof event.text === 'string') {
    active.chunks.push(event.text);
    collectSpeculativeFirstClause(active, event.text);
    notify(active);
    return;
  }
  if (event.type === 'error') {
    active.error = event.message || 'Speculative generation failed.';
    cancelSpeculativeTts(active, 'llm_speculation_error');
    notify(active);
  }
}

function collectSpeculativeFirstClause(active: ActiveSpeculation, text: string): void {
  if (!ttsSpeculationEnabled() || active.prefetchedClause) return;
  const ready = active.firstClause.append(text, performance.now());
  if (ready.length) {
    active.prefetchedClause = ready[0].text;
    clearFirstClauseTimer(active);
    startSpeculativeTtsPrefetch(active, active.prefetchedClause);
    return;
  }
  scheduleFirstClauseTimer(active);
}

function scheduleFirstClauseTimer(active: ActiveSpeculation): void {
  clearFirstClauseTimer(active);
  const remaining = active.firstClause.deadlineRemainingMs();
  if (remaining === null) return;
  active.firstClauseTimer = window.setTimeout(() => {
    active.firstClauseTimer = null;
    if (!activeSpeculations.includes(active) || active.abortController.signal.aborted) return;
    const ready = active.firstClause.takeReady(performance.now());
    if (ready.length) {
      active.prefetchedClause = ready[0].text;
      startSpeculativeTtsPrefetch(active, active.prefetchedClause);
      return;
    }
    scheduleFirstClauseTimer(active);
  }, Math.max(1, Math.ceil(remaining + 1)));
}

function clearFirstClauseTimer(active: ActiveSpeculation): void {
  if (active.firstClauseTimer !== null) window.clearTimeout(active.firstClauseTimer);
  active.firstClauseTimer = null;
}

function startSpeculativeTtsPrefetch(active: ActiveSpeculation, clause: string): void {
  const newest = newestSegmentSpeculation(active.segmentId, active.sourceSequence);
  if (
    !ttsSpeculationEnabled()
    || active.prefetchStarted
    || !active.generationId
    || !clause.trim()
    || !speculativeTtsPrefetchWindowOpen(active.finalText, newest === active)
  ) return;
  active.prefetchStarted = true;
  active.prefetchAcceptPromise = null;
  const fetchImpl = ownFetch;
  const synthesis = createLiveSpeechSynthesisOptions(clause, {
    scopeKey: active.sessionId,
    enablePerformancePlan: true,
    enableVocalContinuity: false,
  });
  const startedAt = performance.now();
  const priorOperation = active.prefetchPromise ?? Promise.resolve();
  active.prefetchPromise = priorOperation
    .catch(() => undefined)
    .then(async () => {
      const response = await fetchImpl('/api/live/speculation/tts-prefetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          generation_id: active.generationId,
          request: {
            text: clause,
            speaker: selectedVoiceId(),
            language: 'English',
            chunk_size: 8,
            temperature: 0.6,
            top_k: 20,
            top_p: 0.85,
            repetition_penalty: 1.0,
            append_silence: false,
            non_streaming_mode: false,
            parity_mode: true,
            diagnostics_stream_id: `chat-speculative-tts-${active.generationId}`,
            delivery_plan: synthesis.performancePlan,
            pronunciation_lexicon: synthesis.pronunciationLexicon ?? [],
          },
        }),
        signal: active.abortController.signal,
      });
      if (!response.ok) {
        throw new Error(`Speculative TTS prefetch failed with status ${response.status}.`);
      }
      dispatchPerformance('tts_speculative_prefetch_started', {
        sessionId: active.sessionId,
        segmentId: active.segmentId,
        generationId: active.generationId,
        clauseChars: clause.length,
        requestMs: performance.now() - startedAt,
      });
    })
    .catch((error: unknown) => {
      if (active.abortController.signal.aborted) return;
      dispatchPerformance('tts_speculative_prefetch_failed', {
        sessionId: active.sessionId,
        segmentId: active.segmentId,
        generationId: active.generationId,
        error: error instanceof Error ? error.message : String(error),
      });
    });
  if (active.finalText) acceptSpeculativeTts(active);
}

function acceptSpeculativeTts(active: ActiveSpeculation): void {
  if (!active.prefetchStarted || !active.generationId || active.prefetchAcceptPromise) return;
  const fetchImpl = ownFetch;
  active.prefetchAcceptPromise = (active.prefetchPromise ?? Promise.resolve())
    .then(async () => {
      const response = await fetchImpl(
        `/api/live/speculation/tts-prefetch/${encodeURIComponent(active.generationId as string)}/accept`,
        { method: 'POST', headers: { Accept: 'application/json' } },
      );
      if (!response.ok) throw new Error(`Speculative TTS accept failed with status ${response.status}.`);
      const payload = await response.json() as { buffered_chunk_count?: number; completed?: boolean };
      dispatchPerformance('tts_speculative_prefetch_accepted', {
        sessionId: active.sessionId,
        segmentId: active.segmentId,
        generationId: active.generationId,
        bufferedChunks: payload.buffered_chunk_count ?? 0,
        completed: payload.completed ?? false,
      });
    })
    .catch((error: unknown) => {
      dispatchPerformance('tts_speculative_prefetch_accept_failed', {
        sessionId: active.sessionId,
        segmentId: active.segmentId,
        generationId: active.generationId,
        error: error instanceof Error ? error.message : String(error),
      });
    });
}

function cancelSpeculativeTts(active: ActiveSpeculation, reason: string): void {
  if (!active.prefetchStarted || !active.generationId) return;
  active.prefetchStarted = false;
  active.prefetchAcceptPromise = null;
  const fetchImpl = ownFetch;
  const priorOperation = active.prefetchPromise ?? Promise.resolve();
  active.prefetchPromise = priorOperation
    .catch(() => undefined)
    .then(async () => {
      await fetchImpl(
        `/api/live/speculation/tts-prefetch/${encodeURIComponent(active.generationId as string)}/cancel`,
        { method: 'POST', headers: { Accept: 'application/json' } },
      ).catch(() => undefined);
      dispatchPerformance('tts_speculative_prefetch_cancelled', {
        sessionId: active.sessionId,
        segmentId: active.segmentId,
        generationId: active.generationId,
        reason,
      });
    });
}

function createAcceptedSpeculationResponse(
  active: ActiveSpeculation,
  fetchImpl: typeof fetch,
): Response {
  const encoder = new TextEncoder();
  let cursor = 0;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let closed = false;
      let chunksReleased = false;
      let releasePending = false;
      const emit = (payload: Record<string, unknown>): void => {
        if (!closed) controller.enqueue(encoder.encode(`data: ${JSON.stringify(payload)}\n\n`));
      };
      const flushChunks = (): void => {
        if (!chunksReleased) return;
        while (cursor < active.chunks.length) {
          emit({ type: 'text_chunk', text: active.chunks[cursor], speculative_reuse: true });
          cursor += 1;
        }
      };
      const releaseChunksAfterTtsGate = (): void => {
        if (chunksReleased || releasePending || cursor >= active.chunks.length) return;
        releasePending = true;
        void waitForSpeculativeTtsAcceptance(active).then(() => {
          releasePending = false;
          chunksReleased = true;
          flushChunks();
        });
      };
      const update = (): void => {
        releaseChunksAfterTtsGate();
        flushChunks();
      };
      active.subscribers.add(update);
      releaseChunksAfterTtsGate();
      void active.generationPromise.then(async () => {
        if (!chunksReleased && cursor < active.chunks.length) {
          await waitForSpeculativeTtsAcceptance(active);
        }
        chunksReleased = true;
        flushChunks();
        if (active.error || !active.generationId || !active.finalText) {
          throw new Error(active.error || 'Speculation could not be accepted.');
        }
        const acceptedResponse = await fetchImpl(
          `/api/live/speculation/sessions/${encodeURIComponent(active.sessionId)}/${encodeURIComponent(active.generationId)}/accept`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              final_text: active.finalText,
              provider_id: bodyString(active.providerId),
              model_id: bodyString(active.modelId),
              user_turn_id: bodyString(active.acceptBody?.user_turn_id),
              speech_segment_id: bodyString(active.acceptBody?.speech_segment_id),
              live_voice_turn_id: bodyString(active.acceptBody?.live_voice_turn_id),
            }),
          },
        );
        if (!acceptedResponse.ok) throw new Error(`Speculation accept failed with status ${acceptedResponse.status}.`);
        const accepted = await acceptedResponse.json() as AcceptedSpeculation;
        emit({ type: 'user_message', user_message: accepted.user_message });
        emit({ type: 'session', session: accepted.session });
        emit({ type: 'done', speculative_reuse: true });
        dispatchPerformance('llm_speculation_committed', {
          sessionId: active.sessionId,
          generationId: active.generationId,
          responseChars: accepted.content.length,
          speculativeTts: active.prefetchStarted,
        });
        closed = true;
        active.subscribers.delete(update);
        removeSpeculation(active);
        partials.delete(`${active.segmentId}:${active.sourceSequence}`);
        controller.close();
      }).catch((error: unknown) => {
        emit({ type: 'error', message: error instanceof Error ? error.message : 'Speculation reuse failed.' });
        closed = true;
        active.subscribers.delete(update);
        controller.close();
      });
    },
    cancel() {
      active.subscribers.clear();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-store',
      ...(active.generationId
        ? { 'X-Omnix-Speculation-Generation-Id': active.generationId }
        : {}),
    },
  });
}

async function waitForSpeculativeTtsAcceptance(active: ActiveSpeculation): Promise<void> {
  if (!ttsSpeculationEnabled()) return;
  const deadline = performance.now() + SPECULATIVE_TTS_ACCEPT_WAIT_MS;
  while (!active.prefetchAcceptPromise && !active.error && performance.now() < deadline) {
    await delay(Math.min(5, Math.max(1, deadline - performance.now())));
  }
  const acceptance = active.prefetchAcceptPromise;
  if (!acceptance) return;
  const remainingMs = deadline - performance.now();
  if (remainingMs <= 0) return;
  await Promise.race([
    acceptance,
    delay(remainingMs),
  ]);
}

function cancelSpeculation(active: ActiveSpeculation, reason: string): void {
  if (!activeSpeculations.includes(active)) return;
  removeSpeculation(active);
  clearFirstClauseTimer(active);
  cancelSpeculativeTts(active, reason);
  active.abortController.abort(reason);
  active.resolveStarted();
  active.resolveGeneration();
  active.subscribers.clear();
  dispatchPerformance('llm_speculation_cancelled', {
    sessionId: active.sessionId,
    segmentId: active.segmentId,
    generationId: active.generationId,
    candidateChars: active.candidateText.length,
    reason,
  });
}

function cancelSegmentSpeculations(
  segmentId: string,
  sourceSequence: number,
  reason: string,
): void {
  segmentSpeculations(segmentId, sourceSequence)
    .forEach((active) => cancelSpeculation(active, reason));
}

function removeSpeculation(active: ActiveSpeculation): void {
  activeSpeculations = activeSpeculations.filter((candidate) => candidate !== active);
}

function segmentSpeculations(segmentId: string, sourceSequence: number): ActiveSpeculation[] {
  return activeSpeculations.filter(
    (active) => active.segmentId === segmentId && active.sourceSequence === sourceSequence,
  );
}

function newestSegmentSpeculation(
  segmentId: string,
  sourceSequence: number,
): ActiveSpeculation | null {
  return segmentSpeculations(segmentId, sourceSequence)
    .sort((left, right) => right.createdAtMs - left.createdAtMs)[0] ?? null;
}

function notify(active: ActiveSpeculation): void {
  active.subscribers.forEach((subscriber) => subscriber());
}

function partialKey(detail: SttPartialDetail | undefined): string | null {
  if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return null;
  return `${detail.segmentId}:${detail.sourceSequence}`;
}

function normalizeComparable(text: string): string {
  return normalizeSpeculationWords(text).join(' ');
}

async function parseRequestBody(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<ChatStreamRequestBody | null> {
  const body = init?.body;
  if (typeof body === 'string') {
    try {
      const parsed = JSON.parse(body) as unknown;
      return parsed && typeof parsed === 'object'
        ? parsed as ChatStreamRequestBody
        : null;
    } catch {
      return null;
    }
  }
  if (input instanceof Request) {
    try {
      const parsed = await input.clone().json() as unknown;
      return parsed && typeof parsed === 'object'
        ? parsed as ChatStreamRequestBody
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

function bodyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function parseSseBlock(block: string): SpeculationEvent | null {
  const data = block.split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n');
  if (!data) return null;
  return parseJson(speculationEventSchema, data);
}

function selectedVoiceId(): string | null {
  if (typeof document !== 'undefined') {
    const liveCallVoice = document.querySelector<HTMLElement>('.assistant-live-card')
      ?.dataset.liveVoiceId?.trim();
    if (liveCallVoice) return liveCallVoice;
    const mounted = document.querySelector<HTMLSelectElement>('select[aria-label="Cloned voice"]')
      ?.value.trim();
    if (mounted) return mounted;
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(VOICE_SETTINGS_KEY) || '{}') as { voiceId?: unknown };
    return typeof parsed.voiceId === 'string' && parsed.voiceId.trim()
      ? parsed.voiceId.trim()
      : null;
  } catch {
    return null;
  }
}

function speculationEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  return env?.VITE_LIVE_SPECULATION_ENABLED?.trim().toLowerCase() !== 'false';
}

function ttsSpeculationEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  return speculativeTtsPrefetchEnabled(env?.VITE_LIVE_TTS_SPECULATION_ENABLED);
}

function dispatchPerformance(stage: string, detail: Record<string, unknown>): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...detail });
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
