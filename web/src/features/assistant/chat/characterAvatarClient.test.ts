import { afterEach, describe, expect, it, vi } from 'vitest';
import { characterAvatarClient } from './characterAvatarClient';

afterEach(() => vi.unstubAllGlobals());

describe('characterAvatarClient optional avatar pack lookup', () => {
  it('returns null through a successful optional lookup instead of issuing an expected 404', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(input.toString()).toBe('/api/characters/anaka/avatar-pack/optional');
      return Response.json(null);
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(characterAvatarClient.optionalPack('anaka')).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('returns an existing avatar pack from the optional route', async () => {
    const fetchMock = vi.fn(async () => Response.json({
      character_id: 'anaka',
      version: 1,
      renderer: 'sprite',
      render_mode: 'audio_envelope',
      base_asset_id: 'image:anaka',
      mouth_frames: { closed: 'image:anaka' },
      blink_frames: {},
      expression_frames: {},
      outfit_frames: {},
      background_asset_ids: {},
      rig_asset_id: null,
      created_at: '2026-07-31T00:00:00Z',
      updated_at: '2026-07-31T00:00:00Z',
    }));
    vi.stubGlobal('fetch', fetchMock);

    const pack = await characterAvatarClient.optionalPack('anaka');

    expect(pack?.character_id).toBe('anaka');
    expect(pack?.base_asset_id).toBe('image:anaka');
  });
});
