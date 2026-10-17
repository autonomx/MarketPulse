import { afterEach, describe, expect, it } from 'vitest';

import {
  assessSemanticTurn,
  conversationTimingProfile,
  reduceUserFloor,
  semanticFinalizeDelay,
} from './live-voice-floor-manager';
import {
  noteLiveSttNegotiation,
  resetLiveSttCapabilityState,
} from './live-stt-capability-state';
import {
  noteAssistantTurnCompletionContext,
  resetAssistantTurnCompletionContext,
} from './live-turn-context';

afterEach(() => {
  resetAssistantTurnCompletionContext();
  resetLiveSttCapabilityState();
});

describe('live voice floor manager', () => {
  it('waits longer for hesitation and unfinished clauses', () => {
    const hesitation = assessSemanticTurn('I think we should um', 'balanced');
    const unfinished = assessSemanticTurn('We should take the road because', 'balanced');
    const command = assessSemanticTurn('Open the inventory', 'balanced');

    expect(hesitation.reason).toBe('trailing_hesitation');
    expect(unfinished.reason).toBe('unfinished_clause');
    expect(command.reason).toBe('complete_command');
    expect(hesitation.recommendedWaitMs).toBeGreaterThan(command.recommendedWaitMs);
    expect(unfinished.recommendedWaitMs).toBeGreaterThan(command.recommendedWaitMs);
  });

  it('uses a bounded 220 ms balanced fast path for clearly terminated questions and commands', () => {
    expect(semanticFinalizeDelay('Where are we going?', 'balanced')).toBe(220);
    expect(semanticFinalizeDelay('Open the inventory', 'balanced')).toBe(220);
  });

  it('does not apply the clear-turn fast path to ordinary statements', () => {
    expect(semanticFinalizeDelay('The road looks dangerous', 'balanced')).toBe(360);
    expect(semanticFinalizeDelay('We should take the road because', 'balanced')).toBe(1_000);
  });

  it('treats a trailing question fragment as unfinished even when an earlier clause has a question mark', () => {
    const firstFragment = assessSemanticTurn("What's up? What's happening? What was", 'balanced');
    const secondFragment = assessSemanticTurn('it last? Was it last', 'balanced');
    const laterStatement = assessSemanticTurn('thing? I know how it is.', 'balanced');

    expect(firstFragment).toMatchObject({ reason: 'unfinished_clause', recommendedWaitMs: 1_000 });
    expect(secondFragment).toMatchObject({ reason: 'unfinished_clause', recommendedWaitMs: 1_000 });
    expect(laterStatement.reason).toBe('definitive_statement');
    expect(semanticFinalizeDelay("What's up? What's happening? What was", 'balanced')).toBe(1_000);
    expect(semanticFinalizeDelay('it last? Was it last', 'balanced')).toBe(1_000);
  });

  it('keeps the floor for empty and one-word partials until stronger completion evidence arrives', () => {
    const profile = conversationTimingProfile('balanced');

    expect(assessSemanticTurn('', 'balanced').reason).toBe('insufficient_text');
    expect(assessSemanticTurn('Hey', 'balanced').reason).toBe('insufficient_text');
    expect(semanticFinalizeDelay('', 'balanced')).toBe(profile.maximumWaitMs);
    expect(semanticFinalizeDelay('Hey', 'balanced')).toBe(profile.maximumWaitMs);
  });

  it('uses a short acoustic fallback when negotiated STT is final-only', () => {
    noteLiveSttNegotiation('parakeet', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
    ]);

    expect(semanticFinalizeDelay('', 'quick')).toBe(260);
    expect(semanticFinalizeDelay('', 'balanced')).toBe(350);
    expect(semanticFinalizeDelay('', 'reflective')).toBe(650);
    expect(semanticFinalizeDelay('Hey', 'balanced')).toBe(
      conversationTimingProfile('balanced').maximumWaitMs,
    );
  });

  it('keeps semantic timing for providers with pre-final endpoint evidence', () => {
    noteLiveSttNegotiation('segmented-test-provider', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
      'continuous_words',
      'semantic_endpointing',
      'delayed_flush',
    ]);

    expect(semanticFinalizeDelay('', 'balanced')).toBe(
      conversationTimingProfile('balanced').maximumWaitMs,
    );
  });

  it('stays conservative when a provider advertises an unknown capability', () => {
    noteLiveSttNegotiation('future-streaming-provider', [
      'segmented_audio',
      'authoritative_final',
      'partial_transcripts',
    ]);

    expect(semanticFinalizeDelay('', 'balanced')).toBe(
      conversationTimingProfile('balanced').maximumWaitMs,
    );
  });

  it('uses the clear-turn path for a one-word answer to a recent assistant question', () => {
    noteAssistantTurnCompletionContext({
      turnId: 'assistant-question',
      questionCount: 1,
      createsObligation: true,
    });

    expect(assessSemanticTurn('Vancouver', 'balanced')).toMatchObject({
      probabilityDone: 0.96,
      reason: 'contextual_short_answer',
      recommendedWaitMs: 220,
    });
    expect(semanticFinalizeDelay('Vancouver', 'balanced')).toBe(220);
    expect(semanticFinalizeDelay('yes', 'balanced')).toBe(220);
  });

  it('does not treat connector or hesitation words as complete contextual answers', () => {
    const profile = conversationTimingProfile('balanced');
    noteAssistantTurnCompletionContext({
      turnId: 'assistant-question',
      questionCount: 1,
      createsObligation: true,
    });

    expect(assessSemanticTurn('because', 'balanced').reason).toBe('insufficient_text');
    expect(semanticFinalizeDelay('because', 'balanced')).toBe(profile.maximumWaitMs);
    expect(semanticFinalizeDelay('well', 'balanced')).toBe(profile.maximumWaitMs);
  });

  it('does not use stale or absent assistant context for arbitrary one-word speech', () => {
    const profile = conversationTimingProfile('balanced');
    resetAssistantTurnCompletionContext();

    expect(assessSemanticTurn('Vancouver', 'balanced').reason).toBe('insufficient_text');
    expect(semanticFinalizeDelay('Vancouver', 'balanced')).toBe(profile.maximumWaitMs);
  });

  it('provides bounded quick balanced and reflective profiles', () => {
    expect(semanticFinalizeDelay('Open the inventory', 'quick')).toBeLessThan(
      semanticFinalizeDelay('Open the inventory', 'reflective'),
    );
    expect(semanticFinalizeDelay('I think we should um', 'reflective')).toBe(
      conversationTimingProfile('reflective').maximumWaitMs,
    );
  });

  it('keeps user-floor state independent from assistant lifecycle', () => {
    let state = reduceUserFloor('idle', { type: 'listen' });
    state = reduceUserFloor(state, { type: 'speech_candidate' });
    state = reduceUserFloor(state, { type: 'speech_confirmed', assistantSpeaking: false });
    expect(state).toBe('speaking');
    state = reduceUserFloor(state, { type: 'pause' });
    expect(state).toBe('paused');
    state = reduceUserFloor(state, { type: 'completion_check' });
    expect(state).toBe('completion_pending');
    state = reduceUserFloor(state, { type: 'commit' });
    expect(state).toBe('listening');
  });
});
