import { sendGatewayCall } from '../../../api/http';
import { api } from './gateway';
import type { components } from './generated';

export type SaveStoryAssetRequest = components['schemas']['SaveStoryAssetRequest'];
export type SavedStoryAssetResponse = components['schemas']['SavedStoryAssetResponse'];

/** The storyteller's gateway calls (PA-2.4). */
export const storyApiClient = {
  async saveStoryAsset(request: SaveStoryAssetRequest): Promise<SavedStoryAssetResponse> {
    return sendGatewayCall(() => api.POST('/api/assets/story', { body: request }));
  },
};
