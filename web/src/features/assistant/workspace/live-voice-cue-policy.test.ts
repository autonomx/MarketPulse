import { describe, expect, it } from 'vitest';

import type { SpeechPerformancePlan } from './live-speech-performance-contract';
import { decideResponseCue, mapBackchannelTokenToCue } from './live-voice-cue-policy';

const plan: SpeechPerformancePlan = {
  schema_version: 1,
  speech_act: 'reflection',
  energy: 'low',
  warmth: 'high',
  certainty: 'moderate',
  pace: 'slightly_slow',
  clause_pause: 'long',
  emphasis: [],
  onset_policy: {
    desired_perceived_onset_ms: 650,
    maximum_additional_delay_ms: 350,
  },
  nonverbal_eligibility: {
    breath: true,
    acknowledgement: true,
    amused_exhale: true,
    sigh: false,
  },
};

describe('live voice cue policy', () => {
  it('uses a subtle hmm only for an eligible reflective opening', () => {
    expect(decideResponseCue('I think the safer option is to wait.', plan, 0, 0, 20_000, 0)).toEqual({
      allowed: true,
      cueId: 'hmm',
      variantId: 'hmm-v1',
      reason: 'eligible_hmm',
    });
    expect(decideResponseCue('I think the next clause is different.', plan, 1, 0, 20_000, 0).reason)
      .toBe('opening_only');
  });

  it('prioritizes amused exhale and falls back to inhale for reassurance', () => {
    expect(decideResponseCue("That's funny, and I think it worked.", plan, 0, 1, 20_000, 0).cueId)
      .toBe('amused_exhale');
    const reassurance = { ...plan, speech_act: 'reassurance' as const };
    expect(decideResponseCue('Take your time.', reassurance, 0, 2, 20_000, 0).cueId)
      .toBe('inhale');
  });

  it('blocks sensitive content and repeated cues', () => {
    expect(decideResponseCue('I think your passcode is 123456.', plan, 0, 0, 20_000, 0).reason)
      .toBe('sensitive_content');
    expect(decideResponseCue('I think this is right.', plan, 0, 0, 20_000, 15_000).reason)
      .toBe('cooldown');
  });

  it('maps listener tokens onto the bounded cue set', () => {
    expect(mapBackchannelTokenToCue('mhm')).toBe('mhm');
    expect(mapBackchannelTokenToCue('right')).toBe('hmm');
    expect(mapBackchannelTokenToCue("i'm with you")).toBe('hmm');
  });
});
