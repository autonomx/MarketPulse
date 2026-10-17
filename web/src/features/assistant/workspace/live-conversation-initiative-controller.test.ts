import { describe, expect, it } from 'vitest';

import {
  parseProactiveSse,
  proactiveReasonFromTranscript,
  resolveInitiativePolicyTiming,
} from './live-conversation-initiative-controller';

describe('live conversation initiative controller', () => {
  it('parses transient proactive stream metadata and content', () => {
    const parsed = parseProactiveSse([
      'data: {"type":"initiative","turn_id":"proactive:one","initiative_reason":"unresolved_question"}',
      '',
      'data: {"type":"complete","content":"Want to keep working through that?","metadata":{"turn_id":"proactive:one"}}',
      '',
      'data: {"type":"done"}',
      '',
    ].join('\n'));

    expect(parsed).toEqual({
      turnId: 'proactive:one',
      content: 'Want to keep working through that?',
      initiativeReason: 'unresolved_question',
    });
  });

  it('surfaces server errors instead of silently discarding them', () => {
    expect(() => parseProactiveSse('data: {"type":"error","message":"Provider unavailable"}\n\n'))
      .toThrow('Provider unavailable');
  });

  it('derives only context-backed initiative reasons from authoritative transcript text', () => {
    expect(proactiveReasonFromTranscript('Should we revisit the launch plan?')).toBe('unresolved_question');
    expect(proactiveReasonFromTranscript('The launch plan is still open.')).toBe('continue_current_topic');
    expect(proactiveReasonFromTranscript('')).toBeNull();
  });

  it('keeps the explicit profile idle delay while applying other active policy timing', () => {
    expect(resolveInitiativePolicyTiming(12_000, {
      silence_tolerance_ms: 16_000,
      initiative_threshold_ms: 20_000,
      initiative_cooldown_ms: 50_000,
      listener_backchannel_frequency: 0.14,
      typical_turn_words: 65,
      interruption_sensitivity: 0.74,
      response_onset_ms: 450,
    })).toEqual({
      idleThresholdMs: 12_000,
      cooldownMs: 50_000,
      typicalTurnWords: 65,
      responseOnsetMs: 450,
    });
  });

  it('preserves profile timing when no server policy is available', () => {
    expect(resolveInitiativePolicyTiming(18_000, null)).toEqual({
      idleThresholdMs: 18_000,
      cooldownMs: 30_000,
      typicalTurnWords: null,
      responseOnsetMs: null,
    });
  });
});
