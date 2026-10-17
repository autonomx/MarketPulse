 
import { describe, expect, it } from 'vitest';

import type { LiveConversationProfile } from '../chat/liveConversationProfileClient';
import { applyDeliveryPlanToTtsRequest, createSpeechDeliveryPlan } from './live-speech-delivery-plan';

const profile: LiveConversationProfile = {
  presence_preset: 'engaged', talkativeness: 70, conversation_stance: 'listen', conversation_pace: 'balanced',
  interruption_preference: 'balanced', assistant_backchannel_mode: 'natural', initiative_mode: 'gentle',
  idle_threshold_ms: 15000, long_pause_behavior: 'wait', response_length: 'conversational',
  response_onset_style: 'adaptive', emotional_attunement: 'expressive', topic_continuity: 'natural',
  max_idle_prompts: 1, duplex_mode: 'echo_aware', pronunciation_save_policy: 'ask', profile_version: 1,
};

describe('live speech delivery plan', () => {
  it('creates a restrained, warm plan for serious listening', () => {
    const plan = createSpeechDeliveryPlan("I'm sorry. Take your time.", profile, true);
    expect(plan.schema_version).toBe(1);
    expect(plan.speech_act).toBe('reassurance');
    expect(plan.energy).toBe('low');
    expect(plan.warmth).toBe('high');
    expect(plan.pace).toBe('slightly_slow');
    expect(plan.nonverbal_eligibility.sigh).toBe(true);
  });

  it('keeps adaptive onset below the first-audio latency budget', () => {
    const plan = createSpeechDeliveryPlan('Let us start with the practical answer.', profile, false);

    expect(plan.onset_policy).toEqual({
      desired_perceived_onset_ms: 120,
      maximum_additional_delay_ms: 80,
    });
  });

  it('removes deliberate onset delay for immediate profiles', () => {
    const immediateProfile: LiveConversationProfile = {
      ...profile,
      response_onset_style: 'immediate',
    };
    const plan = createSpeechDeliveryPlan('Yes, exactly.', immediateProfile, false);

    expect(plan.onset_policy).toEqual({
      desired_perceived_onset_ms: 0,
      maximum_additional_delay_ms: 0,
    });
  });

  it('attaches the plan without using sampling controls as emotion proxies', () => {
    const engagedProfile: LiveConversationProfile = { ...profile, conversation_stance: 'discuss' };
    const plan = createSpeechDeliveryPlan('That is GREAT news!', engagedProfile, false);
    const request = applyDeliveryPlanToTtsRequest({
      type: 'synthesize', text: 'That is GREAT news!', temperature: 0.6, top_p: 0.85, repetition_penalty: 1,
    }, plan);
    expect(request.text).toBe('That is GREAT news!');
    expect(plan.energy).toBe('high');
    expect(request.temperature).toBe(0.6);
    expect(request.top_p).toBe(0.85);
    expect(request.repetition_penalty).toBe(1);
    expect(request.delivery_plan).toEqual(plan);
  });
});
