import { pipelineFetch } from '../../../api/fetchPipeline';

import type { components } from './generated';
export type RpgCampaignLoreResponse = components['schemas']['RpgCampaignLoreResponse'];

async function request<T>(path: string): Promise<T> {
  const response = await pipelineFetch(path, {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Campaign lore request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const rpgCampaignLoreClient = {
  read(sessionId: string): Promise<RpgCampaignLoreResponse> {
    return request<RpgCampaignLoreResponse>(
      `/api/rpg/sessions/${encodeURIComponent(sessionId)}/lore`,
    );
  },
};
