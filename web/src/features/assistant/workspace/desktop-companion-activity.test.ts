import { describe, expect, it } from 'vitest';

import { classifyDesktopActivity, DesktopBehaviorTracker } from './desktop-companion-activity';

const WIDTH = 48;
const HEIGHT = 27;

function filled(value: number): Uint8Array {
  return new Uint8Array(WIDTH * HEIGHT).fill(value);
}

function shifted(source: Uint8Array, dx: number, dy: number): Uint8Array {
  const target = new Uint8Array(source.length);
  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) {
      const sx = x - dx;
      const sy = y - dy;
      if (sx >= 0 && sx < WIDTH && sy >= 0 && sy < HEIGHT) target[y * WIDTH + x] = source[sy * WIDTH + sx] ?? 0;
    }
  }
  return target;
}

describe('desktop companion activity classifier', () => {
  it('classifies unchanged samples as static', () => {
    const frame = filled(90);
    expect(classifyDesktopActivity(frame, frame, 1000)).toMatchObject({ activity: 'static', hypothesis: 'none' });
  });

  it('detects translation-like changes as likely scrolling', () => {
    const previous = filled(0);
    for (let y = 5; y < 22; y += 1) {
      for (let x = 4; x < 44; x += 1) previous[y * WIDTH + x] = (x * 7 + y * 11) % 255;
    }
    const current = shifted(previous, 0, 3);
    const result = classifyDesktopActivity(previous, current, 2000);
    expect(result.activity).toBe('translation_like');
    expect(result.hypothesis).toBe('likely_scroll');
    expect(result.verticalShift).not.toBe(0);
  });

  it('keeps small focused changes as a low-confidence typing hypothesis', () => {
    const previous = filled(80);
    const current = previous.slice();
    for (let y = 18; y < 24; y += 1) {
      for (let x = 14; x < 34; x += 1) current[y * WIDTH + x] = 190;
    }
    const result = classifyDesktopActivity(previous, current, 3000);
    expect(['localized_change', 'micro_change']).toContain(result.activity);
    expect(result.confidence).toBeLessThanOrEqual(0.72);
  });
});

describe('desktop companion behavior tracker', () => {
  it('recognizes rapid browsing and later settling from bounded history', () => {
    const tracker = new DesktopBehaviorTracker();
    for (let index = 0; index < 4; index += 1) {
      tracker.record({
        activity: 'localized_change',
        hypothesis: index % 2 ? 'likely_navigation' : 'likely_scroll',
        confidence: 0.7,
        changedRatio: 0.3,
        meanDifference: 0.2,
        horizontalShift: 0,
        verticalShift: 0,
        focus: 0.5,
        capturedAtMs: index * 500,
      });
    }
    expect(tracker.snapshot(2000).rapidBrowsing).toBe(true);

    let settled = tracker.snapshot(2000);
    for (let index = 0; index < 4; index += 1) {
      settled = tracker.record({
        activity: 'static', hypothesis: 'none', confidence: 0.95, changedRatio: 0, meanDifference: 0,
        horizontalShift: 0, verticalShift: 0, focus: 0, capturedAtMs: 3000 + index * 500,
      });
    }
    expect(settled.currentPattern).toBe('settled');
    expect(settled.transition).toBe('settled_down');
  });
});
