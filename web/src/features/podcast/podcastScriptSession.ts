import { omnixApiClient, type CreateChatSessionRequest } from '../../api/client';
import { podcastDefaults } from '../settings';
import { loadSettingsProfile } from '../settings';

/**
 * Create the chat session a podcast script is drafted in, with the podcast
 * provider and model defaults from settings when the request names none.
 */
export async function createPodcastScriptSession(request: CreateChatSessionRequest) {
  try {
    const { profile } = await loadSettingsProfile();
    const defaults = podcastDefaults(profile);
    return await omnixApiClient.createChatSession({
      ...request,
      provider_id: request.provider_id || defaults.providerId || undefined,
      model_id: request.model_id || defaults.modelId || undefined,
    });
  } catch {
    return omnixApiClient.createChatSession(request);
  }
}
