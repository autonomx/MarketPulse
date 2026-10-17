import { describe, expect, it } from 'vitest';
import { canStartInput, canStartOutput, isLiveSessionMode } from './session-mode';

describe('live session mode contracts', () => {
  it('recognizes known modes', () => {
    expect(isLiveSessionMode('ready')).toBe(true);
    expect(isLiveSessionMode('missing')).toBe(false);
  });

  it('derives allowed transitions', () => {
    expect(canStartInput('ready')).toBe(true);
    expect(canStartOutput('working')).toBe(true);
    expect(canStartOutput('ready')).toBe(false);
  });
});
