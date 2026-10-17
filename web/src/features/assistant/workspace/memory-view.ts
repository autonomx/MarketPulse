import type { WorkspaceMemory, MemoryScope } from './memories';

export type MemoryViewFilter = {
  scope?: MemoryScope;
  pinnedOnly?: boolean;
  suggestedOnly?: boolean;
};

export type MemoryViewAction = 'approve' | 'reject' | 'edit' | 'forget' | 'pin' | 'move_scope';

export type MemoryViewRow = {
  id: string;
  scope: MemoryScope;
  content: string;
  pinned: boolean;
  requiresConfirmation: boolean;
  actions: MemoryViewAction[];
};

export function createMemoryViewRows(memories: WorkspaceMemory[], filter: MemoryViewFilter = {}): MemoryViewRow[] {
  return memories
    .filter((memory) => (filter.scope ? memory.scope === filter.scope : true))
    .filter((memory) => (filter.pinnedOnly ? Boolean(memory.pinned) : true))
    .filter((memory) => (filter.suggestedOnly ? memory.source === 'assistant_suggested' : true))
    .map((memory) => ({
      id: memory.id,
      scope: memory.scope,
      content: memory.content,
      pinned: Boolean(memory.pinned),
      requiresConfirmation: memory.source === 'assistant_suggested',
      actions: memory.source === 'assistant_suggested'
        ? ['approve', 'reject', 'edit', 'forget']
        : ['edit', 'forget', 'pin', 'move_scope'],
    }));
}
