/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  noteLiveSttNegotiation,
  resetLiveSttCapabilityState,
} from './live-stt-capability-state';
import { assessSemanticTurn, semanticFinalizeDelay } from './live-voice-floor-manager';
import { LiveVoiceTurnCoordinator, endpointFusionAction, initializeLiveVoiceTranscriptReconciliation, removeTransientFinalUserRows, resetLiveVoiceTurnCoordinatorForTests, type LiveVoiceTurnTimelineDetail } from './live-voice-turn-coordinator';
import { liveVoiceTranscriptStore } from './live-voice-transcript-store';
import { LIVE_COORDINATION_TERMINAL_EVENT, LIVE_VOICE_TURN_TIMELINE_EVENT } from '../../../events/bus';

afterEach(() => {
  resetLiveVoiceTurnCoordinatorForTests();
  resetLiveSttCapabilityState();
  document.body.innerHTML = '';
});

describe('live voice turn coordinator', () => {
  it('keeps the latest speech-end timestamp before finalization', () => {
    const coordinator = new LiveVoiceTurnCoordinator();
    const events: LiveVoiceTurnTimelineDetail[] = [];
    const listener = (event: Event) => events.push(
      (event as CustomEvent<LiveVoiceTurnTimelineDetail>).detail,
    );
    window.addEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, listener);

    coordinator.speechEnded('voice-turn:one', 100);
    coordinator.speechEnded('voice-turn:one', 150);
    coordinator.finalReceived('voice-turn:one', 220);
    coordinator.speechEnded('voice-turn:one', 240);

    expect(coordinator.snapshot('voice-turn:one')).toMatchObject({
      state: 'committed',
      speechEndedAt: 150,
      finalReceivedAt: 220,
    });
    expect(events.map((event) => event.event)).toEqual([
      'speech_ended',
      'speech_ended',
      'final_received',
    ]);
    window.removeEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, listener);
  });

  it('fuses endpoint confidence, stability, and silence into one action', () => {
    expect(endpointFusionAction({
      endpointProbability: 0.8,
      endpointThreshold: 0.75,
      silenceMs: 170,
      transcriptStableMs: 100,
      semanticProbabilityDone: 0.94,
      transcriptWords: 4,
      correctionPending: false,
    })).toBe('commit');
    expect(endpointFusionAction({
      endpointProbability: 0.48,
      endpointThreshold: 0.75,
      silenceMs: 40,
      transcriptStableMs: 80,
      semanticProbabilityDone: 0.7,
      transcriptWords: 4,
      correctionPending: false,
    })).toBe('speculate');
    expect(endpointFusionAction({
      endpointProbability: 0.95,
      endpointThreshold: 0.75,
      silenceMs: 300,
      transcriptStableMs: 100,
      semanticProbabilityDone: 0.95,
      transcriptWords: 3,
      correctionPending: true,
    })).toBe('continue');
  });

  it('uses semantic-aware acoustic confirmation for authoritative EOU', () => {
    noteLiveSttNegotiation('nemotron_parakeet_eou', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
      'partial_transcripts',
      'authoritative_eou',
    ]);

    const incompleteCandidate = {
      endpointProbability: 1,
      endpointThreshold: 0.75,
      transcriptStableMs: 500,
      semanticProbabilityDone: 0.18,
      transcriptWords: 3,
      correctionPending: false,
    };

    expect(endpointFusionAction({
      ...incompleteCandidate,
      silenceMs: 499,
    })).toBe('speculate');
    expect(endpointFusionAction({
      ...incompleteCandidate,
      silenceMs: 500,
    })).toBe('commit');

    const completeCandidate = {
      ...incompleteCandidate,
      semanticProbabilityDone: 0.95,
      transcriptWords: 4,
    };
    expect(endpointFusionAction({
      ...completeCandidate,
      silenceMs: 359,
    })).toBe('speculate');
    expect(endpointFusionAction({
      ...completeCandidate,
      silenceMs: 360,
    })).toBe('commit');
    expect(endpointFusionAction({
      ...completeCandidate,
      silenceMs: 600,
      transcriptWords: 0,
    })).toBe('continue');
  });

  it('starts private speculation before the authoritative EOU commit threshold', () => {
    noteLiveSttNegotiation('nemotron_parakeet_eou', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
      'partial_transcripts',
      'authoritative_eou',
    ]);

    const candidate = {
      endpointProbability: 1,
      endpointThreshold: 0.5,
      transcriptStableMs: 215,
      semanticProbabilityDone: 0.78,
      transcriptWords: 5,
      correctionPending: false,
    };

    expect(endpointFusionAction({ ...candidate, silenceMs: 99 })).toBe('continue');
    expect(endpointFusionAction({ ...candidate, silenceMs: 100 })).toBe('speculate');
    expect(endpointFusionAction({ ...candidate, silenceMs: 499 })).toBe('speculate');
    expect(endpointFusionAction({ ...candidate, silenceMs: 500 })).toBe('commit');
  });

  it('keeps the captured 431 and 478 ms intra-sentence pauses open', () => {
    noteLiveSttNegotiation('nemotron_parakeet_eou', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
      'partial_transcripts',
      'authoritative_eou',
    ]);

    expect(endpointFusionAction({
      endpointProbability: 1,
      endpointThreshold: 0.5,
      silenceMs: 431,
      transcriptStableMs: 1480,
      semanticProbabilityDone: 0.1,
      transcriptWords: 1,
      correctionPending: false,
    })).toBe('continue');
    expect(endpointFusionAction({
      endpointProbability: 1,
      endpointThreshold: 0.5,
      silenceMs: 478,
      transcriptStableMs: 154,
      semanticProbabilityDone: 0.78,
      transcriptWords: 3,
      correctionPending: false,
    })).toBe('speculate');
  });

  it('uses a 500 ms watchdog when authoritative EOU is negotiated', () => {
    noteLiveSttNegotiation('nemotron_parakeet_eou', [
      'segmented_audio',
      'authoritative_final',
      'result_replay',
      'partial_transcripts',
      'authoritative_eou',
    ]);

    expect(semanticFinalizeDelay('', 'balanced')).toBe(500);
    expect(semanticFinalizeDelay('Where are we going?', 'balanced')).toBe(500);
    expect(semanticFinalizeDelay('We should take the road because', 'balanced')).toBe(500);
  });

  it('commits newly complete delayed text immediately after a mature acoustic pause', () => {
    const delayedComplete = {
      endpointProbability: 0.91,
      endpointThreshold: 0.75,
      semanticProbabilityDone: 0.92,
      transcriptWords: 5,
      correctionPending: false,
    };

    expect(endpointFusionAction({
      ...delayedComplete,
      silenceMs: 900,
      transcriptStableMs: 1,
    })).toBe('commit');
    expect(endpointFusionAction({
      ...delayedComplete,
      silenceMs: 300,
      transcriptStableMs: 1,
    })).toBe('continue');
    expect(endpointFusionAction({
      ...delayedComplete,
      endpointProbability: 0.82,
      silenceMs: 900,
      transcriptStableMs: 1,
    })).toBe('continue');
  });

  it('recovers definitive-statement latency only after long silence and stable text', () => {
    const stableStatement = {
      endpointProbability: 0.85,
      endpointThreshold: 0.75,
      semanticProbabilityDone: 0.78,
      transcriptWords: 4,
      correctionPending: false,
    };

    expect(endpointFusionAction({
      ...stableStatement,
      silenceMs: 650,
      transcriptStableMs: 160,
    })).toBe('commit');
    expect(endpointFusionAction({
      ...stableStatement,
      silenceMs: 400,
      transcriptStableMs: 160,
    })).toBe('speculate');
    expect(endpointFusionAction({
      ...stableStatement,
      silenceMs: 800,
      transcriptStableMs: 21,
    })).toBe('continue');
    expect(endpointFusionAction({
      endpointProbability: 0.968,
      endpointThreshold: 0.75,
      silenceMs: 0,
      transcriptStableMs: 21,
      semanticProbabilityDone: 0.78,
      transcriptWords: 3,
      correctionPending: false,
    })).toBe('continue');
  });

  it('never commits an acoustically strong endpoint while semantics say the clause is unfinished', () => {
    expect(endpointFusionAction({
      endpointProbability: 0.999,
      endpointThreshold: 0.75,
      silenceMs: 1_000,
      transcriptStableMs: 6_000,
      semanticProbabilityDone: 0.18,
      transcriptWords: 4,
      correctionPending: false,
    })).toBe('speculate');
  });

  it('keeps the captured fragmented phrases open until the current clause is actually complete', () => {
    expect(assessSemanticTurn('Hey', 'balanced')).toMatchObject({
      reason: 'insufficient_text',
      recommendedWaitMs: 1_700,
    });
    expect(assessSemanticTurn("What's up? What's happening? What was", 'balanced')).toMatchObject({
      reason: 'unfinished_clause',
      recommendedWaitMs: 1_000,
    });
    expect(assessSemanticTurn('it last? Was it last', 'balanced')).toMatchObject({
      reason: 'unfinished_clause',
      recommendedWaitMs: 1_000,
    });
    expect(assessSemanticTurn('thing? I know how it is.', 'balanced').reason).toBe('definitive_statement');
    expect(semanticFinalizeDelay('Where are we going?', 'balanced')).toBe(220);
  });

  it('removes the controller-created final user row after durable conversation submission', () => {
    liveVoiceTranscriptStore.resetForTests();
    liveVoiceTranscriptStore.write('You', "I'm the one that found you.", 'final');
    const dispose = initializeLiveVoiceTranscriptReconciliation();

    window.dispatchEvent(new CustomEvent(LIVE_COORDINATION_TERMINAL_EVENT, {
      detail: { outcome: 'conversation_submitted' },
    }));

    expect(liveVoiceTranscriptStore.getState().rows).toEqual([]);
    dispose();
  });

  it('keeps draft and non-conversation transcript rows intact', () => {
    liveVoiceTranscriptStore.resetForTests();
    liveVoiceTranscriptStore.write('You', 'submitted final', 'final');
    liveVoiceTranscriptStore.write('Omnix', 'Live voice paused.', 'final');
    liveVoiceTranscriptStore.write('You', 'still speaking', 'draft');

    expect(removeTransientFinalUserRows()).toBe(1);
    expect(liveVoiceTranscriptStore.getState().rows.map((row) => row.text)).toEqual(['Live voice paused.', 'still speaking']);
    expect(liveVoiceTranscriptStore.draftText()).toBe('still speaking');
  });

  it('emits timeline events using the supplied monotonic timestamp', () => {
    const coordinator = new LiveVoiceTurnCoordinator();
    const listener = vi.fn();
    window.addEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, listener);
    coordinator.playbackStarted('voice-turn:play', 440);
    expect(listener).toHaveBeenCalledOnce();
    const detail = (listener.mock.calls[0][0] as CustomEvent<LiveVoiceTurnTimelineDetail>).detail;
    expect(detail).toMatchObject({
      turnId: 'voice-turn:play',
      event: 'playback_started',
      atMs: 440,
      state: 'playing',
    });
    window.removeEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, listener);
  });
});
