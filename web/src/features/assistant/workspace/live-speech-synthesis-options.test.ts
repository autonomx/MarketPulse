 
import { afterEach, describe, expect, it } from 'vitest';

import {
  ACTIVE_PRONUNCIATIONS_KEY,
} from '../chat/livePronunciationClient';
import {
  LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY,
  type LiveConversationProfile,
} from '../chat/liveConversationProfileClient';
import {
  createLiveSpeechSynthesisOptions,
  resetLiveSpeechCueState,
} from './live-speech-synthesis-options';
import { readVocalInteractionState } from './live-voice-performance-behavior';

const profile: LiveConversationProfile = {
  presence_preset: 'natural',
  talkativeness: 50,
  conversation_stance: 'advise',
  conversation_pace: 'reflective',
  interruption_preference: 'balanced',
  assistant_backchannel_mode: 'natural',
  initiative_mode: 'gentle',
  idle_threshold_ms: 15_000,
  long_pause_behavior: 'wait',
  response_length: 'conversational',
  response_onset_style: 'reflective',
  emotional_attunement: 'expressive',
  topic_continuity: 'natural',
  max_idle_prompts: 1,
  duplex_mode: 'echo_aware',
  pronunciation_save_policy: 'ask',
  profile_version: 1,
};

afterEach(() => {
  window.localStorage.clear();
  resetLiveSpeechCueState();
});

describe('explicit live speech synthesis options', () => {
  it('reads the effective profile and pronunciation hints at phrase commit time', () => {
    window.localStorage.setItem(LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY, JSON.stringify(profile));
    window.localStorage.setItem(ACTIVE_PRONUNCIATIONS_KEY, JSON.stringify([
      {
        id: 'p1',
        phrase: 'Omnix',
        pronunciation: 'Om-nicks',
        locale: 'en-US',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]));

    const options = createLiveSpeechSynthesisOptions('I think Omnix should wait.', {
      scopeKey: 'chat:one',
    });

    expect(options.performancePlan).toMatchObject({
      schema_version: 1,
      speech_act: 'instruction',
      pace: 'slightly_slow',
      clause_pause: 'long',
      onset_policy: {
        desired_perceived_onset_ms: 650,
        maximum_additional_delay_ms: 120,
      },
    });
    expect(options.pronunciationLexicon).toEqual([
      { phrase: 'Omnix', pronunciation: 'Om-nicks', locale: 'en-US' },
    ]);
    expect(readVocalInteractionState('chat:one').observationCount).toBe(1);
    expect(readVocalInteractionState('chat:two').observationCount).toBe(0);
  });

  it('applies calibrated uncertainty without changing the synthesis text contract', () => {
    window.localStorage.setItem(LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY, JSON.stringify(profile));

    const options = createLiveSpeechSynthesisOptions('My best estimate is that it could finish tomorrow.');

    expect(options.performancePlan).toMatchObject({
      certainty: 'low',
      pace: 'natural',
    });
    expect(options).not.toHaveProperty('text');
  });

  it('can disable performance and continuity independently while retaining pronunciation hints', () => {
    window.localStorage.setItem(LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY, JSON.stringify(profile));
    window.localStorage.setItem(ACTIVE_PRONUNCIATIONS_KEY, JSON.stringify([
      {
        id: 'p1',
        phrase: 'Omnix',
        pronunciation: 'Om-nicks',
        locale: 'en-US',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]));

    const disabled = createLiveSpeechSynthesisOptions('Omnix should wait.', {
      scopeKey: 'chat:disabled',
      enablePerformancePlan: false,
      enableVocalContinuity: false,
    });
    expect(disabled.performancePlan).toBeUndefined();
    expect(disabled.pronunciationLexicon).toEqual([
      { phrase: 'Omnix', pronunciation: 'Om-nicks', locale: 'en-US' },
    ]);
    expect(readVocalInteractionState('chat:disabled').observationCount).toBe(0);

    const stateless = createLiveSpeechSynthesisOptions('I think Omnix should wait.', {
      scopeKey: 'chat:stateless',
      enableVocalContinuity: false,
    });
    expect(stateless.performancePlan).toMatchObject({ speech_act: 'instruction' });
    expect(readVocalInteractionState('chat:stateless').observationCount).toBe(0);
  });

  it('creates a fallback plan for canonical Chat sessions before the profile cache is mirrored', () => {
    const options = createLiveSpeechSynthesisOptions(
      'I think we should take a moment and look at this carefully.',
      { scopeKey: 'chat:profile-cache-miss' },
    );

    expect(options.performancePlan).toMatchObject({
      schema_version: 1,
      speech_act: 'answer',
      energy: 'moderate',
      warmth: 'moderate',
      pace: 'slightly_slow',
      clause_pause: 'long',
    });
    expect(readVocalInteractionState('chat:profile-cache-miss').observationCount).toBe(1);
  });

  it('returns an empty explicit contract when no profile or pronunciation state is available', () => {
    expect(createLiveSpeechSynthesisOptions('Hello.')).toEqual({});
  });
});
