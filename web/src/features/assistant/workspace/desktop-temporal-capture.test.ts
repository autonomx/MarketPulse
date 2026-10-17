import { describe, expect, it } from 'vitest';

import {
  formatRelativeTimestamp,
  retainTemporalFrames,
  scoreFrameDifference,
  selectTemporalFrames,
  type TemporalFrame,
} from './desktop-temporal-capture';

function frame(capturedAtMs: number, value: number): TemporalFrame {
  return {
    dataUrl: `data:image/jpeg;base64,${capturedAtMs}`,
    capturedAtMs,
    width: 640,
    height: 360,
    sample: new Uint8Array(16).fill(value),
  };
}

describe('desktop temporal capture', () => {
  it('keeps only the bounded six-second history', () => {
    const frames = Array.from({ length: 16 }, (_, index) => frame(index * 500, index));
    const retained = retainTemporalFrames(frames, 7_500, 6_000, 12);

    expect(retained).toHaveLength(12);
    expect(retained[0]?.capturedAtMs).toBe(2_000);
    expect(retained.at(-1)?.capturedAtMs).toBe(7_500);
  });

  it('selects a pre-change frame while the current frame represents the new state', () => {
    const frames = [frame(1_000, 10), frame(4_000, 10), frame(5_250, 180), frame(5_750, 180)];
    const selected = selectTemporalFrames(frames, 6_000, 4, new Uint8Array(16).fill(180));

    expect(selected.map((item) => item.capturedAtMs)).toEqual([1_000]);
  });

  it('omits redundant history for a static screen', () => {
    const frames = [frame(1_000, 80), frame(4_000, 80), frame(5_250, 80), frame(5_750, 80)];
    const selected = selectTemporalFrames(frames, 6_000, 4, new Uint8Array(16).fill(80));

    expect(selected).toEqual([]);
  });

  it('scores visual changes and formats chronological labels', () => {
    expect(scoreFrameDifference(new Uint8Array([0, 0]), new Uint8Array([255, 255]))).toBe(1);
    expect(formatRelativeTimestamp(1_000, 6_000)).toBe('T-5.00s');
  });
});
