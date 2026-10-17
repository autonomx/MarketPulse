/**
 * Omnix's in-page event bus (WP-9.6). Every event has one name and one
 * detail type, in `OmnixEventMap`; `emitOmnixEvent` and `onOmnixEvent` check
 * both. Events travel on `window` as `OmnixEvent`s (an `Event` with a
 * `detail`, `null` when there is none, as a `CustomEvent` had), so a listener
 * added with `window.addEventListener` receives them too.
 *
 * The core events are below; a feature declares its own events by
 * augmenting `OmnixEventMap` in its folder (for example
 * `features/assistant/assistantEvents.ts`), so the bus imports no feature.
 */
import type { ChatSession, ChatSessionListResponse } from '../api/client';
import type { OmnixAppearanceChangeDetail } from '../design/appearanceEffects';

export interface OmnixEventMap {
  // Appearance and trading.
  'omnix:appearance-change': OmnixAppearanceChangeDetail;
  'omnix.trading.chart.timezone-change': string;
  'omnix:trading-alerts-changed': null;
  'omnix:paper-position-protection-changed': { accountId: string; instrumentId: string };
  // Chat sessions and the composer.
  'omnix:chat-session-created': { session: ChatSession };
  'omnix:chat-session-selected': { sessionId: string | null; session: ChatSession | ChatSessionListResponse['sessions'][number] | undefined };
  'omnix:live-chat-session-changed': { sessionId: string; characterId?: string; displayName?: string; voiceId?: string; profileVersion?: number };
  'omnix:chat-image-selected': { dataUrl: string; mimeType: string; size: number };
  'omnix:chat-text-file-selected': { filename: string; mimeType: string; size: number; text: string };
  'omnix:chat-image-error': { message: string };
  'omnix:desktop-share-changed': { sharing: boolean };
  'omnix:research-job-settled': { jobId: string | undefined };
}

/** Event names; each is a key of `OmnixEventMap`. */
export const APPEARANCE_CHANGE_EVENT = 'omnix:appearance-change';
export const ASSISTANT_AUDIO_DUCK_EVENT = 'omnix:assistant-audio-duck';
export const ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT = 'omnix:assistant-audio-playback-state';
export const ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT = 'omnix:assistant-live-voice-call-connected';
export const ASSISTANT_LIVE_VOICE_CALL_START_EVENT = 'omnix:assistant-live-voice-call-start';
export const ASSISTANT_LIVE_VOICE_STOP_EVENT = 'omnix:assistant-live-voice-stop';
export const ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT = 'omnix:assistant-live-voice-user-speech';
export const ASSISTANT_VOICE_INTERRUPT_EVENT = 'omnix:assistant-voice-interrupt';
export const ASSISTANT_VOICE_PERF_EVENT = 'omnix:assistant-voice-perf';
export const ASSISTANT_VOICE_RELEASE_QUALITY_EVENT = 'omnix:assistant-voice-release-quality';
export const CHARACTER_AVATAR_FRAME_EVENT = 'omnix:character-avatar-frame';
export const CHARACTER_AVATAR_PCM_EVENT = 'omnix:character-avatar-pcm';
export const CHARACTER_AVATAR_RUNTIME_EVENT = 'omnix:character-avatar-runtime';
export const CHARACTER_LIVE2D_FRAMING_EVENT = 'omnix:character-live2d-framing';
export const CHARACTER_LIVE2D_MOTION_EVENT = 'omnix:character-live2d-motion';
export const CHARACTER_LIVE2D_RENDER_EVENT = 'omnix:character-live2d-render';
export const CHARACTER_LIVE2D_ZOOM_EVENT = 'omnix:character-live2d-zoom';
export const CHAT_IMAGE_ERROR_EVENT = 'omnix:chat-image-error';
export const CHAT_IMAGE_SELECTED_EVENT = 'omnix:chat-image-selected';
export const CHAT_SESSION_CREATED_EVENT = 'omnix:chat-session-created';
export const CHAT_SESSION_SELECTED_EVENT = 'omnix:chat-session-selected';
export const CHAT_TEXT_FILE_SELECTED_EVENT = 'omnix:chat-text-file-selected';
export const DESKTOP_COMPANION_DELIVERY_EVENT = 'omnix:desktop-companion-delivery';
export const DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT = 'omnix:desktop-companion-delivery-request';
export const DESKTOP_COMPANION_EVALUATION_EVENT = 'omnix:desktop-companion-evaluation';
export const DESKTOP_COMPANION_EXPRESSION_EVENT = 'omnix:desktop-companion-expression';
export const DESKTOP_COMPANION_STATUS_EVENT = 'omnix:desktop-companion-status';
export const DESKTOP_COMPANION_TEXT_EVENT = 'omnix:desktop-companion-text';
export const DESKTOP_SHARE_CHANGED_EVENT = 'omnix:desktop-share-changed';
export const LIVE_CALL_DIAGNOSTIC_EVENT = 'omnix:live-call-diagnostic';
export const LIVE_CHAT_SESSION_CHANGED_EVENT = 'omnix:live-chat-session-changed';
export const LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT = 'omnix:live-conversation-assistant-summary';
export const LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT = 'omnix:live-conversation-durable-evaluation-saved';
export const LIVE_CONVERSATION_EVALUATION_UPDATED_EVENT = 'omnix:live-conversation-evaluation-updated';
export const LIVE_CONVERSATION_LISTENER_BACKCHANNEL_EVENT = 'omnix:live-conversation-listener-backchannel';
export const LIVE_CONVERSATION_PROACTIVE_DELIVERED_EVENT = 'omnix:live-conversation-proactive-delivered';
export const LIVE_CONVERSATION_PROFILE_CHANGED_EVENT = 'omnix:live-conversation-profile-changed';
export const LIVE_CONVERSATION_PRONUNCIATIONS_CHANGED_EVENT = 'omnix:live-conversation-pronunciations-changed';
export const LIVE_CONVERSATION_REPAIR_PLANNED_EVENT = 'omnix:live-conversation-repair-planned';
export const LIVE_CONVERSATION_SETTINGS_CHANGED_EVENT = 'omnix:live-conversation-settings-changed';
export const LIVE_CONVERSATION_STORE_UPDATED_EVENT = 'omnix:live-conversation-store-updated';
export const LIVE_CONVERSATION_USER_CONTINUER_EVENT = 'omnix:live-conversation-user-continuer';
export const LIVE_COORDINATION_TERMINAL_EVENT = 'omnix:live-coordination-terminal';
export const LIVE_OBSERVATION_CANDIDATE_EVENT = 'omnix:live-observation-candidate';
export const LIVE_OBSERVATION_SUPERSEDED_EVENT = 'omnix:live-observation-superseded';
export const LIVE_OBSERVATION_TEXT_EVENT = 'omnix:live-observation-text';
export const LIVE_PRESENCE_POLICY_REFRESH_EVENT = 'omnix:live-presence-policy-refresh';
export const LIVE_RUNTIME_BOOTSTRAP_EVENT = 'omnix:live-runtime-bootstrap';
export const LIVE_STT_SPECULATION_CANDIDATE_EVENT = 'omnix:live-stt-speculation-candidate';
export const LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT = 'omnix:live-stt-speculation-delivery-settled';
export const LIVE_STT_SPECULATION_FINAL_EVENT = 'omnix:live-stt-speculation-final';
export const LIVE_STT_SPECULATION_PARTIAL_EVENT = 'omnix:live-stt-speculation-partial';
export const LIVE_TASK_CONTRACT_EVENT = 'omnix:live-task-contract';
export const LIVE_VOICE_CALIBRATION_UPDATED_EVENT = 'omnix:live-voice-calibration-updated';
export const LIVE_VOICE_CUE_SEGMENT_EVENT = 'omnix:live-voice-cue-segment';
export const LIVE_VOICE_CUE_SKIPPED_EVENT = 'omnix:live-voice-cue-skipped';
export const LIVE_VOICE_HUMANIZATION_FLAGS_CHANGED_EVENT = 'omnix:live-voice-humanization-flags-changed';
export const LIVE_VOICE_PERFORMANCE_BEHAVIOR_EVENT = 'omnix:live-voice-performance-behavior';
export const LIVE_VOICE_RELEASE_OBSERVATION_EVENT = 'omnix:live-voice-release-observation';
export const LIVE_VOICE_TURN_TIMELINE_EVENT = 'omnix:live-voice-turn-timeline';
export const PAPER_POSITION_PROTECTION_CHANGED_EVENT = 'omnix:paper-position-protection-changed';
export const RESEARCH_JOB_SETTLED_EVENT = 'omnix:research-job-settled';
export const TRADING_ALERTS_CHANGED_EVENT = 'omnix:trading-alerts-changed';
export const TRADING_CHART_TIMEZONE_CHANGE_EVENT = 'omnix.trading.chart.timezone-change';
export const VOICE_CUE_ASSETS_CLEAR_EVENT = 'omnix:voice-cue-assets-clear';
export const VOICE_CUE_ASSETS_READY_EVENT = 'omnix:voice-cue-assets-ready';
export const VOICE_CUE_ASSETS_REGISTERED_EVENT = 'omnix:voice-cue-assets-registered';
export const VOICE_CUE_PACK_STATUS_EVENT = 'omnix:voice-cue-pack-status';

export type OmnixEventName = keyof OmnixEventMap;

export class OmnixEvent<K extends OmnixEventName = OmnixEventName> extends Event {
  readonly detail: OmnixEventMap[K];

  constructor(type: K, detail: OmnixEventMap[K]) {
    super(type);
    this.detail = detail;
  }
}

/** The detail argument: optional for events whose detail may be empty. */
type DetailArgs<K extends OmnixEventName> = null extends OmnixEventMap[K] ? [detail?: OmnixEventMap[K]] : [detail: OmnixEventMap[K]];

export function emitOmnixEvent<K extends OmnixEventName>(type: K, ...[detail]: DetailArgs<K>): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new OmnixEvent(type, (detail ?? null) as OmnixEventMap[K]));
}

/** Calls `handler` with each event's detail until the returned function is called. */
export function onOmnixEvent<K extends OmnixEventName>(type: K, handler: (detail: OmnixEventMap[K]) => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const listener = (event: Event) => handler((event as OmnixEvent<K>).detail);
  window.addEventListener(type, listener);
  return () => window.removeEventListener(type, listener);
}
