import { sendGatewayCall } from '../../../api/http';
import type { AssetListResponse, JobListResponse } from '../../../api/client';
import { api } from './gateway';
import type { components } from './generated';

export type TtsEnginesResponse = components['schemas']['TtsEnginesResponse'];
export type TtsEngineSwitchPhase = TtsEnginesResponse['switch']['phase'];

/** The voice feature's gateway calls: the voice library and voice job history (PA-2.4). */
export const voiceApiClient = {
  /** Voice Studio's bounded job history: recent voice and voice-cloning jobs only. */
  async listVoiceJobSummaries(limit = 40): Promise<JobListResponse> {
    return sendGatewayCall(() => api.GET('/api/jobs/voice-summaries', { params: { query: { limit } } }));
  },

  async listVoiceLibrary(): Promise<AssetListResponse> {
    return sendGatewayCall(() => api.GET('/api/voice-library'));
  },

  /** The local TTS engines, which one Omnix uses, and the progress of the last switch. */
  async listTtsEngines(): Promise<TtsEnginesResponse> {
    return sendGatewayCall(() => api.GET('/api/tts/engines'));
  },

  /** Make an engine Omnix's default TTS; the old one unloads and the new one loads in the background. */
  async selectTtsEngine(providerId: string): Promise<TtsEnginesResponse> {
    return sendGatewayCall(() => api.POST('/api/tts/engines/select', { body: { provider_id: providerId } }));
  },
};
