 
import { beforeEach, describe, expect, it } from 'vitest';

import type { LiveConversationProfile } from '../chat/liveConversationProfileClient';
import type { SpeechPerformancePlan } from './live-speech-performance-contract';
import {
  createVocalInteractionState,
  decayVocalInteractionState,
  humanizeSpeechPerformance,
  planMeaningfulSpeechPerformance,
  readVocalInteractionState,
  resetVocalInteractionState,
} from './live-voice-performance-behavior';

const profile: LiveConversationProfile = {
  presence_preset: 'natural',
  talkativeness: 50,
  conversation_stance: 'discuss',
  conversation_pace: 'balanced',
  interruption_preference: 'balanced',
  assistant_backchannel_mode: 'natural',
  initiative_mode: 'gentle',
  idle_threshold_ms: 15_000,
  long_pause_behavior: 'wait',
  response_length: 'conversational',
  response_onset_style: 'adaptive',
  emotional_attunement: 'subtle',
  topic_continuity: 'natural',
  max_idle_prompts: 1,
  duplex_mode: 'echo_aware',
  pronunciation_save_policy: 'ask',
  profile_version: 1,
};

const plan: SpeechPerformancePlan = {
  schema_version: 1,
  speech_act: 'answer',
  energy: 'moderate',
  warmth: 'moderate',
  certainty: 'high',
  pace: 'natural',
  clause_pause: 'medium',
  emphasis: [],
  onset_policy: {
    desired_perceived_onset_ms: 450,
    maximum_additional_delay_ms: 350,
  },
  nonverbal_eligibility: {
    breath: true,
    acknowledgement: true,
    amused_exhale: false,
    sigh: false,
  },
};

beforeEach(() => {
  resetVocalInteractionState();
});

describe('meaningful live voice performance behavior', () => {
  it('maintains bounded delivery continuity and decays it toward neutral', () => {
    const seriousPlan: SpeechPerformancePlan = {
      ...plan,
      speech_act: 'reassurance',
      energy: 'low',
      warmth: 'high',
      clause_pause: 'long',
    };
    const observed = planMeaningfulSpeechPerformance(
      'I am sorry. Take your time.',
      seriousPlan,
      profile,
      createVocalInteractionState(0),
      1_000,
    );

    expect(observed.state.warmth).toBeGreaterThan(0.5);
    expect(observed.state.tension).toBeGreaterThan(0);
    expect(observed.plan.warmth).toBe('high');

    const decayed = decayVocalInteractionState(observed.state, 91_000);
    expect(decayed.tension).toBeLessThan(observed.state.tension);
    expect(Math.abs(decayed.warmth - 0.5)).toBeLessThan(Math.abs(observed.state.warmth - 0.5));
  });

  it('recognizes a correction only when prior context and a real revision marker exist', () => {
    const initial = createVocalInteractionState(0);
    const opening = planMeaningfulSpeechPerformance(
      'Actually, this is the first point.',
      plan,
      profile,
      initial,
      1_000,
    );
    expect(opening.behavior.genuineSelfCorrection).toBe(false);

    const conversationalActually = planMeaningfulSpeechPerformance(
      'Actually, that worked perfectly.',
      plan,
      profile,
      opening.state,
      1_500,
    );
    expect(conversationalActually.behavior.genuineSelfCorrection).toBe(false);

    const correction = planMeaningfulSpeechPerformance(
      'Actually, not the first approach—the second is safer.',
      plan,
      profile,
      conversationalActually.state,
      2_000,
    );
    expect(correction.behavior.genuineSelfCorrection).toBe(true);
    expect(correction.plan.pace).toBe('slightly_slow');
    expect(correction.plan.clause_pause).toBe('long');
    expect(correction.plan.certainty).toBe('moderate');
  });

  it('uses semantic uncertainty without inserting filler or mutating the source plan', () => {
    const original = structuredClone(plan);
    const result = planMeaningfulSpeechPerformance(
      'My best estimate is that it could finish tomorrow.',
      plan,
      profile,
      createVocalInteractionState(0),
      1_000,
    );

    expect(result.behavior.calibratedUncertainty).toBe(true);
    expect(result.plan.certainty).toBe('low');
    expect(result.plan.pace).toBe('natural');
    expect(plan).toEqual(original);
  });

  it('bounds character habits with an observation cooldown', () => {
    const advisingProfile: LiveConversationProfile = {
      ...profile,
      conversation_stance: 'advise',
    };
    const instructionPlan: SpeechPerformancePlan = {
      ...plan,
      speech_act: 'instruction',
    };
    let state = createVocalInteractionState(0);

    const first = planMeaningfulSpeechPerformance('Start with the smaller change.', instructionPlan, advisingProfile, state, 1_000);
    state = first.state;
    const second = planMeaningfulSpeechPerformance('Then measure the result.', instructionPlan, advisingProfile, state, 2_000);
    state = second.state;
    const third = planMeaningfulSpeechPerformance('Keep the rollout reversible.', instructionPlan, advisingProfile, state, 3_000);
    state = third.state;
    const fourth = planMeaningfulSpeechPerformance('Finally, expand only if it works.', instructionPlan, advisingProfile, state, 4_000);

    expect(first.behavior.habit).toBe('direct_opening');
    expect(second.behavior.habit).toBe('none');
    expect(third.behavior.habit).toBe('none');
    expect(fourth.behavior.habit).toBe('direct_opening');
    expect(first.plan.onset_policy.desired_perceived_onset_ms).toBe(320);
  });

  it('keeps unrelated session scopes isolated and resets on session changes', () => {
    humanizeSpeechPerformance('I think this is safer.', plan, profile, 'chat:one', 1_000);
    humanizeSpeechPerformance('Maybe it changes.', plan, profile, 'chat:one', 2_000);
    humanizeSpeechPerformance('A separate answer.', plan, profile, 'chat:two', 2_000);

    expect(readVocalInteractionState('chat:one').observationCount).toBe(2);
    expect(readVocalInteractionState('chat:two').observationCount).toBe(1);
    expect(readVocalInteractionState('default').observationCount).toBe(0);

    window.dispatchEvent(new Event('omnix:live-chat-session-changed'));
    expect(readVocalInteractionState('chat:one').observationCount).toBe(0);
    expect(readVocalInteractionState('chat:two').observationCount).toBe(0);
  });

  it('publishes content-free diagnostics and resets state at call start', () => {
    const events: Array<Record<string, unknown>> = [];
    const listener: EventListener = (event) => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener('omnix:live-voice-performance-behavior', listener);

    humanizeSpeechPerformance('I think this is the safer option.', plan, profile, 'chat:test', 1_000);
    expect(readVocalInteractionState('chat:test').observationCount).toBe(1);
    expect(events.at(-1)).toMatchObject({
      scope_key: 'chat:test',
      reflective: true,
      canonical_text_modified: false,
      observation_count: 1,
    });

    window.dispatchEvent(new Event('omnix:assistant-live-voice-call-start'));
    expect(readVocalInteractionState('chat:test').observationCount).toBe(0);
    window.removeEventListener('omnix:live-voice-performance-behavior', listener);
  });
});
