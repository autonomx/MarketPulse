import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import { createSettingsSaveRequest, loadSettingsProfile, saveSettingsProfile, type SettingsFetch } from './settingsApi';
import { migrateSettingsDocument } from './settingsMerge';

function response(body: unknown, status = 200): Response {
  return Response.json(body, { status });
}

describe('settings API adapter', () => {
  it('loads and migrates the embedded profile', async () => {
    const fetcher: SettingsFetch = async () => response({ success: true, provider: 'lmstudio', audio_provider_tts: '', audio_provider_stt: '', settings: { settings_control_center: { revision: 'r1', appearance: { mode: 'dark' } } } });
    const result = await loadSettingsProfile(fetcher);
    expect(result.profile.appearance.mode).toBe('dark');
    expect(result.profile.voice.speed).toBe(1);
  });

  it('builds and saves only changed namespaces', async () => {
    const base = migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT);
    const draft = migrateSettingsDocument({ ...base, assistant: { ...base.assistant, autoSpeakReplies: true } });
    const request = createSettingsSaveRequest(base, draft);
    let sent = '';
    let path = '';
    const fetcher: SettingsFetch = async (input, init) => { path = String(input); sent = String(init?.body || ''); return response({ success: true }); };
    await saveSettingsProfile(request, fetcher);
    // The profile route keeps the patch, provider sections and API keys together (POST /api/settings takes per-key values).
    expect(path).toBe('/api/settings/profile');
    expect(JSON.parse(sent).settings_profile_patch).toEqual({ assistant: draft.assistant });
  });

  it('sends provider keys only through the legacy secret channel', () => {
    const base = migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT);
    const draft = migrateSettingsDocument({
      ...base,
      global: { ...base.global, providers: { ...base.global.providers, llm: 'openrouter' } },
      providerConfigs: {
        ...base.providerConfigs,
        openrouter: { ...base.providerConfigs.openrouter, apiKey: 'sk-live', model: 'anthropic/claude-sonnet-4' },
        cerebras: { ...base.providerConfigs.cerebras, apiKey: 'csk-live' },
      },
    });
    const request = createSettingsSaveRequest(base, draft);
    expect(request.provider).toBe('openrouter');
    expect(request.openrouter).toEqual({
      api_key: 'sk-live',
      model: 'anthropic/claude-sonnet-4',
      context_size: 128000,
      thinking_budget: 0,
    });
    expect(request.cerebras).toEqual({ api_key: 'csk-live', model: 'llama-3.3-70b-versatile' });
    expect(request.settings_profile_patch.providerConfigs?.openrouter?.apiKey).toBeUndefined();
    expect(request.settings_profile_patch.providerConfigs?.cerebras?.apiKey).toBeUndefined();
    expect(JSON.stringify(request.settings_profile_patch)).not.toContain('sk-live');
  });

  it('persists ChatGPT Codex settings only as non-secret typed profile data', () => {
    const base = migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT);
    const draft = migrateSettingsDocument({
      ...base,
      global: { ...base.global, providers: { ...base.global.providers, llm: 'chatgpt_codex' } },
      providerConfigs: {
        ...base.providerConfigs,
        chatgptCodex: {
          ...base.providerConfigs.chatgptCodex,
          model: 'gpt-5.6-sol',
          reasoningEffort: 'high',
          fastMode: true,
          codexPath: 'C:/tools/codex.exe',
        },
      },
    });

    const request = createSettingsSaveRequest(base, draft);

    expect(request.provider).toBe('chatgpt_codex');
    expect(request.settings_profile_patch.providerConfigs?.chatgptCodex).toEqual({
      model: 'gpt-5.6-sol',
      reasoningEffort: 'high',
      fastMode: true,
      codexPath: 'C:/tools/codex.exe',
      transport: 'app_server',
    });
    expect(JSON.stringify(request)).not.toContain('api_key');
    expect(JSON.stringify(request)).not.toContain('apiKey');
  });
});
