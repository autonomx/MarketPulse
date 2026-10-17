import { describe, expect, it } from 'vitest';

import { LiveVoicePreSpeechBuffer } from './live-voice-pre-speech-buffer';

describe('LiveVoicePreSpeechBuffer', () => {
  it('retains only the newest samples in capture order', () => {
    const buffer = new LiveVoicePreSpeechBuffer(5);
    buffer.push(new Float32Array([1, 2, 3]));
    buffer.push(new Float32Array([4, 5, 6, 7]));

    expect(buffer.bufferedSamples).toBe(5);
    expect(buffer.capacitySamples).toBe(5);
    expect(buffer.drain().flatMap((frame) => [...frame])).toEqual([3, 4, 5, 6, 7]);
    expect(buffer.bufferedSamples).toBe(0);
  });

  it('copies caller-owned samples and clears without draining', () => {
    const buffer = new LiveVoicePreSpeechBuffer(4);
    const frame = new Float32Array([0.1, 0.2]);
    buffer.push(frame);
    frame[0] = 9;

    expect(buffer.drain()[0][0]).toBeCloseTo(0.1);
    buffer.push(new Float32Array([1, 2]));
    buffer.clear();
    expect(buffer.drain()).toEqual([]);
  });
});
