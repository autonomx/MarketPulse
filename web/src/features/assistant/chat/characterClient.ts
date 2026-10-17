import { applyAvatarPackToCurrentRuntime, publishCharacterAvatarRuntime } from './liveCharacterAvatarBridge';
import type { components } from '../api/generated';
import { unwrapAs } from '../../../api/http';
import { api } from '../api/gateway';

export type CharacterAvatarRenderMode = 'audio_envelope' | 'viseme' | 'static';
export type CharacterAvatarRenderer = 'sprite' | 'live2d' | 'rive';

export type CharacterAvatarPack = components['schemas']['CharacterAvatarPack'];

export interface UpsertCharacterAvatarPackInput {
  expected_version?: number | null;
  render_mode?: CharacterAvatarRenderMode;
  renderer?: CharacterAvatarRenderer;
  rig_asset_id?: string | null;
  base_asset_id?: string | null;
  mouth_frames?: Record<string, string>;
  blink_frames?: Record<string, string>;
  expression_frames?: Record<string, string>;
  outfit_frames?: Record<string, string>;
  background_asset_ids?: Record<string, string>;
  active_outfit?: string | null;
  active_background?: string | null;
  mouth_anchor?: Record<string, number>;
}

export type CharacterProfile = components['schemas']['CharacterProfile'];

export type CharacterListResponse = components['schemas']['CharacterListResponse'];

export type VoiceConsentStatus = 'unverified' | 'granted' | 'revoked';
export type VoiceDeletionState = 'active' | 'pending_deletion' | 'deleted';
export type VoiceAllowedUse = 'character' | 'live_call' | 'system_assistant' | 'general_tts';

export type VoiceProfileGovernance = components['schemas']['VoiceProfileGovernance'];

export interface UpdateVoiceProfileGovernanceInput {
  subject_owner: string;
  source_type: string;
  source_reference?: string;
  creator_id: string;
  consent_status: VoiceConsentStatus;
  allowed_uses: VoiceAllowedUse[];
  deletion_state: VoiceDeletionState;
  deletion_reason?: string;
}

export interface SessionInteraction {
  id: string;
  title: string;
  interaction_mode: 'system' | 'character';
  character_id?: string | null;
  voice_asset_id?: string | null;
  read_memory: boolean;
  write_memory: boolean;
  shared_memory_access: 'none' | 'read_only';
  transcript_policy: 'persistent' | 'temporary' | 'none';
  character_profile_version?: number | null;
  effective_identity_hash?: string | null;
  messages: Array<{ id: string; role: string; content: string; created_at: string }>;
}

export type LiveCallSpeechStyle = components['schemas']['LiveCallSpeechStyle'];

// Playback adapts the runtime: a cloned speaker plays as voice_asset_id and the profile moves aside.
export type CharacterLiveCallRuntime = components['schemas']['CharacterLiveCallRuntime'] & { voice_profile_asset_id?: string | null };

export type CharacterDataExport = components['schemas']['CharacterDataExport'];

export type CharacterDataActionResponse = components['schemas']['CharacterDataActionResponse'];

const LIVE_CALL_RUNTIME_CACHE_TTL_MS = 5 * 60_000;
const trackedPlaybackRuntimes = new Map<string, Set<CharacterLiveCallRuntime>>();
const liveCallRuntimeCache = new Map<string, { runtime: CharacterLiveCallRuntime; cachedAt: number }>();
const liveCallRuntimeRequests = new Map<string, Promise<CharacterLiveCallRuntime>>();
let latestTrustedPlaybackRuntime: CharacterLiveCallRuntime | null = null;

export function readLatestTrustedCharacterRuntime(): CharacterLiveCallRuntime | null {
  return latestTrustedPlaybackRuntime;
}

export function applyCharacterAvatarPackToTrackedRuntimes(
  characterId: string,
  avatarPack: CharacterAvatarPack | null,
): void {
  let runtimeToPublish: CharacterLiveCallRuntime | null = null;
  for (const tracked of trackedPlaybackRuntimes.values()) {
    for (const runtime of tracked) {
      if (runtime.character_id !== characterId) continue;
      runtime.avatar_pack = avatarPack;
      if (runtime === latestTrustedPlaybackRuntime) runtimeToPublish = runtime;
    }
  }
  if (latestTrustedPlaybackRuntime?.character_id === characterId) {
    latestTrustedPlaybackRuntime.avatar_pack = avatarPack;
    runtimeToPublish = latestTrustedPlaybackRuntime;
  }
  if (runtimeToPublish) publishCharacterAvatarRuntime(runtimeToPublish);
  else applyAvatarPackToCurrentRuntime(characterId, avatarPack);
}

function character<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  return unwrapAs(call, (error) => error.body || `Character request failed with status ${error.status}.`);
}

const characterPath = (characterId: string) => ({ character_id: characterId });
const sessionPath = (sessionId: string) => ({ session_id: sessionId });

function adaptLiveCallRuntimeForPlayback(runtime: CharacterLiveCallRuntime): CharacterLiveCallRuntime {
  const speakerId = runtime.voice_speaker_id?.trim();
  if (!speakerId) return runtime;
  return {
    ...runtime,
    voice_profile_asset_id: runtime.voice_asset_id ?? null,
    voice_asset_id: speakerId,
  };
}

function synchronizeTrackedPlaybackRuntime(runtime: CharacterLiveCallRuntime): CharacterLiveCallRuntime {
  const playbackRuntime = adaptLiveCallRuntimeForPlayback(runtime);
  const tracked = trackedPlaybackRuntimes.get(playbackRuntime.session_id) ?? new Set<CharacterLiveCallRuntime>();
  const incomingPackVersion = playbackRuntime.avatar_pack?.version ?? -1;
  const newerTrackedPack = [...tracked].find((existing) => (
    existing.character_id === playbackRuntime.character_id
    && (existing.avatar_pack?.version ?? -1) > incomingPackVersion
  ))?.avatar_pack;

  // Avatar selection updates the active runtime immediately. A runtime request
  // that began before that mutation can finish afterwards with the previous
  // pack, so never let an older pack version put the old Live2D rig back on
  // screen.
  if (newerTrackedPack) playbackRuntime.avatar_pack = newerTrackedPack;
  for (const existing of tracked) Object.assign(existing, playbackRuntime);
  tracked.add(playbackRuntime);
  trackedPlaybackRuntimes.set(playbackRuntime.session_id, tracked);
  liveCallRuntimeCache.set(playbackRuntime.session_id, {
    runtime: playbackRuntime,
    cachedAt: Date.now(),
  });
  latestTrustedPlaybackRuntime = playbackRuntime;
  publishCharacterAvatarRuntime(playbackRuntime);
  return playbackRuntime;
}

function cachedLiveCallRuntime(sessionId: string): CharacterLiveCallRuntime | null {
  const cached = liveCallRuntimeCache.get(sessionId);
  if (!cached) return null;
  if (Date.now() - cached.cachedAt > LIVE_CALL_RUNTIME_CACHE_TTL_MS) {
    liveCallRuntimeCache.delete(sessionId);
    return null;
  }
  latestTrustedPlaybackRuntime = cached.runtime;
  publishCharacterAvatarRuntime(cached.runtime);
  return cached.runtime;
}

async function loadLiveCallRuntime(
  sessionId: string,
  { force = false }: { force?: boolean } = {},
): Promise<CharacterLiveCallRuntime> {
  if (!force) {
    const cached = cachedLiveCallRuntime(sessionId);
    if (cached) return cached;
    const pending = liveCallRuntimeRequests.get(sessionId);
    if (pending) return pending;
  }

  const pending = character(api.GET('/api/chat/sessions/{session_id}/live-call/runtime', { params: { path: sessionPath(sessionId) } }))
    .then(synchronizeTrackedPlaybackRuntime);
  if (!force) liveCallRuntimeRequests.set(sessionId, pending);
  try {
    return await pending;
  } finally {
    if (!force && liveCallRuntimeRequests.get(sessionId) === pending) {
      liveCallRuntimeRequests.delete(sessionId);
    }
  }
}

function invalidateLiveCallRuntime(sessionId: string): void {
  liveCallRuntimeCache.delete(sessionId);
  liveCallRuntimeRequests.delete(sessionId);
}

export const characterClient = {
  list(includeArchived = false): Promise<CharacterListResponse> {
    return character(api.GET('/api/characters', { params: { query: includeArchived ? { include_archived: true } : {} } }));
  },
  create(input: components['schemas']['CreateCharacterRequest']): Promise<CharacterProfile> {
    return character(api.POST('/api/characters', { body: input }));
  },
  update(characterId: string, input: components['schemas']['UpdateCharacterRequest']): Promise<CharacterProfile> {
    return character(api.PATCH('/api/characters/{character_id}', { params: { path: characterPath(characterId) }, body: input }));
  },
  data(characterId: string): Promise<CharacterDataExport> {
    return character(api.GET('/api/characters/{character_id}/data', { params: { path: characterPath(characterId) } }));
  },
  applyDataActions(characterId: string, input: components['schemas']['CharacterDataActionRequest']): Promise<CharacterDataActionResponse> {
    return character(api.POST('/api/characters/{character_id}/data/actions', { params: { path: characterPath(characterId) }, body: input }));
  },
  avatarPack(characterId: string): Promise<CharacterAvatarPack> {
    return character(api.GET('/api/characters/{character_id}/avatar-pack', { params: { path: characterPath(characterId) } }));
  },
  upsertAvatarPack(characterId: string, input: UpsertCharacterAvatarPackInput): Promise<CharacterAvatarPack> {
    return character(api.PUT('/api/characters/{character_id}/avatar-pack', { params: { path: characterPath(characterId) }, body: input }));
  },
  deleteAvatarPack(characterId: string): Promise<{ ok: boolean; character_id: string }> {
    return character(api.DELETE('/api/characters/{character_id}/avatar-pack', { params: { path: characterPath(characterId) } }));
  },
  voiceGovernance(assetId: string): Promise<VoiceProfileGovernance> {
    return character(api.GET('/api/voice-profiles/{asset_id}/governance', { params: { path: { asset_id: assetId } } }));
  },
  updateVoiceGovernance(assetId: string, input: UpdateVoiceProfileGovernanceInput): Promise<VoiceProfileGovernance> {
    return character(api.PATCH('/api/voice-profiles/{asset_id}/governance', { params: { path: { asset_id: assetId } }, body: input }));
  },
  session(sessionId: string): Promise<SessionInteraction> {
    return character(api.GET('/api/chat/sessions/{session_id}/interaction', { params: { path: sessionPath(sessionId) } }));
  },
  liveCallRuntime(sessionId: string): Promise<CharacterLiveCallRuntime> {
    return loadLiveCallRuntime(sessionId);
  },
  refreshLiveCallRuntime(sessionId: string): Promise<CharacterLiveCallRuntime> {
    return loadLiveCallRuntime(sessionId, { force: true });
  },
  async setSession(sessionId: string, input: components['schemas']['SetSessionInteractionRequest']): Promise<SessionInteraction> {
    const interaction = await character(api.POST('/api/chat/sessions/{session_id}/interaction', {
      params: { path: sessionPath(sessionId) },
      body: {
        transcript_policy: 'persistent',
        read_memory: false,
        write_memory: false,
        shared_memory_access: 'none',
        ...input,
      },
    }));
    invalidateLiveCallRuntime(sessionId);
    return interaction;
  },
};
