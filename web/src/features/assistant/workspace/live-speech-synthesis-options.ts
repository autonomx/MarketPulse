 
import {
  readEffectiveLiveConversationProfile,
  type LiveConversationProfile,
} from '../chat/liveConversationProfileClient';
import { readActivePronunciations } from '../chat/livePronunciationClient';
import { createSpeechDeliveryPlan } from './live-speech-delivery-plan';
import type {
  SpeechPerformancePlan,
  SpeechSynthesisOptions,
} from './live-speech-performance-contract';
import { decideResponseCue, type ResponseCueDecision } from './live-voice-cue-policy';
import {
  humanizeSpeechPerformance,
  resetVocalInteractionState,
} from './live-voice-performance-behavior';
import { ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, LIVE_CHAT_SESSION_CHANGED_EVENT } from '../../../events/bus';


const FALLBACK_LIVE_CONVERSATION_PROFILE: Readonly<LiveConversationProfile> = {
  presence_preset: 'natural',
  talkativeness: 50,
  conversation_stance: 'automatic',
  conversation_pace: 'balanced',
  interruption_preference: 'balanced',
  assistant_backchannel_mode: 'off',
  initiative_mode: 'gentle',
  idle_threshold_ms: 15_000,
  long_pause_behavior: 'wait',
  response_length: 'conversational',
  response_onset_style: 'adaptive',
  emotional_attunement: 'subtle',
  topic_continuity: 'natural',
  max_idle_prompts: 1,
  duplex_mode: 'automatic',
  pronunciation_save_policy: 'ask',
  profile_version: 1,
};

export type LiveSpeechSynthesisPlanningOptions = {
  scopeKey?: string;
  enablePerformancePlan?: boolean;
  enableVocalContinuity?: boolean;
};

let responseCueSequence = 0;
let lastResponseCueAt = 0;
let resetListenersInstalled = false;

export function createLiveSpeechSynthesisOptions(
  text: string,
  options: LiveSpeechSynthesisPlanningOptions = {},
): SpeechSynthesisOptions {
  installResetListeners();
  const profile = readEffectiveLiveConversationProfile()
    ?? fallbackProfileForCanonicalChatScope(options.scopeKey);
  const performanceEnabled = options.enablePerformancePlan ?? true;
  const continuityEnabled = options.enableVocalContinuity ?? true;
  const serious = /\b(?:sorry|grief|loss|afraid|hurt|serious|take your time)\b/i.test(text);
  const basePerformancePlan = profile && performanceEnabled
    ? createSpeechDeliveryPlan(text, profile, serious)
    : undefined;
  const performancePlan = basePerformancePlan && profile && continuityEnabled
    ? humanizeSpeechPerformance(
      text,
      basePerformancePlan,
      profile,
      options.scopeKey ?? 'default',
    ).plan
    : basePerformancePlan;
  const pronunciationLexicon = readActivePronunciations().map((entry) => ({
    phrase: entry.phrase,
    pronunciation: entry.pronunciation,
    locale: entry.locale,
  }));
  return {
    ...(performancePlan ? { performancePlan } : {}),
    ...(pronunciationLexicon.length ? { pronunciationLexicon } : {}),
  };
}

export function selectLiveResponseCue(
  text: string,
  plan: SpeechPerformancePlan | undefined,
  phraseIndex: number,
  now = Date.now(),
): ResponseCueDecision {
  installResetListeners();
  const decision = decideResponseCue(
    text,
    plan,
    phraseIndex,
    responseCueSequence,
    now,
    lastResponseCueAt,
  );
  if (decision.allowed) {
    responseCueSequence += 1;
    lastResponseCueAt = now;
  }
  return decision;
}

export function resetLiveSpeechCueState(): void {
  responseCueSequence = 0;
  lastResponseCueAt = 0;
  resetVocalInteractionState();
}

function fallbackProfileForCanonicalChatScope(scopeKey: string | undefined): LiveConversationProfile | null {
  // Persisted Chat sessions use the `chat:` namespace. Delivery planning must
  // remain available even when the settings panel has not mirrored the server
  // profile into localStorage yet.
  return scopeKey?.startsWith('chat:')
    ? { ...FALLBACK_LIVE_CONVERSATION_PROFILE }
    : null;
}

function installResetListeners(): void {
  if (resetListenersInstalled || typeof window === 'undefined') return;
  resetListenersInstalled = true;
  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, resetLiveSpeechCueState);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, resetLiveSpeechCueState);
  window.addEventListener(LIVE_CHAT_SESSION_CHANGED_EVENT, resetLiveSpeechCueState);
}
