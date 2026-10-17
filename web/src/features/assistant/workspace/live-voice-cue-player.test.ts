import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearVoiceCueAssets,
  registerVoiceCueSamples,
} from './live-voice-cue-bank';
import {
  closeLowLatencyVoiceCuePlayer,
  playLowLatencyVoiceCue,
  stopLowLatencyVoiceCue,
} from './live-voice-cue-player';
import { resetLiveVoiceHumanizationFlags } from './live-voice-humanization-flags';

class FakeSource extends EventTarget {
  buffer: AudioBuffer | null = null;
  connect = vi.fn();
  disconnect = vi.fn();
  start = vi.fn();
  stop = vi.fn(() => this.dispatchEvent(new Event('ended')));

  finish(): void {
    this.dispatchEvent(new Event('ended'));
  }
}

class FakeAudioContext {
  static sources: FakeSource[] = [];
  state: AudioContextState = 'running';
  sampleRate = 24_000;
  destination = {} as AudioDestinationNode;
  resume = vi.fn(async () => undefined);
  close = vi.fn(async () => {
    this.state = 'closed';
  });
  createBuffer = vi.fn((_channels: number, length: number, sampleRate: number) => ({
    length,
    sampleRate,
    copyToChannel: vi.fn(),
  } as unknown as AudioBuffer));
  createBufferSource = vi.fn(() => {
    const source = new FakeSource();
    FakeAudioContext.sources.push(source);
    return source as unknown as AudioBufferSourceNode;
  });
  createGain = vi.fn(() => ({
    gain: { value: 1 },
    connect: vi.fn(),
  } as unknown as GainNode));
}

beforeEach(() => {
  FakeAudioContext.sources = [];
  clearVoiceCueAssets();
  resetLiveVoiceHumanizationFlags();
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext);
});

afterEach(async () => {
  await closeLowLatencyVoiceCuePlayer();
  clearVoiceCueAssets();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function registerCue(cueId: 'mhm' | 'inhale', variantId: string): void {
  registerVoiceCueSamples({
    voiceId: 'Jinx',
    cueId,
    variantId,
    samples: new Float32Array([0, 0.1, -0.1, 0]),
    sampleRate: 24_000,
  });
}

describe('low-latency cue player', () => {
  it('publishes voice-asset lifecycle without canonical speech progress', async () => {
    registerCue('mhm', 'mhm-v1');
    const events: Array<Record<string, unknown>> = [];
    const listener: EventListener = (event) => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener('omnix:live-voice-cue-segment', listener);

    const played = playLowLatencyVoiceCue('mhm', 'mhm-v1', 0.75, { voiceId: 'Jinx' });
    await vi.waitFor(() => expect(FakeAudioContext.sources).toHaveLength(1));
    FakeAudioContext.sources[0].finish();
    await expect(played).resolves.toBe(true);

    expect(events.map((event) => event.type)).toEqual(['segment_started', 'segment_completed']);
    expect(events.every((event) => event.segment_kind === 'cue')).toBe(true);
    expect(events.every((event) => event.semantic_speech_samples === 0)).toBe(true);
    expect(events.every((event) => event.cue_source === 'voice_asset')).toBe(true);
    expect(events.every((event) => event.voice_id === 'jinx')).toBe(true);
    window.removeEventListener('omnix:live-voice-cue-segment', listener);
  });

  it('reports interruption exactly once for a voice asset', async () => {
    registerCue('inhale', 'inhale-v1');
    const events: Array<Record<string, unknown>> = [];
    const listener: EventListener = (event) => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener('omnix:live-voice-cue-segment', listener);

    void playLowLatencyVoiceCue('inhale', 'inhale-v1', 0.75, { voiceId: 'Jinx' });
    await vi.waitFor(() => expect(FakeAudioContext.sources).toHaveLength(1));
    stopLowLatencyVoiceCue('test_interrupt');

    expect(events.map((event) => event.type)).toEqual(['segment_started', 'segment_interrupted']);
    expect(events.at(-1)?.reason).toBe('test_interrupt');
    window.removeEventListener('omnix:live-voice-cue-segment', listener);
  });

  it('skips missing voice assets instead of silently using procedural audio', async () => {
    const skipped: Array<Record<string, unknown>> = [];
    const listener: EventListener = (event) => {
      skipped.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener('omnix:live-voice-cue-skipped', listener);

    await expect(playLowLatencyVoiceCue('mhm', 'mhm-v1', 0.75, { voiceId: 'Jinx' }))
      .resolves.toBe(false);
    expect(FakeAudioContext.sources).toHaveLength(0);
    expect(skipped).toEqual([
      expect.objectContaining({
        cue_id: 'mhm',
        voice_id: 'Jinx',
        reason: 'voice_asset_unavailable',
        procedural_fallback_allowed: false,
      }),
    ]);
    window.removeEventListener('omnix:live-voice-cue-skipped', listener);
  });
});
