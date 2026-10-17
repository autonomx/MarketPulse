import { describe, expect, it } from 'vitest';

import {
  AdaptiveTtsBufferPolicy,
  LiveTtsAncillaryCancellationGuard,
  adaptiveBufferWorkletMessage,
} from './live-tts-adaptive-buffer-controller';

describe('adaptive live TTS buffering', () => {
  it('starts with a 120 ms reserve across the first two Qwen frames at 24 kHz', () => {
    const policy = new AdaptiveTtsBufferPolicy();
    const snapshot = policy.snapshot();

    expect(snapshot).toMatchObject({
      startBufferMs: 120,
      rebufferMs: 520,
      stableTurns: 0,
      underrunTurns: 0,
    });
    expect(adaptiveBufferWorkletMessage(snapshot, 24_000)).toMatchObject({
      startBufferSamples: 2_880,
      minimumBufferedSpeechSamples: 2_880,
      rebufferSamples: 12_480,
    });
  });

  it('immediately restores a larger safety reserve after an underrun', () => {
    const policy = new AdaptiveTtsBufferPolicy();

    expect(policy.observeWorkletEvent('underrun')).toMatchObject({
      startBufferMs: 190,
      rebufferMs: 630,
    });
    expect(policy.observeWorkletEvent('drained')).toMatchObject({
      startBufferMs: 190,
      rebufferMs: 630,
      stableTurns: 0,
      underrunTurns: 1,
    });
  });

  it('never decays below the safe startup floor', () => {
    const policy = new AdaptiveTtsBufferPolicy();

    policy.observeWorkletEvent('drained');
    policy.observeWorkletEvent('drained');
    expect(policy.snapshot().startBufferMs).toBe(120);

    expect(policy.observeWorkletEvent('drained')).toMatchObject({
      startBufferMs: 120,
      rebufferMs: 490,
      stableTurns: 0,
    });
  });

  it('clamps a sub-frame reserve to the 120 ms floor', () => {
    const policy = new AdaptiveTtsBufferPolicy({
      startBufferMs: 60,
      rebufferMs: 490,
    });

    expect(policy.snapshot()).toMatchObject({
      startBufferMs: 120,
      rebufferMs: 490,
    });
  });

  it('uses persistent-session idle events as stable turn completions', () => {
    const policy = new AdaptiveTtsBufferPolicy({
      startBufferMs: 230,
      rebufferMs: 630,
    });

    policy.observeWorkletEvent('idle');
    policy.observeWorkletEvent('idle');
    expect(policy.snapshot()).toMatchObject({
      startBufferMs: 230,
      rebufferMs: 630,
      stableTurns: 2,
    });
    expect(policy.observeWorkletEvent('idle')).toMatchObject({
      startBufferMs: 200,
      rebufferMs: 600,
      stableTurns: 0,
    });
  });

  it('cancels queued unowned pauses when a turn is interrupted', () => {
    const guard = new LiveTtsAncillaryCancellationGuard();

    expect(guard.handleOutbound({
      type: 'push_segment_silence',
      segmentId: 'silence-old-reflection',
      durationSamples: 7_200,
      reason: 'reflection',
    })).toEqual({ forward: true, cancelSegmentIds: [] });
    expect(guard.handleOutbound({
      type: 'push_segment_samples',
      segmentId: 'cue-old-hmm',
      segmentKind: 'cue',
      samples: new Float32Array([0.1]),
    })).toEqual({ forward: true, cancelSegmentIds: [] });

    expect(guard.handleOutbound({
      type: 'cancel_output',
      outputId: 'old-output',
      generationEpoch: 6,
      reason: 'voice-interrupt',
    })).toEqual({
      forward: true,
      cancelSegmentIds: ['silence-old-reflection', 'cue-old-hmm'],
      reason: 'voice-interrupt',
    });
  });

  it('drops late stale pauses until the replacement turn establishes a new start policy', () => {
    const guard = new LiveTtsAncillaryCancellationGuard();

    guard.handleOutbound({
      type: 'cancel_output',
      outputId: 'old-output',
      generationEpoch: 6,
      reason: 'superseded-by-real-response',
    });

    expect(guard.handleOutbound({
      type: 'push_segment_silence',
      segmentId: 'silence-late',
      durationSamples: 6_000,
    })).toEqual({
      forward: false,
      cancelSegmentIds: [],
      reason: 'superseded-unowned-ancillary',
    });

    expect(guard.handleOutbound({
      type: 'set_start_policy',
      notBeforeRenderSample: 0,
      minimumBufferedSpeechSamples: 1_920,
    })).toEqual({ forward: true, cancelSegmentIds: [] });
    expect(guard.handleOutbound({
      type: 'push_segment_silence',
      segmentId: 'silence-new-turn',
      durationSamples: 1_920,
    })).toEqual({ forward: true, cancelSegmentIds: [] });
  });

  it('preserves ancillary audio for non-terminal selective cancellation', () => {
    const guard = new LiveTtsAncillaryCancellationGuard();

    guard.handleOutbound({
      type: 'push_segment_silence',
      segmentId: 'silence-preserved',
      durationSamples: 1_920,
    });
    expect(guard.handleOutbound({
      type: 'cancel_output',
      outputId: 'self-corrected-output',
      generationEpoch: 4,
      reason: 'self_corrected',
    })).toEqual({ forward: true, cancelSegmentIds: [] });
    expect(guard.handleOutbound({
      type: 'push_segment_silence',
      segmentId: 'silence-still-allowed',
      durationSamples: 1_920,
    })).toEqual({ forward: true, cancelSegmentIds: [] });
  });
});
