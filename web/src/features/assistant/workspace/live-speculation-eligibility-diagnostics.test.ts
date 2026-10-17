import { afterEach, describe, expect, it } from 'vitest';

import {
  classifySpeculationEligibility,
  initializeLiveSpeculationEligibilityDiagnostics,
} from './live-speculation-eligibility-diagnostics';
import { ASSISTANT_VOICE_PERF_EVENT, LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

let cleanup: (() => void) | null = null;

afterEach(() => {
  cleanup?.();
  cleanup = null;
});

describe('live speculation eligibility diagnostics', () => {
  it('classifies candidates without exposing transcript content', () => {
    expect(classifySpeculationEligibility('hello')).toEqual({
      eligible: false,
      reason: 'too_short',
      wordCount: 1,
      charCount: 5,
    });
    expect(classifySpeculationEligibility('wait actually continue')).toMatchObject({
      eligible: false,
      reason: 'correction_shaped',
      wordCount: 3,
    });
    expect(classifySpeculationEligibility('tell me more')).toEqual({
      eligible: true,
      reason: 'eligible',
      wordCount: 3,
      charCount: 12,
    });
  });

  it('records why a final turn did not enter speculation', () => {
    const events: Array<Record<string, unknown>> = [];
    const handlePerformance = (event: Event): void => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    cleanup = initializeLiveSpeculationEligibilityDiagnostics();

    const detail = {
      chatSessionId: 'chat:test',
      segmentId: 'segment-1',
      sourceSequence: 1,
      text: 'hello',
      probability: 0.91,
      modelTimeMs: 420,
    };
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_CANDIDATE_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_FINAL_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, { detail }));

    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    const candidate = events.find((event) => event.stage === 'llm_speculation_candidate_evaluated');
    const final = events.find((event) => event.stage === 'llm_speculation_final_eligibility');
    const notStarted = events.find((event) => event.stage === 'llm_speculation_not_started');
    const notReused = events.find((event) => event.stage === 'llm_speculation_not_reused');

    expect(candidate).toMatchObject({
      eligible: false,
      reason: 'too_short',
      wordCount: 1,
      charCount: 5,
      partialSeen: true,
    });
    expect(final).toMatchObject({
      reason: 'too_short',
      candidateSeen: true,
      speculationStarted: false,
      speculationActive: false,
      speculationReused: false,
      finalWordCount: 1,
      finalCharCount: 5,
    });
    expect(notStarted).toMatchObject({
      reason: 'too_short',
      candidateSeen: true,
      partialSeen: true,
      finalSeen: true,
    });
    expect(notReused).toMatchObject({
      reason: 'too_short',
      speculationStarted: false,
      speculationActive: false,
    });
    for (const event of [candidate, final, notStarted, notReused]) {
      expect(event).not.toHaveProperty('text');
      expect(event).not.toHaveProperty('content');
    }
  });

  it('distinguishes a generation cancelled before the final transcript', () => {
    const events: Array<Record<string, unknown>> = [];
    const handlePerformance = (event: Event): void => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    cleanup = initializeLiveSpeculationEligibilityDiagnostics();

    const detail = {
      chatSessionId: 'chat:test',
      segmentId: 'segment-2',
      sourceSequence: 2,
      text: 'tell me more',
      probability: 0.93,
      modelTimeMs: 620,
    };
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_CANDIDATE_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(ASSISTANT_VOICE_PERF_EVENT, {
      detail: {
        stage: 'llm_speculation_started',
        sessionId: detail.chatSessionId,
        segmentId: detail.segmentId,
        sourceSequence: detail.sourceSequence,
      },
    }));
    window.dispatchEvent(new CustomEvent(ASSISTANT_VOICE_PERF_EVENT, {
      detail: {
        stage: 'llm_speculation_cancelled',
        sessionId: detail.chatSessionId,
        segmentId: detail.segmentId,
        reason: 'transcript_resumed_or_corrected',
      },
    }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_FINAL_EVENT, { detail }));
    window.dispatchEvent(new CustomEvent(LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, { detail }));

    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerformance);
    const final = events.find((event) => event.stage === 'llm_speculation_final_eligibility');
    const notReused = events.find((event) => event.stage === 'llm_speculation_not_reused');
    const notStarted = events.find((event) => event.stage === 'llm_speculation_not_started');

    expect(final).toMatchObject({
      reason: 'speculation_cancelled_before_final',
      speculationStarted: true,
      speculationActive: false,
      speculationReused: false,
      lastCancellationReason: 'transcript_resumed_or_corrected',
    });
    expect(notReused).toMatchObject({
      reason: 'speculation_cancelled_before_final',
      speculationStarted: true,
      speculationActive: false,
      lastCancellationReason: 'transcript_resumed_or_corrected',
    });
    expect(notStarted).toBeUndefined();
    for (const event of [final, notReused]) {
      expect(event).not.toHaveProperty('text');
      expect(event).not.toHaveProperty('content');
    }
  });
});
