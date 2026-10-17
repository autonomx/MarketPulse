 
/* eslint-disable no-restricted-syntax -- baseline WP-9.x */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { characterClient, type CharacterLiveCallRuntime } from '../chat/characterClient';
import { liveConversationStore } from './live-conversation-store';
import {
  DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG,
  createAssistantWorkspaceRuntimeConfig,
} from './runtime-config';
import { fixture } from '../../../test/fixture';

afterEach(() => {
  document.body.innerHTML = '';
  liveConversationStore.reset();
  vi.unstubAllGlobals();
});

function trustedRuntime(
  overrides: Partial<CharacterLiveCallRuntime> = {},
): CharacterLiveCallRuntime {
  return fixture({
    session_id: 'chat:jinx',
    interaction_mode: 'character',
    display_name: 'Jinx',
    character_id: 'jinx',
    character_profile_version: 4,
    effective_identity_hash: 'a'.repeat(64),
    voice_asset_id: 'voice-cloning:jinx',
    voice_speaker_id: 'Jinx',
    greeting: '',
    speech_style: {
      speed: 1,
      temperature: 0.6,
      top_k: 20,
      top_p: 0.85,
      repetition_penalty: 1,
      expressiveness: 'neutral',
      emotion: 'neutral',
      interruption_style: 'balanced',
    },
    read_memory: true,
    write_memory: false,
    shared_memory_access: 'read_only',
    preload: {
      profile_loaded: true,
      voice_resolved: true,
      memory_snapshot_loaded: false,
      memory_record_count: 0,
      preload_ms: 1,
      resolved_at: '2026-08-01T00:00:00Z',
    },
    ...overrides,
  });
}

async function retainRuntime(runtime = trustedRuntime()): Promise<void> {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(runtime)));
  await characterClient.liveCallRuntime(runtime.session_id);
}

describe('createAssistantWorkspaceRuntimeConfig', () => {
  it('uses durable defaults when env values are absent', () => {
    expect(createAssistantWorkspaceRuntimeConfig({})).toEqual(DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG);
  });

  it('reads workspace services and feature flags while leaving provider defaults server-owned', () => {
    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_WORKSPACE_ID: 'workspace:lab',
      VITE_ASSISTANT_PROJECT_ID: 'project:omnix',
      VITE_ASSISTANT_PROVIDER_ID: 'lmstudio',
      VITE_ASSISTANT_MODEL_ID: 'qwen-local',
      VITE_ASSISTANT_STT_URL: 'http://localhost:5201/transcribe',
      VITE_ASSISTANT_TTS_URL: 'http://localhost:5101/synthesize',
      VITE_ASSISTANT_TTS_VOICE: 'narrator',
      VITE_ASSISTANT_EVENT_STORAGE_KEY: 'omnix.test.events',
      VITE_ASSISTANT_LIVE_ENABLED: 'true',
      VITE_ASSISTANT_PERSISTED_EVENTS: 'false',
      VITE_ASSISTANT_TOOL_EXECUTION: '1',
    });

    expect(config).toMatchObject({
      workspaceId: 'workspace:lab',
      projectId: 'project:omnix',
      defaultProviderId: undefined,
      defaultModelId: undefined,
      sttServiceUrl: 'http://localhost:5201/transcribe',
      ttsServiceUrl: 'http://localhost:5101/synthesize',
      ttsVoice: 'narrator',
      eventStorageKey: 'omnix.test.events',
      features: {
        liveAssistant: true,
        persistedEvents: false,
        toolExecution: true,
      },
    });
  });

  it('uses the active Character Mode speaker instead of the static default voice', () => {
    document.body.innerHTML = '<section class="assistant-live-card" data-live-voice-id="Inigo"></section>';

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Inigo');
  });

  it('uses a hot-swapped session voice when the rendered voice is temporarily empty', () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity active">Talking to Jinx</span>
      </section>`;
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:jinx' });
    liveConversationStore.dispatch({
      type: 'identity',
      identity: {
        characterId: 'jinx',
        displayName: 'Jinx',
        voiceId: 'Jinx',
        profileVersion: 4,
      },
    });

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Jinx');
  });

  it('uses the retained trusted runtime when its event and rendered attribute were missed', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity">Jinx</span>
      </section>`;
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:jinx' });
    liveConversationStore.dispatch({
      type: 'identity',
      identity: {
        characterId: 'jinx',
        displayName: 'Jinx',
        voiceId: null,
        profileVersion: 4,
      },
    });
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Jinx');
  });

  it('trusts the selected Character Mode runtime before a stale System Assistant label', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity">System Assistant</span>
      </section>`;
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:jinx' });
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Jinx');
  });

  it('uses the retained runtime for manual playback without an active call marker', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity">Jinx is active in Live Voice</span>
      </section>`;
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Jinx');
  });

  it('uses the retained trusted runtime when only the visible active character can confirm it', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity active">Talking to Jinx</span>
      </section>`;
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('Jinx');
  });

  it('does not use a retained runtime belonging to a different displayed character', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity">Maya is active in Live Voice</span>
      </section>`;
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('default');
  });

  it('does not leak a stale retained or stored character voice into another System Assistant session', async () => {
    document.body.innerHTML = `
      <section class="assistant-live-card" data-live-voice-id="">
        <span class="assistant-live-identity">System Assistant</span>
      </section>`;
    liveConversationStore.dispatch({ type: 'session', sessionId: 'chat:system' });
    liveConversationStore.dispatch({
      type: 'identity',
      identity: {
        characterId: 'system-assistant',
        displayName: 'System Assistant',
        voiceId: null,
        profileVersion: null,
      },
    });
    await retainRuntime();

    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_TTS_VOICE: 'default',
    });

    expect(config.ttsVoice).toBe('default');
  });

  it('ignores empty strings and unknown boolean values', () => {
    const config = createAssistantWorkspaceRuntimeConfig({
      VITE_ASSISTANT_WORKSPACE_ID: '   ',
      VITE_ASSISTANT_PROVIDER_ID: '',
      VITE_ASSISTANT_LIVE_ENABLED: 'maybe',
    });

    expect(config.workspaceId).toBe(DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.workspaceId);
    expect(config.defaultProviderId).toBeUndefined();
    expect(config.features.liveAssistant).toBe(DEFAULT_ASSISTANT_WORKSPACE_RUNTIME_CONFIG.features.liveAssistant);
  });
});
