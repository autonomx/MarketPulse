import { describe, expect, it } from 'vitest';
import { getEnabledInstructionRecords, getScopedInstructionRecords, sortInstructionRecords } from './instructions';

const low = { id: 'b', scope: 'workspace' as const, content: 'Low', priority: 1, enabled: true };
const high = { id: 'a', scope: 'project' as const, content: 'High', priority: 2, enabled: true };
const disabled = { id: 'c', scope: 'session' as const, content: 'Off', priority: 3, enabled: false };

describe('instruction contracts', () => {
  it('filters enabled records', () => {
    expect(getEnabledInstructionRecords([low, disabled])).toEqual([low]);
  });

  it('sorts by priority and id', () => {
    expect(sortInstructionRecords([low, high]).map((record) => record.id)).toEqual(['a', 'b']);
  });

  it('filters by scope', () => {
    expect(getScopedInstructionRecords([low, high], ['project'])).toEqual([high]);
  });
});
