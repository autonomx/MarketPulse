import type { components } from './api/generated';
import { createGatewayClient } from '../../api/http';
import { migrateSettingsDocument, settingsPatch } from './settingsMerge';
import type { SettingsDocument } from './settingsDocumentTypes';
import { api } from './api/gateway';

export type SettingsApiPayload = components['schemas']['SettingsPayload'];

export type SettingsProfileEnvelope = {
  profile: SettingsDocument;
  legacy: SettingsApiPayload;
};

/** A Settings Control Center save (POST /api/settings/profile); the profile patch is typed here. */
export type SettingsProfileSaveRequest = components['schemas']['SettingsProfileSaveRequest'] & {
  settings_profile_patch: Partial<SettingsDocument>;
};

export class SettingsProfileApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export type SettingsFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const SETTINGS_LOAD_MAX_ATTEMPTS = 3;
const SETTINGS_LOAD_RETRY_DELAY_MS = 100;

function waitForSettingsRetry(): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, SETTINGS_LOAD_RETRY_DELAY_MS));
}

// The profile routes mask API keys on read and send them to the secret store on save.
function settingsClient(fetcher?: SettingsFetch) {
  return fetcher ? createGatewayClient({ fetchImpl: fetcher as typeof fetch }) : api;
}

export async function loadSettingsProfile(fetcherOrContext?: SettingsFetch | unknown): Promise<SettingsProfileEnvelope> {
  const client = settingsClient(typeof fetcherOrContext === 'function' ? fetcherOrContext as SettingsFetch : undefined);
  for (let attempt = 1; attempt <= SETTINGS_LOAD_MAX_ATTEMPTS; attempt += 1) {
    try {
      const { data, response } = await client.GET('/api/settings/profile');
      if (!response.ok || !data) throw new SettingsProfileApiError('Settings request failed.', response.status);
      const legacy: SettingsApiPayload = data;
      const raw = legacy.settings?.settings_control_center;
      return { profile: migrateSettingsDocument(raw), legacy };
    } catch (error) {
      const nonRetryable = error instanceof SettingsProfileApiError && error.status < 500;
      if (nonRetryable || attempt === SETTINGS_LOAD_MAX_ATTEMPTS) throw error;
      await waitForSettingsRetry();
    }
  }
  throw new SettingsProfileApiError('Settings request failed.', 500);
}

export async function saveSettingsProfile(request: SettingsProfileSaveRequest, fetcher?: SettingsFetch): Promise<void> {
  const { data: result, response } = await settingsClient(fetcher).POST('/api/settings/profile', { body: request });
  if (!response.ok || !result) throw new SettingsProfileApiError('Settings save failed.', response.status);
  if (result.success !== true) throw new SettingsProfileApiError('Settings were not saved because the profile changed or validation failed.', 409);
}

function profilePatchWithoutSecrets(patch: Partial<SettingsDocument>): Partial<SettingsDocument> {
  if (!patch.providerConfigs) return patch;
  const providerConfigs = { ...patch.providerConfigs };
  if (providerConfigs.openrouter) {
    providerConfigs.openrouter = { ...providerConfigs.openrouter };
    delete (providerConfigs.openrouter as Partial<typeof providerConfigs.openrouter>).apiKey;
  }
  if (providerConfigs.cerebras) {
    providerConfigs.cerebras = { ...providerConfigs.cerebras };
    delete (providerConfigs.cerebras as Partial<typeof providerConfigs.cerebras>).apiKey;
  }
  return { ...patch, providerConfigs };
}

export function createSettingsSaveRequest(base: SettingsDocument, draft: SettingsDocument): SettingsProfileSaveRequest {
  const request: SettingsProfileSaveRequest = { base_revision: base.revision, settings_profile_patch: profilePatchWithoutSecrets(settingsPatch(base, draft)) };
  if (JSON.stringify(base.global.providers) !== JSON.stringify(draft.global.providers)) {
    request.provider = draft.global.providers.llm;
    request.audio_provider_tts = draft.global.providers.tts;
    request.audio_provider_stt = draft.global.providers.stt;
  }
  if (JSON.stringify(base.providerConfigs.lmstudio) !== JSON.stringify(draft.providerConfigs.lmstudio)) {
    request.lmstudio = {
      base_url: draft.providerConfigs.lmstudio.baseUrl,
      model: draft.providerConfigs.lmstudio.model,
      direct: draft.providerConfigs.lmstudio.direct,
    };
  }
  if (JSON.stringify(base.providerConfigs.openrouter) !== JSON.stringify(draft.providerConfigs.openrouter)) {
    request.openrouter = {
      api_key: draft.providerConfigs.openrouter.apiKey,
      model: draft.providerConfigs.openrouter.model,
      context_size: draft.providerConfigs.openrouter.contextSize,
      thinking_budget: draft.providerConfigs.openrouter.thinkingBudget,
    };
  }
  if (JSON.stringify(base.providerConfigs.cerebras) !== JSON.stringify(draft.providerConfigs.cerebras)) {
    request.cerebras = {
      api_key: draft.providerConfigs.cerebras.apiKey,
      model: draft.providerConfigs.cerebras.model,
    };
  }
  if (JSON.stringify(base.providerConfigs.llamacpp) !== JSON.stringify(draft.providerConfigs.llamacpp)) {
    request.llamacpp = {
      base_url: draft.providerConfigs.llamacpp.baseUrl,
      model: draft.providerConfigs.llamacpp.model,
      download_location: draft.providerConfigs.llamacpp.downloadLocation,
      auto_start: draft.providerConfigs.llamacpp.autoStart,
    };
  }
  if (JSON.stringify(base.providerConfigs.fasterQwen3Tts) !== JSON.stringify(draft.providerConfigs.fasterQwen3Tts)) {
    request['faster-qwen3-tts'] = {
      model_name: draft.providerConfigs.fasterQwen3Tts.modelName,
      model_dir: draft.providerConfigs.fasterQwen3Tts.modelDir,
      device: draft.providerConfigs.fasterQwen3Tts.device,
      dtype: draft.providerConfigs.fasterQwen3Tts.dtype,
      chunk_size: draft.providerConfigs.fasterQwen3Tts.chunkSize,
      non_streaming_mode: draft.providerConfigs.fasterQwen3Tts.nonStreamingMode,
    };
  }
  if (JSON.stringify(base.providerConfigs.parakeet) !== JSON.stringify(draft.providerConfigs.parakeet)) {
    request.parakeet = { base_url: draft.providerConfigs.parakeet.baseUrl };
  }
  if (JSON.stringify(base.providerConfigs.fluxKlein) !== JSON.stringify(draft.providerConfigs.fluxKlein)) {
    request.image = {
      provider: 'flux_klein',
      flux_klein: {
        enabled: draft.providerConfigs.fluxKlein.enabled,
        repo_id: draft.providerConfigs.fluxKlein.repoId,
        local_dir: draft.providerConfigs.fluxKlein.localDir,
        device: draft.providerConfigs.fluxKlein.device,
        torch_dtype: draft.providerConfigs.fluxKlein.torchDtype,
        prefer_local_files: draft.providerConfigs.fluxKlein.preferLocalFiles,
        allow_repo_fallback: draft.providerConfigs.fluxKlein.allowRepoFallback,
      },
    };
  }
  return request;
}
