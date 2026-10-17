import { describe, expect, it } from 'vitest';
import type { AssetListResponse } from '../../api/client';
import { imageAssetUrl, selectLatestImageAsset } from './ImageGenerationWorkspace';

type ImageAsset = AssetListResponse['assets'][number];

const asset = {
  id: 'image:test',
  module: 'image-generation',
  type: 'image',
  mime_type: 'image/png',
  file_name: 'test.png',
  source_job_id: 'job:test',
  created_at: '2026-07-01T00:00:00Z',
} as ImageAsset;

describe('latest image result', () => {
  it('selects a submitted job asset', () => {
    expect(selectLatestImageAsset([asset], [], null, 'job:test')?.id).toBe('image:test');
  });

  it('builds encoded URLs', () => {
    expect(imageAssetUrl('image:test')).toBe('/api/assets/image%3Atest/file?preview=true');
    expect(imageAssetUrl('image:test', true)).toBe('/api/assets/image%3Atest/file?download=true');
  });
});
