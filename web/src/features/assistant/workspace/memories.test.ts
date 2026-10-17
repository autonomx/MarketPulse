import { describe, expect, it } from 'vitest';
import { createMemoryRecord, filterMemoriesByScope, pinMemory, requiresMemoryConfirmation } from './memories';

const baseMemory = {
  id: 'm1',
  scope: 'workspace' as const,
  source: 'user_saved' as const,
  category: 'preference' as const,
  content: 'Prefers direct implementation plans.',
  confidence: 1,
  createdAt: 't1',
  updatedAt: 't1',
};

describe('scoped memory contracts', () => {
  it('filters records by scope', () => {
    const sessionMemory = { ...baseMemory, id: 'm2', scope: 'session' as const };
    expect(filterMemoriesByScope([baseMemory, sessionMemory], 'workspace')).toEqual([baseMemory]);
  });

  it('pins records immutably', () => {
    const created = createMemoryRecord(baseMemory);
    const pinned = pinMemory(created, 't2');

    expect(pinned.pinned).toBe(true);
    expect(pinned.updatedAt).toBe('t2');
    expect(created.pinned).toBeUndefined();
  });

  it('requires confirmation for assistant suggestions', () => {
    expect(requiresMemoryConfirmation({ ...baseMemory, source: 'assistant_suggested' })).toBe(true);
    expect(requiresMemoryConfirmation(baseMemory)).toBe(false);
  });
});
