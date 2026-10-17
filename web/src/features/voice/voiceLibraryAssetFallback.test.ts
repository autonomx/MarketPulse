import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  installVoiceLibraryAssetFallback,
  resetVoiceLibraryAssetFallbackForTests,
} from './voiceLibraryAssetFallback';
import { pipelineFetch } from '../../api/fetchPipeline';
import { OmnixApiClient } from '../../api/client';

const jsonResponse = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const voice = (name: string, source: string) => ({ id: `voice-cloning:${name}`, module: 'voice-cloning', type: 'voice_profile', metadata: { profile_name: name, source } });
const urlOf = (input: RequestInfo | URL) => (input instanceof Request ? input.url : String(input));

afterEach(() => {
  resetVoiceLibraryAssetFallbackForTests();
  vi.restoreAllMocks();
});

describe('voiceLibraryAssetFallback', () => {
  it('merges direct voice profiles when the aggregate asset response has none', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/voice-library')) {
        return new Response(JSON.stringify({
          assets: [
            {
              id: 'voice-cloning:Maya',
              module: 'voice-cloning',
              type: 'voice_profile',
              file_name: 'Maya.wav',
              metadata: { profile_name: 'Maya' },
            },
          ],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        assets: [
          { id: 'image:one', module: 'image', type: 'image' },
        ],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    installVoiceLibraryAssetFallback(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets');
    const body = await response.json() as { assets: Array<{ id: string; type: string }> };

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(body.assets.map((asset) => asset.id)).toEqual(['image:one', 'voice-cloning:Maya']);
    expect(response.headers.get('x-omnix-voice-library-merged')).toBe('true');
  });

  it('merges missing direct voices even when aggregate assets already include a partial voice list', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/api/voice-library')) {
        return new Response(JSON.stringify({
          assets: [
            {
              id: 'voice-cloning:Maya',
              module: 'voice-cloning',
              type: 'voice_profile',
              metadata: { voice_clone_id: 'Maya', canonical: true },
            },
            {
              id: 'voice-cloning:Jinx',
              module: 'voice-cloning',
              type: 'voice_profile',
              metadata: { voice_clone_id: 'Jinx', canonical: true },
            },
          ],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({
        assets: [
          {
            id: 'voice-cloning:Maya',
            module: 'voice-cloning',
            type: 'voice_profile',
            metadata: { voice_clone_id: 'maya', canonical: false },
          },
          { id: 'image:one', module: 'image', type: 'image' },
        ],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    installVoiceLibraryAssetFallback(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets');
    const body = await response.json() as {
      assets: Array<{ id: string; metadata?: { voice_clone_id?: string; canonical?: boolean } }>;
    };

    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(body.assets.map((asset) => asset.id)).toEqual([
      'voice-cloning:Maya',
      'image:one',
      'voice-cloning:Jinx',
    ]);
    expect(body.assets[0]?.metadata).toEqual({ voice_clone_id: 'Maya', canonical: true });
    expect(response.headers.get('x-omnix-voice-library-merged')).toBe('true');
  });

  it('preserves the aggregate response when the direct endpoint is unavailable', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/api/voice-library')) {
        return new Response('not found', { status: 404 });
      }
      return new Response(JSON.stringify({
        assets: [{ id: 'image:one', module: 'image', type: 'image' }],
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    installVoiceLibraryAssetFallback(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets');
    const body = await response.json() as { assets: Array<{ id: string }> };

    expect(response.status).toBe(200);
    expect(body.assets.map((asset) => asset.id)).toEqual(['image:one']);
  });

  it('lists each direct voice once across a paginated asset listing', async () => {
    // The gateway pages newest first: the DB-backed Maya row only arrives on the second page.
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(urlOf(input), 'http://localhost/');
      if (url.pathname === '/api/voice-library') {
        return jsonResponse({ assets: [voice('Maya', 'direct'), voice('Jinx', 'direct')] });
      }
      if (!url.searchParams.get('cursor')) {
        return jsonResponse({ assets: [{ id: 'image:one', module: 'image', type: 'image' }], next_cursor: 'page-2', has_more: true });
      }
      return jsonResponse({ assets: [voice('Maya', 'database'), voice('DbOnly', 'database'), { id: 'image:two', module: 'image', type: 'image' }], next_cursor: null, has_more: false });
    });

    installVoiceLibraryAssetFallback(fetchImpl as typeof fetch);
    const listing = await new OmnixApiClient().listAssets();
    const ids = listing.assets.map((asset) => asset.id);

    expect(ids).toEqual(['image:one', 'voice-cloning:Maya', 'voice-cloning:Jinx', 'voice-cloning:DbOnly', 'image:two']);
    expect(new Set(ids).size).toBe(ids.length);
    expect((listing.assets[1]?.metadata as { source?: string }).source).toBe('direct');
  });

  it('does not add voices to a listing filtered to another asset type', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (urlOf(input).includes('/api/voice-library')) {
        return jsonResponse({ assets: [voice('Maya', 'direct')] });
      }
      return jsonResponse({ assets: [{ id: 'image:one', module: 'image', type: 'image' }] });
    });

    installVoiceLibraryAssetFallback(fetchImpl as typeof fetch);
    const response = await pipelineFetch('/api/assets?type=image');
    const body = await response.json() as { assets: Array<{ id: string }> };

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(body.assets.map((asset) => asset.id)).toEqual(['image:one']);
  });
});
