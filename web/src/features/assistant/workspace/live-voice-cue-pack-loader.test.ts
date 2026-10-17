/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { initializeLiveVoiceCueAssetBridge } from './live-voice-cue-asset-bridge';
import {
  clearVoiceCueAssets,
  hasVoiceCueSamples,
  resolveCueSamples,
} from './live-voice-cue-bank';
import {
  ensureLiveVoiceCuePack,
  resetLiveVoiceCuePackLoaderState,
} from './live-voice-cue-pack-loader';

class FakeAudioContext {
  static decodeCalls = 0;
  state: AudioContextState = 'running';
  sampleRate = 48_000;
  close = vi.fn(async () => {
    this.state = 'closed';
  });
  decodeAudioData = vi.fn(async (_bytes: ArrayBuffer) => {
    FakeAudioContext.decodeCalls += 1;
    return {
      length: 4,
      numberOfChannels: 1,
      sampleRate: 24_000,
      getChannelData: () => new Float32Array([0, 0.25, -0.25, 0]),
    } as unknown as AudioBuffer;
  });
}

let cleanupBridge: (() => void) | null = null;

beforeEach(() => {
  FakeAudioContext.decodeCalls = 0;
  resetLiveVoiceCuePackLoaderState();
  clearVoiceCueAssets();
  cleanupBridge = initializeLiveVoiceCueAssetBridge();
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext);
});

afterEach(() => {
  cleanupBridge?.();
  cleanupBridge = null;
  clearVoiceCueAssets();
  resetLiveVoiceCuePackLoaderState();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice cue pack loader', () => {
  it('fetches, decodes, and registers a selected voice pack', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/manifest')) {
        return new Response(JSON.stringify({
          schema_version: 1,
          voice_id: 'Jinx',
          available: true,
          assets: [{
            cue_id: 'hmm',
            variant_id: 'hmm-v1',
            url: '/api/voice/cues/Jinx/hmm/hmm-v1.wav',
            size_bytes: 128,
            sha256: 'a'.repeat(64),
          }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(new Uint8Array([1, 2, 3, 4]), {
        status: 200,
        headers: { 'Content-Type': 'audio/wav' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const loaded = await ensureLiveVoiceCuePack('Jinx');

    expect(loaded).toEqual({
      voiceId: 'Jinx',
      available: true,
      loadedCount: 1,
      skippedCount: 0,
      reason: 'loaded',
    });
    expect(hasVoiceCueSamples('Jinx', 'hmm', 'hmm-v1')).toBe(true);
    expect(resolveCueSamples('hmm', 'hmm-v1', 24_000, {
      voiceId: 'Jinx',
      allowProceduralFallback: false,
    })?.source).toBe('voice_asset');
    expect(FakeAudioContext.decodeCalls).toBe(1);
  });

  it('clears stale voice assets when the server reports no pack', async () => {
    window.dispatchEvent(new CustomEvent('omnix:voice-cue-assets-ready', {
      detail: {
        voiceId: 'Maya',
        cueId: 'mhm',
        variantId: 'mhm-v1',
        samples: [0, 0.1, -0.1, 0],
        sampleRate: 24_000,
      },
    }));
    expect(hasVoiceCueSamples('Maya', 'mhm', 'mhm-v1')).toBe(true);
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      schema_version: 1,
      voice_id: 'Maya',
      available: false,
      assets: [],
    }), { status: 200 })));

    const loaded = await ensureLiveVoiceCuePack('Maya');

    expect(loaded.reason).toBe('pack_unavailable');
    expect(hasVoiceCueSamples('Maya', 'mhm', 'mhm-v1')).toBe(false);
  });

  it('does not decode the same immutable fingerprint twice', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/manifest')) {
        return new Response(JSON.stringify({
          schema_version: 1,
          voice_id: 'Jinx',
          available: true,
          assets: [{
            cue_id: 'inhale',
            variant_id: 'inhale-v1',
            url: '/api/voice/cues/Jinx/inhale/inhale-v1.wav',
            size_bytes: 128,
            sha256: 'b'.repeat(64),
          }],
        }), { status: 200 });
      }
      return new Response(new Uint8Array([1, 2, 3, 4]), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    expect((await ensureLiveVoiceCuePack('Jinx')).reason).toBe('loaded');
    expect((await ensureLiveVoiceCuePack('Jinx')).reason).toBe('already_loaded');
    expect(FakeAudioContext.decodeCalls).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('rejects cross-surface or oversized manifest entries before fetching audio', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      schema_version: 1,
      voice_id: 'Jinx',
      available: true,
      assets: [
        {
          cue_id: 'hmm',
          variant_id: 'hmm-v1',
          url: 'https://example.com/cue.wav',
          size_bytes: 128,
          sha256: 'c'.repeat(64),
        },
        {
          cue_id: 'mhm',
          variant_id: 'mhm-v1',
          url: '/api/voice/cues/Jinx/mhm/mhm-v1.wav',
          size_bytes: 9_000_000,
          sha256: 'd'.repeat(64),
        },
      ],
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const loaded = await ensureLiveVoiceCuePack('Jinx');

    expect(loaded.reason).toBe('pack_unavailable');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(FakeAudioContext.decodeCalls).toBe(0);
  });
});
