import { describe, expect, it } from 'vitest';
import { DEFAULT_OUTPUT_SETTINGS } from './outputDefaults';

describe('DEFAULT_OUTPUT_SETTINGS', () => {
  it('keeps stable numeric defaults', () => {
    expect(DEFAULT_OUTPUT_SETTINGS).toEqual({
      stability: 0.75,
      similarity: 0.8,
      style: 0.35,
      speed: 1,
      pitch: 0,
      volume: 0,
    });
  });
});
