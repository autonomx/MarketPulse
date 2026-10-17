 
import {
  readLiveConversationSettings,
  updateLiveConversationSettings,
} from '../workspace/live-voice-conversation-settings';
import type { components } from '../api/generated';
import { unwrapAs } from '../../../api/http';
import { emitOmnixEvent, LIVE_CONVERSATION_PROFILE_CHANGED_EVENT } from '../../../events/bus';
import { api } from '../api/gateway';

export type PresencePreset = 'quiet' | 'natural' | 'engaged' | 'listener';
export type ConversationStance = 'automatic' | 'listen' | 'discuss' | 'advise' | 'brainstorm' | 'teach';
export type ConversationPace = 'quick' | 'balanced' | 'reflective';
export type InterruptionPreference = 'easy' | 'balanced' | 'finish_more';
export type AssistantBackchannelMode = 'off' | 'minimal' | 'natural';
export type InitiativeMode = 'off' | 'gentle' | 'active';
export type LongPauseBehavior = 'wait' | 'reassure' | 'ask_to_continue';
export type ResponseLength = 'brief' | 'conversational' | 'detailed';
export type ResponseOnsetStyle = 'adaptive' | 'immediate' | 'natural' | 'reflective';
export type EmotionalAttunement = 'off' | 'subtle' | 'expressive';
export type TopicContinuity = 'focused' | 'natural' | 'exploratory';
export type DuplexMode = 'automatic' | 'half_duplex' | 'echo_aware';
export type PronunciationSavePolicy = 'ask' | 'session_only' | 'allow';

export type LiveConversationProfile = components['schemas']['LiveConversationProfile'];

export type LiveConversationProfileEnvelope = components['schemas']['LiveConversationProfileEnvelope'];

export type LiveConversationProfilePatch = Partial<Omit<LiveConversationProfile, 'profile_version'>>;

export const LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY = 'omnix.liveConversation.effectiveProfile';
const MIGRATION_KEY = 'omnix.liveConversation.serverProfileMigrated.v1';
const CANONICAL_LEGACY_KEY = 'omnix.liveConversation.settings';
const ASSISTANT_LEGACY_KEY = 'omnix.chatbot.assistantSettings';

function profileCall<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  return unwrapAs(call, (error) => `Live Chat profile request failed with status ${error.status}.`);
}

const PROFILE_PATH = '/api/chat/sessions/{session_id}/live-conversation/profile';

export const liveConversationProfileClient = {
  defaults: (): Promise<LiveConversationProfile> => profileCall(api.GET('/api/live-chat/profile/defaults')),
  updateDefaults: (patch: LiveConversationProfilePatch): Promise<LiveConversationProfile> =>
    profileCall(api.PATCH('/api/live-chat/profile/defaults', { body: patch })),
  get: (sessionId: string): Promise<LiveConversationProfileEnvelope> =>
    profileCall(api.GET(PROFILE_PATH, { params: { path: { session_id: sessionId } } })),
  update: (sessionId: string, patch: LiveConversationProfilePatch): Promise<LiveConversationProfileEnvelope> =>
    profileCall(api.PATCH(PROFILE_PATH, { params: { path: { session_id: sessionId } }, body: patch })),
  clear: (sessionId: string): Promise<LiveConversationProfileEnvelope> =>
    profileCall(api.DELETE(PROFILE_PATH, { params: { path: { session_id: sessionId } } })),
};
export function mirrorProfileForLegacyRuntime(profile: LiveConversationProfile): void {
  updateLiveConversationSettings({
    conversationPace: profile.conversation_pace,
    interruptionPreference: profile.interruption_preference,
    backchannelMode: profile.assistant_backchannel_mode,
  });
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY, JSON.stringify(profile));
  } catch {
    // The in-memory event remains available when storage is blocked.
  }
  emitOmnixEvent(LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, profile);
}

export function readEffectiveLiveConversationProfile(): LiveConversationProfile | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(LIVE_CONVERSATION_EFFECTIVE_PROFILE_KEY);
    return raw ? JSON.parse(raw) as LiveConversationProfile : null;
  } catch {
    return null;
  }
}

export async function migrateLegacyConversationSettingsOnce(): Promise<boolean> {
  if (typeof window === 'undefined' || window.localStorage.getItem(MIGRATION_KEY) === 'done') return false;
  const hasLegacySettings = window.localStorage.getItem(CANONICAL_LEGACY_KEY) !== null
    || window.localStorage.getItem(ASSISTANT_LEGACY_KEY) !== null;
  if (!hasLegacySettings) {
    window.localStorage.setItem(MIGRATION_KEY, 'done');
    return false;
  }
  const legacy = readLiveConversationSettings();
  await liveConversationProfileClient.updateDefaults({
    conversation_pace: legacy.conversationPace,
    interruption_preference: legacy.interruptionPreference,
    assistant_backchannel_mode: legacy.backchannelMode,
  });
  window.localStorage.setItem(MIGRATION_KEY, 'done');
  return true;
}
