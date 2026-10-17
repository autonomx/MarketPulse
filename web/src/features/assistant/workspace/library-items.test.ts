import { describe, expect, it } from 'vitest';
import { getReadyLibraryItems, getScopedLibraryItems } from './library-items';

const base = { id: 'item-1', workspaceId: 'w1', title: 'Guide', status: 'ready' as const };
const project = { id: 'item-2', workspaceId: 'w1', projectId: 'p1', title: 'Plan', status: 'ready' as const };
const other = { id: 'item-3', workspaceId: 'w2', title: 'Other', status: 'pending' as const };

describe('library item contracts', () => {
  it('filters ready items', () => {
    expect(getReadyLibraryItems([base, other])).toEqual([base]);
  });

  it('filters workspace and project items', () => {
    expect(getScopedLibraryItems([base, project, other], 'w1', 'p1')).toEqual([base, project]);
  });
});
