import { describe, expect, it } from 'vitest';
import { createComposerState, toggleComposerControl } from './composer';

describe('composer contracts', () => {
  it('enables submit only for non-empty drafts', () => {
    expect(createComposerState('').submitEnabled).toBe(false);
    expect(createComposerState('hello').submitEnabled).toBe(true);
  });

  it('toggles controls immutably', () => {
    const state = createComposerState('go', ['model']);
    expect(toggleComposerControl(state, 'model').controls).toEqual([]);
    expect(toggleComposerControl(state, 'voice').controls).toEqual(['model', 'voice']);
  });
});
