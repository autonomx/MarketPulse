import { emitOmnixEvent } from '../../../events/bus';
export type ConversationPaceSetting = 'quick' | 'balanced' | 'reflective';
export type InterruptionPreference = 'easy' | 'balanced' | 'finish_more';
export type BackchannelMode = 'off' | 'minimal' | 'natural';

export type LiveConversationSettings = {
  conversationPace: ConversationPaceSetting;
  interruptionPreference: InterruptionPreference;
  backchannelMode: BackchannelMode;
};

const STORAGE_KEY = 'omnix.liveConversation.settings';
const LEGACY_SETTINGS_KEY = 'omnix.chatbot.assistantSettings';

export const DEFAULT_LIVE_CONVERSATION_SETTINGS: LiveConversationSettings = {
  conversationPace: 'balanced',
  interruptionPreference: 'balanced',
  backchannelMode: 'off',
};

export function readLiveConversationSettings(): LiveConversationSettings {
  try {
    if (typeof window === 'undefined') return DEFAULT_LIVE_CONVERSATION_SETTINGS;
    const canonical = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '{}') as Record<string, unknown>;
    const legacy = JSON.parse(window.localStorage.getItem(LEGACY_SETTINGS_KEY) || '{}') as Record<string, unknown>;
    const parsed = { ...legacy, ...canonical };
    return {
      conversationPace: isConversationPace(parsed.conversationPace)
        ? parsed.conversationPace
        : DEFAULT_LIVE_CONVERSATION_SETTINGS.conversationPace,
      interruptionPreference: isInterruptionPreference(parsed.interruptionPreference)
        ? parsed.interruptionPreference
        : DEFAULT_LIVE_CONVERSATION_SETTINGS.interruptionPreference,
      backchannelMode: isBackchannelMode(parsed.backchannelMode)
        ? parsed.backchannelMode
        : DEFAULT_LIVE_CONVERSATION_SETTINGS.backchannelMode,
    };
  } catch {
    return DEFAULT_LIVE_CONVERSATION_SETTINGS;
  }
}

export function updateLiveConversationSettings(
  patch: Partial<LiveConversationSettings>,
): LiveConversationSettings {
  const current = readLiveConversationSettings();
  const next: LiveConversationSettings = {
    conversationPace: isConversationPace(patch.conversationPace)
      ? patch.conversationPace
      : current.conversationPace,
    interruptionPreference: isInterruptionPreference(patch.interruptionPreference)
      ? patch.interruptionPreference
      : current.interruptionPreference,
    backchannelMode: isBackchannelMode(patch.backchannelMode)
      ? patch.backchannelMode
      : current.backchannelMode,
  };
  try {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      mirrorConversationSettings(next);
      emitOmnixEvent('omnix:live-conversation-settings-changed', next);
    }
  } catch {
    // Settings remain usable for the current read even when storage is unavailable.
  }
  return next;
}

/**
 * Retained as a compatibility shim for older callers. Live Chat now renders the
 * controls through React rather than injecting them into the Settings DOM.
 */
export function initializeLiveConversationSettingsControls(): void {
  // Intentionally empty.
}

function mirrorConversationSettings(settings: LiveConversationSettings): void {
  try {
    const existing = JSON.parse(window.localStorage.getItem(LEGACY_SETTINGS_KEY) || '{}') as Record<string, unknown>;
    window.localStorage.setItem(LEGACY_SETTINGS_KEY, JSON.stringify({ ...existing, ...settings }));
  } catch {
    // The canonical conversation settings key remains authoritative.
  }
}

function isConversationPace(value: unknown): value is ConversationPaceSetting {
  return value === 'quick' || value === 'balanced' || value === 'reflective';
}

function isInterruptionPreference(value: unknown): value is InterruptionPreference {
  return value === 'easy' || value === 'balanced' || value === 'finish_more';
}

function isBackchannelMode(value: unknown): value is BackchannelMode {
  return value === 'off' || value === 'minimal' || value === 'natural';
}
