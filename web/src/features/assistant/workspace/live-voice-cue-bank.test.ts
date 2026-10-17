import { afterEach, describe, expect, it } from 'vitest';

import {
  clearCueSampleCache,
  clearVoiceCueAssets,
  cloneCueSamples,
  cueVariantCount,
  cueVariantId,
  getCachedCueSamples,
  hasVoiceCueSamples,
  registerVoiceCueSamples,
  resolveCueSamples,
  unregisterVoiceCueSamples,
} from './live-voice-cue-bank';
import {
  resetLiveVoiceHumanizationFlags,
  writeLiveVoiceHumanizationFlags,
} from './live-voice-humanization-flags';

afterEach(() => {
  clearCueSampleCache();
  clearVoiceCueAssets();
  resetLiveVoiceHumanizationFlags();
  window.localStorage.clear();
});

describe('live voice cue bank', () => {
  it('caches stable procedural variants without sharing transferable clones', () => {
    const cached = getCachedCueSamples('hmm', 'hmm-v1', 24_000);
    expect(getCachedCueSamples('hmm', 'hmm-v1', 24_000)).toBe(cached);
    const clone = cloneCueSamples('hmm', 'hmm-v1', 24_000);
    expect(clone).not.toBe(cached);
    expect(Array.from(clone)).toEqual(Array.from(cached));
  });

  it('produces multiple bounded non-identical procedural variants', () => {
    expect(cueVariantCount()).toBe(4);
    for (const cue of ['mhm', 'hmm', 'inhale', 'amused_exhale'] as const) {
      const first = getCachedCueSamples(cue, cueVariantId(cue, 0), 24_000);
      const second = getCachedCueSamples(cue, cueVariantId(cue, 1), 24_000);
      expect(first.length).toBeGreaterThan(2_000);
      expect(second.length).not.toBe(first.length);
      expect(Math.max(...first)).toBeLessThan(0.2);
      expect(Math.min(...first)).toBeGreaterThan(-0.2);
    }
  });

  it('generates procedural cue duration in the requested sample domain', () => {
    const at24k = getCachedCueSamples('inhale', 'inhale-v1', 24_000);
    const at48k = getCachedCueSamples('inhale', 'inhale-v1', 48_000);
    expect(at48k.length).toBe(at24k.length * 2);
  });

  it('prefers registered voice assets and resamples them for playback', () => {
    expect(registerVoiceCueSamples({
      voiceId: 'Jinx',
      cueId: 'hmm',
      variantId: 'hmm-v1',
      samples: new Float32Array([0, 0.25, -0.25, 0]),
      sampleRate: 24_000,
    })).toBe(true);
    expect(hasVoiceCueSamples('jinx', 'hmm', 'hmm-v1')).toBe(true);

    const resolved = resolveCueSamples('hmm', 'hmm-v1', 48_000, {
      voiceId: 'JINX',
      allowProceduralFallback: false,
    });
    expect(resolved).toMatchObject({
      source: 'voice_asset',
      voiceId: 'jinx',
      sourceSampleRate: 24_000,
      playbackSampleRate: 48_000,
    });
    expect(resolved?.samples).toHaveLength(8);
    expect(unregisterVoiceCueSamples('Jinx', 'hmm', 'hmm-v1')).toBe(1);
    expect(hasVoiceCueSamples('Jinx', 'hmm', 'hmm-v1')).toBe(false);
  });

  it('skips missing assets unless procedural fallback is explicitly enabled', () => {
    expect(resolveCueSamples('hmm', 'hmm-v1', 24_000, { voiceId: 'Jinx' })).toBeNull();

    writeLiveVoiceHumanizationFlags({ proceduralCueFallback: true });
    const fallback = resolveCueSamples('hmm', 'hmm-v1', 24_000, { voiceId: 'Jinx' });
    expect(fallback).toMatchObject({
      source: 'procedural_fallback',
      voiceId: 'jinx',
      playbackSampleRate: 24_000,
    });
    expect(fallback?.samples.length).toBeGreaterThan(2_000);
  });
});
