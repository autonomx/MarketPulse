import { describe, expect, it } from 'vitest';
import { liveVoiceVisualScales, normalizeLiveVoiceLevel, smoothLiveVoiceLevel } from './live-voice-level';

describe('live voice level', () => {
  it('normalizes rms values', () => {
    expect(normalizeLiveVoiceLevel(0)).toBe(0);
    expect(normalizeLiveVoiceLevel(0.06)).toBeCloseTo(0.5);
    expect(normalizeLiveVoiceLevel(1)).toBe(1);
  });

  it('smooths the current value', () => {
    expect(smoothLiveVoiceLevel(0, 0.12)).toBeCloseTo(0.28);
    expect(smoothLiveVoiceLevel(1, 0)).toBeCloseTo(0.72);
  });

  it('creates bounded visual scales', () => {
    expect(liveVoiceVisualScales(0).inputScale).toBe(0.08);
    expect(liveVoiceVisualScales(1).inputScale).toBe(1);
    expect(liveVoiceVisualScales(1).barScale).toBeCloseTo(1.12);
  });
});
