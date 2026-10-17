import { describe, expect, it } from 'vitest';

import { FinalizationAudioBuffer } from './live-voice-finalization-buffer';

describe('FinalizationAudioBuffer', () => {
  it('preserves frame order and copies caller-owned samples', () => {
    const buffer = new FinalizationAudioBuffer(8);
    const first = new Float32Array([0.1, 0.2]);
    const second = new Float32Array([0.3, 0.4, 0.5]);

    expect(buffer.push(first).accepted).toBe(true);
    expect(buffer.push(second).accepted).toBe(true);
    first[0] = 9;

    const drained = buffer.drain();
    expect(drained).toHaveLength(2);
    expect([...drained[0]]).toHaveLength(2);
    expect([...drained[1]]).toHaveLength(3);
    expect(drained[0][0]).toBeCloseTo(0.1);
    expect(drained[0][1]).toBeCloseTo(0.2);
    expect(drained[1][0]).toBeCloseTo(0.3);
    expect(drained[1][1]).toBeCloseTo(0.4);
    expect(drained[1][2]).toBeCloseTo(0.5);
    expect(buffer.bufferedSamples).toBe(0);
  });

  it('fails closed instead of silently dropping an overflowing frame', () => {
    const buffer = new FinalizationAudioBuffer(3);
    expect(buffer.push(new Float32Array([1, 2])).accepted).toBe(true);
    const overflow = buffer.push(new Float32Array([3, 4]));

    expect(overflow).toEqual({ accepted: false, bufferedSamples: 2, maxSamples: 3 });
    expect(buffer.drain().map((frame) => [...frame])).toEqual([[1, 2]]);
  });
});
