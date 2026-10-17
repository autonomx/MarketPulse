import { describe, expect, it } from 'vitest';
import { isChatSessionMode } from './domain';

describe('assistant workspace domain contracts', () => {
  it('recognizes supported session modes', () => {
    expect(isChatSessionMode('text')).toBe(true);
    expect(isChatSessionMode('voice')).toBe(true);
    expect(isChatSessionMode('mixed')).toBe(true);
  });
});
