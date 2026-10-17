import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  applyCharacterAvatarPackToTrackedRuntimes,
  characterClient,
  readLatestTrustedCharacterRuntime,
  type CharacterLiveCallRuntime,
} from './characterClient';
import { fixture } from '../../../test/fixture';

afterEach(() => vi.unstubAllGlobals());

function runtime(overrides: Partial<CharacterLiveCallRuntime> = {}): CharacterLiveCallRuntime {
  return fixture({
    session_id: 'chat:one',
    interaction_mode: 'character',
    display_name: 'Maya',
    character_id: 'maya',
    character_profile_version: 3,
    effective_identity_hash: 'a'.repeat(64),
    voice_asset_id: 'voice-cloning:maya',
    voice_speaker_id: 'Maya',
    greeting: 'Hey, good to hear from you.',
    speech_style: {
      speed: 0.94,
      temperature: 0.52,
      top_k: 18,
      top_p: 0.82,
      repetition_penalty: 1.05,
      expressiveness: 'relaxed',
      emotion: 'calm',
      interruption_style: 'patient',
    },
    read_memory: true,
    write_memory: false,
    shared_memory_access: 'none',
    memory_snapshot_id: 'memory-snapshot:one',
    preload: {
      profile_loaded: true,
      voice_resolved: true,
      memory_snapshot_loaded: true,
      memory_record_count: 4,
      preload_ms: 1.25,
      resolved_at: '2026-07-09T00:00:00Z',
    },
    ...overrides,
  });
}

describe('characterClient live-call runtime', () => {
  it('loads and retains the trusted runtime for the selected chat session', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(input.toString()).toBe('/api/chat/sessions/chat%3Aone/live-call/runtime');
      return Response.json(runtime());
    });
    vi.stubGlobal('fetch', fetchMock);

    const resolved = await characterClient.liveCallRuntime('chat:one');

    expect(resolved.character_id).toBe('maya');
    expect(resolved.voice_asset_id).toBe('Maya');
    expect(resolved.voice_profile_asset_id).toBe('voice-cloning:maya');
    expect(resolved.speech_style.speed).toBe(0.94);
    expect(resolved.preload.memory_record_count).toBe(4);
    expect(readLatestTrustedCharacterRuntime()).toBe(resolved);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('reuses a recent runtime when broad chat-query invalidation invokes the query again', async () => {
    const sessionId = 'chat:runtime-cache';
    const fetchMock = vi.fn(async () => Response.json(runtime({ session_id: sessionId })));
    vi.stubGlobal('fetch', fetchMock);

    const initial = await characterClient.liveCallRuntime(sessionId);
    const repeated = await characterClient.liveCallRuntime(sessionId);

    expect(repeated).toBe(initial);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('updates an already-referenced playback runtime and retained runtime when the linked voice changes', async () => {
    let requestCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(input.toString()).toBe('/api/chat/sessions/chat%3Ahot-swap/live-call/runtime');
      requestCount += 1;
      return Response.json(requestCount === 1
        ? runtime({ session_id: 'chat:hot-swap' })
        : runtime({
            session_id: 'chat:hot-swap',
            character_profile_version: 4,
            voice_asset_id: 'voice-cloning:inigo',
            voice_speaker_id: 'Inigo',
          }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const activeRuntimeReference = await characterClient.liveCallRuntime('chat:hot-swap');
    const refreshedRuntime = await characterClient.refreshLiveCallRuntime('chat:hot-swap');

    expect(refreshedRuntime.voice_asset_id).toBe('Inigo');
    expect(refreshedRuntime.voice_profile_asset_id).toBe('voice-cloning:inigo');
    expect(activeRuntimeReference.voice_asset_id).toBe('Inigo');
    expect(activeRuntimeReference.voice_speaker_id).toBe('Inigo');
    expect(activeRuntimeReference.voice_profile_asset_id).toBe('voice-cloning:inigo');
    expect(activeRuntimeReference.character_profile_version).toBe(4);
    expect(readLatestTrustedCharacterRuntime()).toBe(refreshedRuntime);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('applies an avatar selection immediately to the tracked live-call runtime', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(runtime({
      session_id: 'chat:avatar-switch',
      character_id: 'avatar-character',
    }))));
    const activeRuntime = await characterClient.liveCallRuntime('chat:avatar-switch');
    const avatarPack = fixture<NonNullable<CharacterLiveCallRuntime['avatar_pack']>>({
      character_id: 'avatar-character',
      version: 7,
      render_mode: 'viseme' as const,
      renderer: 'live2d' as const,
      rig_asset_id: 'character-live2d:open-llm-vtuber-shizuku',
      mouth_frames: {},
      blink_frames: {},
      expression_frames: {},
      outfit_frames: {},
      background_asset_ids: {},
      mouth_anchor: {},
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    });

    applyCharacterAvatarPackToTrackedRuntimes('avatar-character', avatarPack);

    expect(activeRuntime.avatar_pack).toBe(avatarPack);
    expect(readLatestTrustedCharacterRuntime()?.avatar_pack).toBe(avatarPack);
  });

  it('does not let an older live-runtime response restore the previous avatar pack', async () => {
    const maoPack = fixture<NonNullable<CharacterLiveCallRuntime['avatar_pack']>>({
      character_id: 'avatar-race',
      version: 2,
      render_mode: 'viseme' as const,
      renderer: 'live2d' as const,
      rig_asset_id: 'character-live2d:open-llm-vtuber-mao-pro',
      mouth_frames: {}, blink_frames: {}, expression_frames: {}, outfit_frames: {}, background_asset_ids: {}, mouth_anchor: {},
      created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    });
    const shizukuPack = { ...maoPack, version: 3, rig_asset_id: 'character-live2d:open-llm-vtuber-shizuku' };
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(runtime({
      session_id: 'chat:avatar-race',
      character_id: 'avatar-race',
      avatar_pack: maoPack,
    }))));

    const activeRuntime = await characterClient.liveCallRuntime('chat:avatar-race');
    applyCharacterAvatarPackToTrackedRuntimes('avatar-race', shizukuPack);
    const staleRefresh = await characterClient.refreshLiveCallRuntime('chat:avatar-race');

    expect(staleRefresh.avatar_pack).toBe(shizukuPack);
    expect(activeRuntime.avatar_pack).toBe(shizukuPack);
  });
});
