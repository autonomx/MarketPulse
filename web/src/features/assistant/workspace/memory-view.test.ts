import { describe, expect, it } from 'vitest';
import { createMemoryViewRows } from './memory-view';

const saved = {
  id: 'm1',
  scope: 'workspace' as const,
  source: 'user_saved' as const,
  category: 'preference' as const,
  content: 'Prefers concise diffs.',
  confidence: 1,
  pinned: true,
  createdAt: 't1',
  updatedAt: 't1',
};
const suggested = { ...saved, id: 'm2', source: 'assistant_suggested' as const, pinned: false };

describe('memory management view contracts', () => {
  it('creates rows with suggested-memory actions', () => {
    const rows = createMemoryViewRows([saved, suggested], { suggestedOnly: true });
    expect(rows).toHaveLength(1);
    expect(rows[0]?.requiresConfirmation).toBe(true);
    expect(rows[0]?.actions).toEqual(['approve', 'reject', 'edit', 'forget']);
  });

  it('filters pinned rows', () => {
    expect(createMemoryViewRows([saved, suggested], { pinnedOnly: true }).map((row) => row.id)).toEqual(['m1']);
  });
});
