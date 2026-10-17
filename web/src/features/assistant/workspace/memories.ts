export type MemoryScope = 'global' | 'workspace' | 'project' | 'session';
export type MemorySource = 'user_saved' | 'assistant_suggested' | 'imported';

export type WorkspaceMemory = {
  id: string;
  scope: MemoryScope;
  source: MemorySource;
  category: 'preference' | 'fact' | 'project' | 'relationship' | 'instruction';
  content: string;
  confidence: number;
  pinned?: boolean;
  createdAt: string;
  updatedAt: string;
};

export function createMemoryRecord(memory: WorkspaceMemory): WorkspaceMemory {
  return { ...memory };
}

export function filterMemoriesByScope(memories: WorkspaceMemory[], scope: MemoryScope): WorkspaceMemory[] {
  return memories.filter((memory) => memory.scope === scope);
}

export function pinMemory(memory: WorkspaceMemory, updatedAt: string): WorkspaceMemory {
  return { ...memory, pinned: true, updatedAt };
}

export function requiresMemoryConfirmation(memory: WorkspaceMemory): boolean {
  return memory.source === 'assistant_suggested';
}
