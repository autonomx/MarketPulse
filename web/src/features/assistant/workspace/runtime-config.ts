 
import { readLatestTrustedCharacterRuntime } from '../chat/characterClient';
import { liveConversationStore } from './live-conversation-store';

export type AssistantWorkspaceRuntimeFeatureFlags = {
  liveAssistant: boolean;
  persistedEvents: boolean;
  toolExecution: boolean;
};

export type AssistantWorkspaceRuntimeConfig = {
  workspaceId: string;
  projectId?: string;
  defaultProviderId?: string;
  defaultModelId?: string;
  sttServiceUrl?: string;
  ttsServiceUrl?: string;
  ttsVoice?: string;
  eventStorageKey: string;
  features: AssistantWorkspaceRuntimeFeatureFlags;
};

export type AssistantWorkspaceRuntimeEnv = Record<string, string | boolean | number | undefined>;

export type CharacterPlaybackVoiceSource =
  | 'trusted_runtime_speaker'
  | 'trusted_runtime_store_voice'
  | 'rendered_character_voice'
  | 'session_store_voice'
  | 'system_assistant_veto'
  | 'document_unavailable'
  | 'none';

export type CharacterPlaybackVoiceDecision = {
  voiceId: string | null;
  source: CharacterPlaybackVoiceSource;
  reason: string;
  cardCount: number;
  renderedIdentities: string[];
  renderedVoiceIds: Array<string | null>;
  systemOnlyRendered: boolean;
  sameSelectedSession: boolean;
  sameDisplayedCharacter: boolean;
  store: {
    sessionId: string | null;
    characterId: string;
    displayName: string;
    voiceId: string | null;
    profileVersion: number | null;
  };
  runtime: {
    sessionId: string;
    interactionMode: 'system' | 'character';
    characterId: string | null;
    displayName: string;
    voiceAssetId: string | null;
    voiceProfileAssetId: string | null;
    voiceSpeakerId: string | null;
    voiceResolved: boolean;
    voiceError: string | null;
  } | null;
};

export const DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG: AssistantWorkspaceRuntimeConfig = {
  workspaceId: 'workspace:default',
  projectId: 'project:chatbot',
  eventStorageKey: 'omnix.assistantWorkspace.events',
  features: {
    liveAssistant: true,
    persistedEvents: true,
    toolExecution: true,
  },
};

export function createAssistantWorkspaceRuntimeConfig(
  env: AssistantWorkspaceRuntimeEnv = getImportMetaEnv(),
): AssistantWorkspaceRuntimeConfig {
  return {
    workspaceId: readString(env, 'VITE_ASSISTANT_WORKSPACE_ID') ?? DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.workspaceId,
    projectId: readString(env, 'VITE_ASSISTANT_PROJECT_ID') ?? DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.projectId,
    // Provider and model defaults are authoritative in the Settings Control Center.
    // Leaving these unset lets new sessions resolve PostgreSQL-backed defaults while
    // explicit selectors and existing session overrides continue to win.
    defaultProviderId: undefined,
    defaultModelId: undefined,
    sttServiceUrl: readString(env, 'VITE_ASSISTANT_STT_URL'),
    ttsServiceUrl: readString(env, 'VITE_ASSISTANT_TTS_URL'),
    // Character Mode is authoritative while its trusted runtime belongs to the
    // currently selected or visibly displayed character. Legacy response-audio
    // paths call this factory at playback time, so they receive the exact
    // case-sensitive TTS speaker.
    ttsVoice: resolveCharacterPlaybackVoice() ?? readString(env, 'VITE_ASSISTANT_TTS_VOICE'),
    eventStorageKey:
      readString(env, 'VITE_ASSISTANT_EVENT_STORAGE_KEY') ??
      DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.eventStorageKey,
    features: {
      liveAssistant:
        readBoolean(env, 'VITE_ASSISTANT_LIVE_ENABLED') ??
        DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.features.liveAssistant,
      persistedEvents:
        readBoolean(env, 'VITE_ASSISTANT_PERSISTED_EVENTS') ??
        DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.features.persistedEvents,
      toolExecution:
        readBoolean(env, 'VITE_ASSISTANT_TOOL_EXECUTION') ??
        DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.features.toolExecution,
    },
  };
}

function getImportMetaEnv(): AssistantWorkspaceRuntimeEnv {
  return ((import.meta as unknown as { env?: AssistantWorkspaceRuntimeEnv }).env ?? {}) as AssistantWorkspaceRuntimeEnv;
}

export function resolveCharacterPlaybackVoice(): string | null {
  return resolveCharacterPlaybackVoiceDecision().voiceId;
}

export function resolveCharacterPlaybackVoiceDecision(): CharacterPlaybackVoiceDecision {
  if (typeof document === 'undefined') {
    return {
      voiceId: null,
      source: 'document_unavailable',
      reason: 'browser_document_unavailable',
      cardCount: 0,
      renderedIdentities: [],
      renderedVoiceIds: [],
      systemOnlyRendered: false,
      sameSelectedSession: false,
      sameDisplayedCharacter: false,
      store: {
        sessionId: null,
        characterId: 'system-assistant',
        displayName: 'System Assistant',
        voiceId: null,
        profileVersion: null,
      },
      runtime: null,
    };
  }

  const cards = Array.from(document.querySelectorAll<HTMLElement>('.assistant-live-card'));
  const renderedIdentities = cards.map((card) => normalizeIdentity(
    card.querySelector<HTMLElement>('.assistant-live-identity')?.textContent ?? '',
  ));
  const renderedVoiceIds = cards.map((card) => card.dataset.liveVoiceId?.trim() || null);
  const visibleIdentities = renderedIdentities.filter(Boolean);
  const systemOnlyRendered = Boolean(
    visibleIdentities.length
    && visibleIdentities.every((identity) => identity === 'system assistant'),
  );

  const storeState = liveConversationStore.getState();
  const storeCharacterActive = storeState.identity.characterId !== 'system-assistant';
  const storedVoice = storeCharacterActive ? storeState.identity.voiceId?.trim() : '';
  const runtime = readLatestTrustedCharacterRuntime();
  const runtimeIdentity = runtime ? normalizeIdentity(runtime.display_name) : '';
  const sameSelectedSession = Boolean(
    runtime
    && storeState.sessionId
    && storeState.sessionId === runtime.session_id,
  );
  const sameDisplayedCharacter = Boolean(
    runtimeIdentity
    && renderedIdentities.some((identity) => identity.includes(runtimeIdentity)),
  );
  const runtimeSnapshot = runtime ? {
    sessionId: runtime.session_id,
    interactionMode: runtime.interaction_mode,
    characterId: runtime.character_id ?? null,
    displayName: runtime.display_name,
    voiceAssetId: runtime.voice_asset_id ?? null,
    voiceProfileAssetId: runtime.voice_profile_asset_id ?? null,
    voiceSpeakerId: runtime.voice_speaker_id ?? null,
    voiceResolved: Boolean(runtime.preload?.voice_resolved),
    voiceError: runtime.preload?.voice_error ?? null,
  } : null;
  const base = {
    cardCount: cards.length,
    renderedIdentities,
    renderedVoiceIds,
    systemOnlyRendered,
    sameSelectedSession,
    sameDisplayedCharacter,
    store: {
      sessionId: storeState.sessionId,
      characterId: storeState.identity.characterId,
      displayName: storeState.identity.displayName,
      voiceId: storeState.identity.voiceId,
      profileVersion: storeState.identity.profileVersion,
    },
    runtime: runtimeSnapshot,
  };

  // The normalized result of the latest successful /live-call/runtime request is the
  // authoritative source. A matching selected session must win over stale React
  // presentation, including a briefly rendered System Assistant label.
  if (runtime?.interaction_mode === 'character') {
    const speakerId = runtime.voice_speaker_id?.trim() || runtime.voice_asset_id?.trim();
    if ((sameSelectedSession || sameDisplayedCharacter) && speakerId) {
      return {
        ...base,
        voiceId: speakerId,
        source: 'trusted_runtime_speaker',
        reason: sameSelectedSession
          ? 'trusted_runtime_matches_selected_session'
          : 'trusted_runtime_matches_displayed_character',
      };
    }
    if (sameSelectedSession && storedVoice) {
      return {
        ...base,
        voiceId: storedVoice,
        source: 'trusted_runtime_store_voice',
        reason: 'trusted_runtime_matches_session_but_has_no_speaker',
      };
    }
  }

  // Only veto character fallbacks after the trusted runtime has had a chance to
  // prove that it belongs to the selected session. The DOM can lag the runtime.
  if (systemOnlyRendered) {
    return {
      ...base,
      voiceId: null,
      source: 'system_assistant_veto',
      reason: 'all_rendered_live_voice_cards_are_system_assistant',
    };
  }

  const renderedVoice = renderedVoiceIds.find((voiceId, index) => (
    Boolean(voiceId) && renderedIdentities[index] !== 'system assistant'
  ));
  if (renderedVoice) {
    return {
      ...base,
      voiceId: renderedVoice,
      source: 'rendered_character_voice',
      reason: 'rendered_character_card_exposes_voice_id',
    };
  }

  // A hot-swapped assignment updates the session-scoped identity before the runtime
  // refresh can finish. Permit that exact voice only when a displayed identity still
  // matches the store's character.
  const storedDisplayName = normalizeIdentity(storeState.identity.displayName);
  if (
    storedVoice
    && storedDisplayName
    && renderedIdentities.some((identity) => identity.includes(storedDisplayName))
  ) {
    return {
      ...base,
      voiceId: storedVoice,
      source: 'session_store_voice',
      reason: 'session_store_identity_matches_displayed_character',
    };
  }

  return {
    ...base,
    voiceId: null,
    source: 'none',
    reason: runtime?.interaction_mode === 'character'
      ? 'trusted_runtime_did_not_match_selected_or_displayed_character'
      : 'no_character_voice_source_available',
  };
}

function normalizeIdentity(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/^talking to\s+/, '');
}

function readString(env: AssistantWorkspaceRuntimeEnv, key: string): string | undefined {
  const value = env[key];
  if (value === undefined || value === false) return undefined;
  const text = String(value).trim();
  return text ? text : undefined;
}

function readBoolean(env: AssistantWorkspaceRuntimeEnv, key: string): boolean | undefined {
  const value = env[key];
  if (value === undefined) return undefined;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  const normalized = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on', 'enabled'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off', 'disabled'].includes(normalized)) return false;
  return undefined;
}
