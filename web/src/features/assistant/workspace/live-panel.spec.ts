import { describe, expect, it } from 'vitest';
import { createLivePanelState, setLivePanelMode } from './live-panel';

describe('live panel contracts', () => {
  it('creates and updates panel state', () => {
    const initial = createLivePanelState();
    const active = setLivePanelMode(initial, 'thinking');
    expect(initial.mode).toBe('idle');
    expect(active.mode).toBe('thinking');
  });
});
