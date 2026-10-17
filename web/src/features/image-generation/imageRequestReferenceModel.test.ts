import { describe, expect, it } from 'vitest';
import { buildImageGenerateInput, imageRequestDefaultValues } from './imageRequestModel';

describe('image-to-image request mapping', () => {
  it('sends selected reference assets and bypasses reusable result cache', () => {
    const defaults = { providerId: 'image:flux_klein', width: 768, height: 768, unloadAfterGeneration: false };
    const values = {
      ...imageRequestDefaultValues(defaults),
      prompt: 'Keep the face and change the outfit',
      referenceAssetIds: ['image:one'],
    };

    const input = buildImageGenerateInput(values, defaults);

    expect(input.reference_asset_ids).toEqual(['image:one']);
    expect(input.no_cache).toBe(true);
  });
});
