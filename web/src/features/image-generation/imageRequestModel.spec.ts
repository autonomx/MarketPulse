import { describe, expect, it } from 'vitest';
import {
  buildImageGenerateInput,
  imagePresetById,
  imageRequestDefaultValues,
  validateImageDimension,
} from './imageRequestModel';

const defaults = { providerId: 'image:flux_klein', width: 768, height: 768, unloadAfterGeneration: true };

describe('image request model', () => {
  it('builds normalized provider controls', () => {
    const values = {
      ...imageRequestDefaultValues(defaults),
      prompt: '  moonlit ruins  ',
      negativePrompt: '  blur  ',
      style: '  cinematic  ',
      seed: '42',
      steps: '30',
      guidanceScale: '4.5',
      noCache: true,
    };

    expect(buildImageGenerateInput(values, defaults)).toEqual({
      prompt: 'moonlit ruins',
      negative_prompt: 'blur',
      provider_id: 'image:flux_klein',
      width: 768,
      height: 768,
      style: 'cinematic',
      reference_asset_ids: [],
      seed: 42,
      steps: 30,
      guidance_scale: 4.5,
      unload_after_generation: true,
      no_cache: true,
    });
  });

  it('exposes presets and validates dimensions', () => {
    expect(imagePresetById('portrait-768')).toMatchObject({ width: 768, height: 1024 });
    expect(validateImageDimension('1024')).toBe(true);
    expect(validateImageDimension('770')).toBe('Use a multiple of 64.');
    expect(validateImageDimension('64')).toBe('Use a value from 128 to 4096.');
  });
});
