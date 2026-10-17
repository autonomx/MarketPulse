import type { SpeechPerformancePlan } from './live-speech-performance-contract';
import { cueVariantId, type LiveVoiceCueId } from './live-voice-cue-bank';

const RESPONSE_CUE_COOLDOWN_MS = 12_000;
const SENSITIVE_PATTERN = /\b(?:password|passcode|pin|account|card number|security code|address|phone number|email address|diagnosis|emergency)\b|\b\d{4,}\b/i;
const AMUSED_PATTERN = /\b(?:funny|amusing|hilarious|made me laugh|that's good|that is good)\b/i;
const REFLECTIVE_PATTERN = /\b(?:i think|perhaps|maybe|on balance|the tradeoff|looking at this|let me think)\b/i;

export type ResponseCueDecision = {
  allowed: boolean;
  cueId: LiveVoiceCueId | null;
  variantId: string | null;
  reason: string;
};

export function decideResponseCue(
  text: string,
  plan: SpeechPerformancePlan | undefined,
  phraseIndex: number,
  sequence: number,
  now = Date.now(),
  lastCueAt = 0,
): ResponseCueDecision {
  const normalized = text.trim();
  if (!plan) return denied('no_performance_plan');
  if (phraseIndex !== 0) return denied('opening_only');
  if (!normalized) return denied('empty_text');
  if (SENSITIVE_PATTERN.test(normalized)) return denied('sensitive_content');
  if (now - lastCueAt < RESPONSE_CUE_COOLDOWN_MS) return denied('cooldown');

  let cueId: LiveVoiceCueId | null = null;
  if (plan.nonverbal_eligibility.amused_exhale && AMUSED_PATTERN.test(normalized)) {
    cueId = 'amused_exhale';
  } else if (
    plan.nonverbal_eligibility.acknowledgement
    && plan.speech_act === 'reflection'
    && REFLECTIVE_PATTERN.test(normalized)
  ) {
    cueId = 'hmm';
  } else if (
    plan.nonverbal_eligibility.breath
    && (plan.speech_act === 'reassurance' || plan.clause_pause === 'long')
  ) {
    cueId = 'inhale';
  }
  if (!cueId) return denied('not_semantically_eligible');
  return {
    allowed: true,
    cueId,
    variantId: cueVariantId(cueId, sequence),
    reason: `eligible_${cueId}`,
  };
}

export function mapBackchannelTokenToCue(token: string): LiveVoiceCueId {
  return token.trim().toLocaleLowerCase() === 'mhm' ? 'mhm' : 'hmm';
}

function denied(reason: string): ResponseCueDecision {
  return { allowed: false, cueId: null, variantId: null, reason };
}
