import type { CharacterAvatarPack } from './characterClient';
import type { ImageReferenceUploadResponse } from '../../image-generation';
import type { components } from '../api/generated';
import { ApiError } from '../../../api/errors';
import { unwrapAs } from '../../../api/http';
import { uploadBinary } from '../../../api/transport';
import { api } from '../api/gateway';

export type AvatarGenerationStatus = 'queued' | 'generating_base' | 'generating_variants' | 'completed' | 'failed';
export type VisemeGenerationStatus = 'generating' | 'completed' | 'failed';

export type CreateCharacterAvatarGenerationInput = components['schemas']['CreateCharacterAvatarGenerationRequest-Input'];

export type CharacterAvatarGenerationBatch = components['schemas']['CharacterAvatarGenerationBatch'];

export type CharacterVisemeGenerationBatch = components['schemas']['CharacterVisemeGenerationBatch'];

export type ClonedVoiceBackfillItem = ClonedVoiceBackfillResponse['items'][number];

export type ClonedVoiceBackfillResponse = components['schemas']['BackfillClonedVoiceCharactersResponse'];

export type UploadedAvatarSourceAsset = ImageReferenceUploadResponse['asset'];

export type Live2DModelCatalogItem = components['schemas']['Live2DModelCatalogItem'];

export type Live2DModelCatalogResponse = components['schemas']['Live2DModelCatalogResponse'];

export type Live2DAvatarActionResponse = components['schemas']['Live2DAvatarActionResponse'];

// The gateway's detail, or the raw body when it is not JSON.
function avatar<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  return unwrapAs(call, (error) => error.detail || `Avatar request failed with status ${error.status}.`);
}

const characterPath = (characterId: string) => ({ character_id: characterId });

export const characterAvatarClient = {
  async optionalPack(characterId: string): Promise<CharacterAvatarPack | null> {
    return (await avatar(api.GET('/api/characters/{character_id}/avatar-pack/optional', { params: { path: characterPath(characterId) } }))) ?? null;
  },
  async uploadSourceImage(file: File): Promise<UploadedAvatarSourceAsset> {
    try {
      const payload = await uploadBinary<ImageReferenceUploadResponse>('/api/image-generation/references', file, {
        query: { filename: file.name || 'avatar-source' },
      });
      return payload.asset;
    } catch (error) {
      if (error instanceof ApiError) throw new Error(error.detail || `Avatar request failed with status ${error.status}.`);
      throw error;
    }
  },
  createGeneration(characterId: string, input: CreateCharacterAvatarGenerationInput): Promise<CharacterAvatarGenerationBatch> {
    return avatar(api.POST('/api/characters/{character_id}/avatar-generations', { params: { path: characterPath(characterId) }, body: input }));
  },
  generation(batchId: string): Promise<CharacterAvatarGenerationBatch> {
    return avatar(api.GET('/api/character-avatar-generations/{batch_id}', { params: { path: { batch_id: batchId } } }));
  },
  createVisemeGeneration(characterId: string): Promise<CharacterVisemeGenerationBatch> {
    return avatar(api.POST('/api/characters/{character_id}/avatar-visemes', { params: { path: characterPath(characterId) } }));
  },
  visemeGeneration(batchId: string): Promise<CharacterVisemeGenerationBatch> {
    return avatar(api.GET('/api/character-avatar-visemes/{batch_id}', { params: { path: { batch_id: batchId } } }));
  },
  live2dCatalog(characterId: string): Promise<Live2DModelCatalogResponse> {
    return avatar(api.GET('/api/characters/{character_id}/live2d-models', { params: { path: characterPath(characterId) } }));
  },
  activateLive2d(characterId: string, input: components['schemas']['ActivateLive2DAvatarRequest']): Promise<Live2DAvatarActionResponse> {
    return avatar(api.POST('/api/characters/{character_id}/live2d-avatar', { params: { path: characterPath(characterId) }, body: input }));
  },
  disableLive2d(characterId: string): Promise<Live2DAvatarActionResponse> {
    return avatar(api.POST('/api/characters/{character_id}/live2d-avatar/disable', { params: { path: characterPath(characterId) } }));
  },
  backfillClonedVoices(input: components['schemas']['BackfillClonedVoiceCharactersRequest']): Promise<ClonedVoiceBackfillResponse> {
    return avatar(api.POST('/api/characters/backfill-cloned-voices', { body: input }));
  },
};

export function characterAvatarAssetUrl(assetId: string): string {
  return `/api/assets/${encodeURIComponent(assetId)}/file`;
}
