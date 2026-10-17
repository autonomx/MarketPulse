 
import type { LiveConversationProfile } from '../chat/liveConversationProfileClient';
import {
  SPEECH_PERFORMANCE_SCHEMA_VERSION,
  type SpeechPerformancePlan,
} from './live-speech-performance-contract';

export type SpeechDeliveryPlan = SpeechPerformancePlan;

export function createSpeechDeliveryPlan(
  text: string,
  profile: LiveConversationProfile,
  serious = false,
): SpeechDeliveryPlan {
  const normalized = text.trim();
  const lower = normalized.toLocaleLowerCase();
  let speechAct: SpeechDeliveryPlan['speech_act'] = normalized.endsWith('?') ? 'question' : 'answer';
  if (/\b(?:i understand|that sounds|take your time|i'm sorry|i am sorry)\b/.test(lower)) speechAct = 'reassurance';
  else if (profile.conversation_stance === 'listen') speechAct = 'reflection';
  else if (profile.conversation_stance === 'teach' || profile.conversation_stance === 'advise') speechAct = 'instruction';
  else if (normalized.split(/\s+/).filter(Boolean).length <= 4) speechAct = 'acknowledgement';

  const reflective = serious || speechAct === 'reassurance' || speechAct === 'reflection';
  const onsetStyle = profile.response_onset_style;
  return {
    schema_version: SPEECH_PERFORMANCE_SCHEMA_VERSION,
    speech_act: speechAct,
    energy: reflective ? 'low' : profile.presence_preset === 'engaged' ? 'high' : 'moderate',
    warmth: serious || profile.emotional_attunement === 'expressive' ? 'high' : profile.emotional_attunement === 'off' ? 'low' : 'moderate',
    certainty: /\b(?:maybe|perhaps|might|not sure|uncertain)\b/.test(lower) ? 'low' : speechAct === 'answer' || speechAct === 'instruction' ? 'high' : 'moderate',
    pace: serious || profile.response_length === 'detailed' ? 'slightly_slow' : profile.conversation_pace === 'quick' ? 'slightly_fast' : 'natural',
    clause_pause: serious ? 'long' : speechAct === 'acknowledgement' ? 'short' : 'medium',
    emphasis: normalized.split(/\s+/).map((word) => word.replace(/[.,!?;:]/g, '')).filter((word) => word.length > 1 && word === word.toLocaleUpperCase()).slice(0, 6),
    onset_policy: {
      // STT finalization and first-token generation already provide a natural
      // conversational gap. Keep only a small adaptive/reflective allowance so
      // accepted speculative text or PCM is not deliberately held for 450 ms.
      desired_perceived_onset_ms: onsetStyle === 'immediate'
        ? 0
        : onsetStyle === 'reflective' ? 280 : 120,
      maximum_additional_delay_ms: onsetStyle === 'immediate'
        ? 0
        : onsetStyle === 'reflective' ? 120 : 80,
    },
    nonverbal_eligibility: {
      breath: profile.emotional_attunement !== 'off',
      acknowledgement: profile.assistant_backchannel_mode !== 'off',
      amused_exhale: profile.emotional_attunement === 'expressive' && !serious,
      sigh: profile.emotional_attunement === 'expressive' && serious,
    },
  };
}

export function applyDeliveryPlanToTtsRequest(
  payload: Record<string, unknown>,
  plan: SpeechDeliveryPlan,
): Record<string, unknown> {
  return {
    ...payload,
    delivery_plan: plan,
  };
}
