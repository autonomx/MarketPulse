import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

let liveSpeculationEligibilityDiagnosticsInstalled = false;

const CORRECTION_PATTERN = /(?:^|\s)(?:uh+|um+|erm+|wait|sorry|actually|correction|no[,. ]+i mean)(?:\s|$)/i;
const WORD_PATTERN = /[\p{L}\p{N}_]+(?:['’][\p{L}\p{N}_]+)?/gu;

type SttDetail = {
  chatSessionId?: string;
  segmentId?: string;
  sourceSequence?: number;
  text?: string;
  probability?: number;
  modelTimeMs?: number;
};

type EligibilityReason =
  | 'eligible'
  | 'disabled'
  | 'missing_partial'
  | 'too_short'
  | 'correction_shaped'
  | 'no_candidate_event'
  | 'eligible_candidate_not_started'
  | 'speculation_active'
  | 'speculation_cancelled_before_final'
  | 'speculation_reused';

type EligibilityClassification = {
  eligible: boolean;
  reason: EligibilityReason;
  wordCount: number;
  charCount: number;
};

type SegmentState = {
  sessionId: string | null;
  segmentId: string;
  sourceSequence: number;
  partialSeen: boolean;
  candidateSeen: boolean;
  finalSeen: boolean;
  speculationStarted: boolean;
  speculationActive: boolean;
  speculationReused: boolean;
  lastCancellationReason: string | null;
  latestClassification: EligibilityClassification | null;
  candidateProbability: number | null;
  candidateModelTimeMs: number | null;
};

const segments = new Map<string, SegmentState>();

export function classifySpeculationEligibility(text: string): EligibilityClassification {
  const normalized = text.trim();
  const wordCount = [...normalized.matchAll(WORD_PATTERN)].length;
  if (wordCount < 2) {
    return { eligible: false, reason: 'too_short', wordCount, charCount: normalized.length };
  }
  if (CORRECTION_PATTERN.test(normalized)) {
    return {
      eligible: false,
      reason: 'correction_shaped',
      wordCount,
      charCount: normalized.length,
    };
  }
  return { eligible: true, reason: 'eligible', wordCount, charCount: normalized.length };
}

export function initializeLiveSpeculationEligibilityDiagnostics(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveSpeculationEligibilityDiagnosticsInstalled) return () => undefined;
  liveSpeculationEligibilityDiagnosticsInstalled = true;

  const handlePartial = (event: Event): void => {
    const detail = (event as CustomEvent<SttDetail>).detail;
    const state = stateFor(detail, true);
    if (!state) return;
    state.partialSeen = true;
    state.latestClassification = classifySpeculationEligibility(detail.text ?? '');
  };

  const handleCandidate = (event: Event): void => {
    const detail = (event as CustomEvent<SttDetail>).detail;
    const state = stateFor(detail, true);
    if (!state) {
      dispatchPerformance('llm_speculation_candidate_evaluated', {
        reason: 'missing_partial',
        eligible: false,
        hasSessionId: Boolean(detail?.chatSessionId),
        hasSegmentId: Boolean(detail?.segmentId),
        hasSourceSequence: typeof detail?.sourceSequence === 'number',
      });
      return;
    }
    state.candidateSeen = true;
    state.candidateProbability = finiteNumber(detail.probability);
    state.candidateModelTimeMs = finiteNumber(detail.modelTimeMs);
    const classification = !speculationEnabled()
      ? disabledClassification(state.latestClassification)
      : state.latestClassification ?? {
        eligible: false,
        reason: 'missing_partial' as const,
        wordCount: 0,
        charCount: 0,
      };
    state.latestClassification = classification;
    dispatchPerformance('llm_speculation_candidate_evaluated', {
      sessionId: state.sessionId,
      segmentId: state.segmentId,
      sourceSequence: state.sourceSequence,
      eligible: classification.eligible,
      reason: classification.reason,
      wordCount: classification.wordCount,
      charCount: classification.charCount,
      endpointProbability: state.candidateProbability,
      modelTimeMs: state.candidateModelTimeMs,
      partialSeen: state.partialSeen,
    });
  };

  const handleFinal = (event: Event): void => {
    const detail = (event as CustomEvent<SttDetail>).detail;
    const state = stateFor(detail, true);
    if (!state) return;
    state.finalSeen = true;
    const finalClassification = classifySpeculationEligibility(detail.text ?? '');
    dispatchPerformance('llm_speculation_final_eligibility', {
      sessionId: state.sessionId,
      segmentId: state.segmentId,
      sourceSequence: state.sourceSequence,
      reason: finalReason(state),
      candidateSeen: state.candidateSeen,
      partialSeen: state.partialSeen,
      speculationStarted: state.speculationStarted,
      speculationActive: state.speculationActive,
      speculationReused: state.speculationReused,
      lastCancellationReason: state.lastCancellationReason,
      candidateReason: state.latestClassification?.reason ?? null,
      finalWordCount: finalClassification.wordCount,
      finalCharCount: finalClassification.charCount,
      endpointProbability: state.candidateProbability,
      modelTimeMs: state.candidateModelTimeMs,
    });
  };

  const handleDeliverySettled = (event: Event): void => {
    const detail = (event as CustomEvent<SttDetail>).detail;
    const key = segmentKey(detail);
    if (!key) return;
    const state = segments.get(key);
    if (state && !state.speculationReused) {
      const diagnostics = {
        sessionId: state.sessionId,
        segmentId: state.segmentId,
        sourceSequence: state.sourceSequence,
        reason: finalReason(state),
        candidateSeen: state.candidateSeen,
        partialSeen: state.partialSeen,
        finalSeen: state.finalSeen,
        speculationStarted: state.speculationStarted,
        speculationActive: state.speculationActive,
        lastCancellationReason: state.lastCancellationReason,
        candidateReason: state.latestClassification?.reason ?? null,
        wordCount: state.latestClassification?.wordCount ?? 0,
        charCount: state.latestClassification?.charCount ?? 0,
        endpointProbability: state.candidateProbability,
        modelTimeMs: state.candidateModelTimeMs,
      };
      dispatchPerformance('llm_speculation_not_reused', diagnostics);
      if (!state.speculationStarted) {
        dispatchPerformance('llm_speculation_not_started', diagnostics);
      }
    }
    segments.delete(key);
  };

  const handlePerformance = (event: Event): void => {
    const detail = (event as CustomEvent<Record<string, unknown>>).detail ?? {};
    const stage = typeof detail.stage === 'string' ? detail.stage : '';
    if (![
      'llm_speculation_started',
      'llm_speculation_cancelled',
      'llm_speculation_reused',
    ].includes(stage)) return;
    const state = stateFromPerformanceDetail(detail);
    if (!state) return;
    if (stage === 'llm_speculation_started') {
      state.speculationStarted = true;
      state.speculationActive = true;
      state.speculationReused = false;
      state.lastCancellationReason = null;
    } else if (stage === 'llm_speculation_cancelled') {
      state.speculationActive = false;
      state.lastCancellationReason = typeof detail.reason === 'string'
        ? detail.reason
        : 'unspecified';
    } else if (stage === 'llm_speculation_reused') {
      state.speculationActive = false;
      state.speculationReused = true;
    }
    segments.set(`${state.segmentId}:${state.sourceSequence}`, state);
  };

  window.addEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, handlePartial);
  window.addEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, handleCandidate);
  window.addEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, handleFinal);
  window.addEventListener(LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, handleDeliverySettled);
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);

  return () => {
    window.removeEventListener(LIVE_STT_SPECULATION_PARTIAL_EVENT, handlePartial);
    window.removeEventListener(LIVE_STT_SPECULATION_CANDIDATE_EVENT, handleCandidate);
    window.removeEventListener(LIVE_STT_SPECULATION_FINAL_EVENT, handleFinal);
    window.removeEventListener(LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, handleDeliverySettled);
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    segments.clear();
    liveSpeculationEligibilityDiagnosticsInstalled = false;
  };
}

function disabledClassification(
  previous: EligibilityClassification | null,
): EligibilityClassification {
  return {
    eligible: false,
    reason: 'disabled',
    wordCount: previous?.wordCount ?? 0,
    charCount: previous?.charCount ?? 0,
  };
}

function finalReason(state: SegmentState): EligibilityReason {
  if (state.speculationReused) return 'speculation_reused';
  if (state.speculationActive) return 'speculation_active';
  if (state.speculationStarted && state.lastCancellationReason) {
    return 'speculation_cancelled_before_final';
  }
  if (!speculationEnabled()) return 'disabled';
  if (!state.candidateSeen) return 'no_candidate_event';
  if (state.latestClassification?.eligible) return 'eligible_candidate_not_started';
  return state.latestClassification?.reason ?? 'missing_partial';
}

function stateFromPerformanceDetail(
  detail: Record<string, unknown>,
): SegmentState | null {
  const segmentId = typeof detail.segmentId === 'string' ? detail.segmentId : null;
  if (!segmentId) return null;
  const sourceSequence = typeof detail.sourceSequence === 'number'
    ? detail.sourceSequence
    : null;
  if (sourceSequence !== null) {
    const key = `${segmentId}:${sourceSequence}`;
    const existing = segments.get(key);
    if (existing) return existing;
    const created = createState({
      chatSessionId: typeof detail.sessionId === 'string' ? detail.sessionId : undefined,
      segmentId,
      sourceSequence,
    });
    if (created) segments.set(key, created);
    return created;
  }
  for (const state of segments.values()) {
    if (state.segmentId === segmentId) return state;
  }
  return null;
}

function stateFor(detail: SttDetail | undefined, create: boolean): SegmentState | null {
  const key = segmentKey(detail);
  if (!key) return null;
  const existing = segments.get(key);
  if (existing || !create) return existing ?? null;
  const created = createState(detail);
  if (created) segments.set(key, created);
  return created;
}

function createState(detail: SttDetail | undefined): SegmentState | null {
  if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return null;
  return {
    sessionId: detail.chatSessionId ?? null,
    segmentId: detail.segmentId,
    sourceSequence: detail.sourceSequence,
    partialSeen: false,
    candidateSeen: false,
    finalSeen: false,
    speculationStarted: false,
    speculationActive: false,
    speculationReused: false,
    lastCancellationReason: null,
    latestClassification: null,
    candidateProbability: null,
    candidateModelTimeMs: null,
  };
}

function segmentKey(detail: SttDetail | undefined): string | null {
  if (!detail?.segmentId || typeof detail.sourceSequence !== 'number') return null;
  return `${detail.segmentId}:${detail.sourceSequence}`;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function speculationEnabled(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  return env?.VITE_LIVE_SPECULATION_ENABLED?.trim().toLowerCase() !== 'false';
}

function dispatchPerformance(stage: string, detail: Record<string, unknown>): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...detail });
}
