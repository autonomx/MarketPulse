 
import {
  readEffectiveLiveConversationProfile,
  type AssistantBackchannelMode,
} from '../chat/liveConversationProfileClient';
import { liveConversationStore } from './live-conversation-store';
import { cueVariantId } from './live-voice-cue-bank';
import { mapBackchannelTokenToCue } from './live-voice-cue-policy';
import { readLiveVoiceHumanizationFlags } from './live-voice-humanization-flags';
import { playLowLatencyVoiceCue, stopLowLatencyVoiceCue } from './live-voice-cue-player';
import { ASSISTANT_AUDIO_DUCK_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_CHAT_SESSION_CHANGED_EVENT, LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT, LIVE_CONVERSATION_USER_CONTINUER_EVENT } from '../../../events/bus';

const BASE_COOLDOWN_MS = 8_000;
const BASE_SPEECH_MS = 3_500;
const DEFAULT_FREQUENCY = 0.16;
const SENSITIVE_PATTERN = /\b(?:password|passcode|pin|account|card number|security code|address|phone number|email address)\b|\b\d{4,}\b/i;
const QUESTION_OR_CORRECTION_PATTERN = /[?？]|\b(?:no|not|wrong|actually|correction|I meant|did you say)\b/i;
const CONTINUER_PATTERN = /^(?:m+h+m+|mhm+|uh[ -]?huh|yeah|yep|right|okay|ok|got it|sure|I see|mm+)[.!\s-]*$/i;

export type BackchannelToken = 'mhm' | 'right' | 'okay' | "i'm with you";
export type AssistantBackchannelDecision = { allowed: boolean; token: BackchannelToken | null; reason: string };
export type BackchannelCadence = { speechMs: number; cooldownMs: number; enabled: boolean };

let initialized = false;
let selectedSessionId: string | null = null;
let lastPlayedAt = 0;
let naturalIndex = 0;
let speechTimer: ReturnType<typeof setTimeout> | null = null;
let restoreTimer: ReturnType<typeof setTimeout> | null = null;

export function isUserContinuer(transcript: string): boolean {
  return CONTINUER_PATTERN.test(transcript.trim());
}

export function resolveBackchannelCadence(frequency = DEFAULT_FREQUENCY): BackchannelCadence {
  const bounded = Math.max(0, Math.min(1, Number.isFinite(frequency) ? frequency : DEFAULT_FREQUENCY));
  if (bounded === 0) return { speechMs: 8_000, cooldownMs: 30_000, enabled: false };
  const delta = DEFAULT_FREQUENCY - bounded;
  return {
    speechMs: Math.round(Math.max(2_500, Math.min(8_000, BASE_SPEECH_MS + delta * 10_000))),
    cooldownMs: Math.round(Math.max(6_000, Math.min(30_000, BASE_COOLDOWN_MS + delta * 60_000))),
    enabled: true,
  };
}

export function decideAssistantListenerBackchannel(
  transcript: string,
  mode: AssistantBackchannelMode,
  speechDurationMs: number,
  now = Date.now(),
  lastAt = lastPlayedAt,
  duplexMode: string = liveConversationStore.getState().duplex.resolvedMode,
  frequency = DEFAULT_FREQUENCY,
): AssistantBackchannelDecision {
  const text = transcript.trim();
  const cadence = resolveBackchannelCadence(frequency);
  if (mode === 'off' || !cadence.enabled) return denied('disabled');
  if (duplexMode !== 'echo_aware') return denied('requires_echo_aware_duplex');
  if (speechDurationMs < cadence.speechMs) return denied('speech_too_short');
  if (now - lastAt < cadence.cooldownMs) return denied('cooldown');
  if (!text) return denied('no_partial_transcript');
  if (SENSITIVE_PATTERN.test(text)) return denied('sensitive_dictation');
  if (QUESTION_OR_CORRECTION_PATTERN.test(text)) return denied('semantic_risk');
  if (!/[,.!;:]\s*$/.test(text) && text.split(/\s+/).length < 12) return denied('no_safe_clause_boundary');
  if (mode === 'minimal') return { allowed: true, token: 'mhm', reason: 'minimal' };
  const tokens: BackchannelToken[] = ['mhm', 'right', 'okay', "i'm with you"];
  return { allowed: true, token: tokens[naturalIndex % tokens.length], reason: 'natural' };
}

export function resolveBackchannelTranscript(detailTranscript: unknown): string {
  if (typeof detailTranscript === 'string' && detailTranscript.trim()) return detailTranscript.trim();
  const transcript = liveConversationStore.getState().transcript;
  return (transcript.partial || transcript.lastFinal).trim();
}

export function listenerBackchannelsRolloutEnabled(): boolean {
  const flags = readLiveVoiceHumanizationFlags();
  return flags.master && flags.listenerCues;
}

export function initializeEphemeralBackchannels(): () => void {
  if (initialized || typeof window === 'undefined') return () => undefined;
  initialized = true;
  selectedSessionId = liveConversationStore.getState().sessionId;

  const handleSession = (event: Event) => {
    const detail = (event as CustomEvent<{ sessionId?: unknown }>).detail;
    if (typeof detail?.sessionId === 'string') selectedSessionId = detail.sessionId;
  };
  const handlePerf = (event: Event) => {
    const detail = (event as CustomEvent<{ stage?: unknown; intent?: unknown; transcript?: unknown }>).detail;
    if (detail?.stage !== 'overlap_classified' || detail.intent !== 'backchannel') return;
    emitOmnixEvent(LIVE_CONVERSATION_USER_CONTINUER_EVENT, { transcript: resolveBackchannelTranscript(detail.transcript), action: 'continue' });
  };
  const handleUserSpeech = () => {
    clearSpeechTimer();
    if (!listenerBackchannelsRolloutEnabled()) return;
    const startedAt = performance.now();
    const runtime = liveConversationStore.getState();
    const profile = runtime.profile ?? readEffectiveLiveConversationProfile();
    const policy = runtime.presencePolicy;
    const frequency = policy && policy.preset === profile?.presence_preset
      ? policy.values.listener_backchannel_frequency
      : DEFAULT_FREQUENCY;
    const cadence = resolveBackchannelCadence(frequency);
    if (!cadence.enabled) return;
    speechTimer = setTimeout(() => {
      if (!listenerBackchannelsRolloutEnabled()) return;
      const current = liveConversationStore.getState();
      const currentProfile = current.profile ?? readEffectiveLiveConversationProfile();
      const currentPolicy = current.presencePolicy;
      const currentFrequency = currentPolicy && currentPolicy.preset === currentProfile?.presence_preset
        ? currentPolicy.values.listener_backchannel_frequency
        : frequency;
      const decision = decideAssistantListenerBackchannel(
        resolveBackchannelTranscript(undefined),
        currentProfile?.assistant_backchannel_mode ?? 'off',
        performance.now() - startedAt,
        Date.now(),
        lastPlayedAt,
        current.duplex.resolvedMode,
        currentFrequency,
      );
      if (decision.allowed && decision.token && selectedSessionId && !assistantIsSpeaking()) {
        void playCharacterBackchannel(selectedSessionId, decision.token);
      }
    }, cadence.speechMs);
  };
  const cancel = () => {
    clearSpeechTimer();
    stopLowLatencyVoiceCue('backchannel_cancelled');
    restoreOutput('cancelled');
  };

  window.addEventListener(LIVE_CHAT_SESSION_CHANGED_EVENT, handleSession);
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
  window.addEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, cancel);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, cancel);

  return () => {
    window.removeEventListener(LIVE_CHAT_SESSION_CHANGED_EVENT, handleSession);
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
    window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, cancel);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, cancel);
    cancel();
    initialized = false;
  };
}

async function playCharacterBackchannel(sessionId: string, token: BackchannelToken): Promise<void> {
  lastPlayedAt = Date.now();
  const sequence = naturalIndex;
  naturalIndex += 1;
  const cueId = mapBackchannelTokenToCue(token);
  const variantId = cueVariantId(cueId, sequence);
  emitOmnixEvent(ASSISTANT_AUDIO_DUCK_EVENT, { gain: 0.35, reason: 'listener-backchannel' });
  try {
    const played = await playLowLatencyVoiceCue(cueId, variantId, 0.68);
    if (played) {
      emitOmnixEvent(LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT, { sessionId, token, cueId, variantId, playedAt: Date.now() });
    }
  } finally {
    if (restoreTimer) clearTimeout(restoreTimer);
    restoreTimer = setTimeout(() => restoreOutput('listener-backchannel-complete'), 250);
  }
}

function assistantIsSpeaking(): boolean {
  const conversation = liveConversationStore.getState().conversation;
  return conversation.assistantTurn === 'speaking' || conversation.delivery === 'audio_started';
}

function clearSpeechTimer(): void {
  if (speechTimer) clearTimeout(speechTimer);
  speechTimer = null;
}

function restoreOutput(reason: string): void {
  if (restoreTimer) clearTimeout(restoreTimer);
  restoreTimer = null;
  emitOmnixEvent(ASSISTANT_AUDIO_DUCK_EVENT, { gain: 1, reason });
}

function denied(reason: string): AssistantBackchannelDecision {
  return { allowed: false, token: null, reason };
}

if (typeof window !== 'undefined') initializeEphemeralBackchannels();
