 
import { readEffectiveLiveConversationProfile, type LiveConversationProfile } from '../chat/liveConversationProfileClient';
import { readLatestLiveVoiceCalibration, type LiveVoiceCalibrationRecord } from './live-voice-calibration';
import { liveConversationStore } from './live-conversation-store';
import type { SpeechDeliveryPlan } from './live-speech-delivery-plan';
import { ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, ASSISTANT_VOICE_PERF_EVENT, CHAT_SESSION_SELECTED_EVENT, LIVE_CHAT_SESSION_CHANGED_EVENT, LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT, LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, LIVE_VOICE_CALIBRATION_UPDATED_EVENT } from '../../../events/bus';

let liveConversationStoreBridgeInstalled = false;

const DELIVERY_PLAN_EVENT = 'omnix:live-speech-delivery-plan';
const PRONUNCIATION_UPDATED_EVENT = 'omnix:live-pronunciation-updated';

type UnknownDetail = Record<string, unknown>;

export function initializeLiveConversationStoreBridge(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveConversationStoreBridgeInstalled) return () => undefined;
  liveConversationStoreBridgeInstalled = true;

  const profile = readEffectiveLiveConversationProfile();
  if (profile) liveConversationStore.dispatch({ type: 'profile', profile });
  applyCalibration(readLatestLiveVoiceCalibration());

  const handleSession = (event: Event) => {
    const detail = detailOf(event);
    const sessionId = stringValue(detail.sessionId) || stringValue(detail.session_id);
    liveConversationStore.dispatch({ type: 'session', sessionId: sessionId || null });
    const characterId = stringValue(detail.characterId) || stringValue(detail.character_id);
    const displayName = stringValue(detail.characterName)
      || stringValue(detail.displayName)
      || stringValue(detail.identity);
    const voiceId = stringValue(detail.voiceId) || stringValue(detail.voice_id);
    const profileVersion = numberValue(detail.profileVersion) ?? numberValue(detail.profile_version);
    if (characterId || displayName || voiceId || profileVersion !== null) {
      liveConversationStore.dispatch({
        type: 'identity',
        identity: {
          ...(characterId ? { characterId } : {}),
          ...(displayName ? { displayName } : {}),
          ...(voiceId ? { voiceId } : {}),
          ...(profileVersion !== null ? { profileVersion } : {}),
        },
      });
    }
  };
  const handleCallStart = () => {
    liveConversationStore.dispatch({ type: 'reset_conversation' });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'connection', value: 'connecting' } });
  };
  const handleCallConnected = () => {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'connection', value: 'connected' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'listening' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'unclaimed' } });
  };
  const handleStop = () => {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'connection', value: 'stopping' } });
    liveConversationStore.dispatch({ type: 'reset_conversation' });
  };
  const handleUserSpeech = (event: Event) => {
    const detail = detailOf(event);
    const partial = stringValue(detail.partialTranscript)
      || stringValue(detail.partial_transcript)
      || stringValue(detail.transcript);
    if (partial) liveConversationStore.dispatch({ type: 'transcript_partial', text: partial });
    if (Boolean(detail.assistantSpeaking)) {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'speech_candidate' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'confirming' } });
      return;
    }
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'speaking' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'user' } });
  };
  const handleInterrupt = () => {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'accepted' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'interrupted' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'interrupted' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'user' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'speaking' } });
  };
  const handlePlayback = (event: Event) => {
    const speaking = Boolean(detailOf(event).speaking);
    if (speaking) {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'audio_started' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'assistant' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'inactive' } });
      return;
    }
    const state = liveConversationStore.getState().conversation;
    if (state.assistantTurn === 'speaking') {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'idle' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'completed' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'unclaimed' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'listening' } });
    }
  };
  const handleProfile = (event: Event) => {
    const next = (event as CustomEvent<LiveConversationProfile>).detail
      ?? readEffectiveLiveConversationProfile();
    liveConversationStore.dispatch({ type: 'profile', profile: next ?? null });
  };
  const handleCalibration = (event: Event) => {
    applyCalibration((event as CustomEvent<LiveVoiceCalibrationRecord>).detail ?? readLatestLiveVoiceCalibration());
  };
  const handlePlan = (event: Event) => {
    liveConversationStore.dispatch({
      type: 'delivery_plan',
      plan: (event as CustomEvent<SpeechDeliveryPlan>).detail ?? null,
    });
  };
  const handleEvaluation = (event: Event) => {
    const detail = detailOf(event);
    const report = isRecord(detail.report) ? detail.report : detail;
    liveConversationStore.dispatch({ type: 'quality', summary: report });
  };
  const handlePronunciation = (event: Event) => {
    const detail = detailOf(event);
    liveConversationStore.dispatch({
      type: 'pronunciation_revision',
      revision: numberValue(detail.revision) ?? liveConversationStore.getState().pronunciationRevision + 1,
    });
  };
  const handleProactiveDelivered = () => {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'initiative', value: 'cooldown' } });
  };
  const handlePerf = (event: Event) => mapPerfEvent(detailOf(event));

  const listeners: Array<[string, EventListener]> = [
    [LIVE_CHAT_SESSION_CHANGED_EVENT, handleSession],
    [CHAT_SESSION_SELECTED_EVENT, handleSession],
    [ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleCallStart],
    [ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, handleCallConnected],
    [ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop],
    [ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech],
    [ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterrupt],
    [ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, handlePlayback],
    [LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, handleProfile],
    [LIVE_VOICE_CALIBRATION_UPDATED_EVENT, handleCalibration],
    [DELIVERY_PLAN_EVENT, handlePlan],
    [LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT, handleEvaluation],
    [PRONUNCIATION_UPDATED_EVENT, handlePronunciation],
    [LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT, handleProactiveDelivered],
    [ASSISTANT_VOICE_PERF_EVENT, handlePerf],
  ];
  for (const [name, listener] of listeners) window.addEventListener(name, listener);

  return () => {
    for (const [name, listener] of listeners) window.removeEventListener(name, listener);
    liveConversationStoreBridgeInstalled = false;
  };
}

function mapPerfEvent(detail: UnknownDetail): void {
  const stage = stringValue(detail.stage);
  if (!stage) return;
  if (stage === 'overlap_candidate') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'ducking' } });
  }
  if (stage === 'barge_in_acoustic_candidate') {
    const decision = stringValue(detail.decision);
    if (decision === 'likely_echo' || decision === 'no_playback') {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'inactive' } });
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'listening' } });
    } else {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'ducking' } });
    }
  }
  if (stage === 'barge_in_ducked') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'confirming' } });
  }
  if (stage === 'barge_in_restored') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'rejected' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'listening' } });
  }
  if (stage === 'overlap_classified') {
    const intent = stringValue(detail.intent);
    const rejected = intent === 'noise' || intent === 'backchannel' || intent === 'uncertain';
    liveConversationStore.dispatch({
      type: 'conversation',
      event: { type: 'barge_in', value: rejected ? 'rejected' : 'accepted' },
    });
    if (rejected) {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'listening' } });
    }
  }
  if (stage === 'initiative_policy_decision') {
    const action = stringValue(detail.action);
    liveConversationStore.dispatch({
      type: 'conversation',
      event: {
        type: 'initiative',
        value: action === 'speak' ? 'eligible' : action === 'suppress' ? 'suppressed' : 'inactive',
      },
    });
  }
  if (stage === 'initiative_generation_started') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'initiative', value: 'prompting' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'generating' } });
  }
  if (stage === 'initiative_generation_completed') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'queued' } });
  }
  if (stage.includes('provider') && (stage.includes('started') || stage.includes('request'))) {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'generating' } });
  }
  if (stage === 'stt_partial_received') {
    const text = stringValue(detail.text) || stringValue(detail.transcript);
    if (text) liveConversationStore.dispatch({ type: 'transcript_partial', text });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'speaking' } });
  }
  if (stage === 'stt_final_received') {
    const text = stringValue(detail.text) || stringValue(detail.transcript);
    if (text) liveConversationStore.dispatch({ type: 'transcript_final', text });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'finalizing' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'floor_owner', value: 'unclaimed' } });
  }
  if (stage === 'endpoint_pause' || stage === 'semantic_pause') {
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'user_turn', value: 'paused' } });
  }
}

function applyCalibration(record: LiveVoiceCalibrationRecord | null): void {
  const runtime = liveConversationStore.getState();
  const configuredMode = runtime.profile?.duplex_mode ?? runtime.duplex.configuredMode;
  const explicitEchoAware = configuredMode === 'echo_aware';
  liveConversationStore.dispatch({
    type: 'duplex',
    duplex: {
      calibration: record,
      resolvedMode: explicitEchoAware ? 'echo_aware' : 'half_duplex',
      reason: explicitEchoAware
        ? 'explicit_user_selection'
        : record ? 'calibration_device_unverified' : 'calibration_missing',
      confidence: record?.confidence ?? 0,
    },
  });
}

function detailOf(event: Event): UnknownDetail {
  const detail = (event as CustomEvent<unknown>).detail;
  return isRecord(detail) ? detail : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
