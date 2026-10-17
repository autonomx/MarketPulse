import type { ConversationProjection } from './projections';

export type ContextSourceType =
  | 'conversation'
  | 'instruction'
  | 'memory'
  | 'knowledge'
  | 'assistant_identity'
  | 'tool';

export type ContextSource = {
  type: ContextSourceType;
  sourceId: string;
  title?: string;
  reasonIncluded: string;
  tokenEstimate?: number;
};

export type WorkspaceInstructionContext = {
  id: string;
  scope: 'global' | 'workspace' | 'project' | 'session';
  content: string;
  priority: number;
  enabled: boolean;
};

export type MemoryContext = {
  id: string;
  scope: 'global' | 'workspace' | 'project' | 'session';
  content: string;
  pinned?: boolean;
  confidence?: number;
};

export type KnowledgeChunkContext = {
  id: string;
  sourceId: string;
  content: string;
  title?: string;
  tokenEstimate?: number;
};

export type AssistantIdentityContext = {
  id: string;
  name: string;
  systemPrompt: string;
};

export type ToolContext = {
  id: string;
  name: string;
  enabled: boolean;
};

export type ContextAssembly = {
  conversation: ConversationProjection;
  workspaceInstructions: WorkspaceInstructionContext[];
  projectInstructions: WorkspaceInstructionContext[];
  sessionInstructions: WorkspaceInstructionContext[];
  memories: MemoryContext[];
  retrievedKnowledge: KnowledgeChunkContext[];
  assistantIdentity?: AssistantIdentityContext;
  activeTools: ToolContext[];
  provider?: string;
  model?: string;
  sources: ContextSource[];
};

export function createContextSource(source: ContextSource): ContextSource {
  return { ...source };
}

export function assembleContext(input: Omit<ContextAssembly, 'sources'>): ContextAssembly {
  const sources: ContextSource[] = [
    createContextSource({
      type: 'conversation',
      sourceId: input.conversation.session.id,
      title: input.conversation.session.title,
      reasonIncluded: 'Current conversation projection is the primary interaction context.',
    }),
    ...input.workspaceInstructions
      .filter((instruction) => instruction.enabled)
      .map((instruction) =>
        createContextSource({
          type: 'instruction',
          sourceId: instruction.id,
          reasonIncluded: `Enabled ${instruction.scope} instruction.`,
        }),
      ),
    ...input.projectInstructions
      .filter((instruction) => instruction.enabled)
      .map((instruction) =>
        createContextSource({
          type: 'instruction',
          sourceId: instruction.id,
          reasonIncluded: `Enabled ${instruction.scope} instruction.`,
        }),
      ),
    ...input.sessionInstructions
      .filter((instruction) => instruction.enabled)
      .map((instruction) =>
        createContextSource({
          type: 'instruction',
          sourceId: instruction.id,
          reasonIncluded: `Enabled ${instruction.scope} instruction.`,
        }),
      ),
    ...input.memories.map((memory) =>
      createContextSource({
        type: 'memory',
        sourceId: memory.id,
        reasonIncluded: memory.pinned ? 'Pinned memory in active scope.' : 'Relevant memory in active scope.',
      }),
    ),
    ...input.retrievedKnowledge.map((chunk) =>
      createContextSource({
        type: 'knowledge',
        sourceId: chunk.id,
        title: chunk.title,
        reasonIncluded: 'Retrieved knowledge chunk for active workspace or project.',
        tokenEstimate: chunk.tokenEstimate,
      }),
    ),
    ...input.activeTools
      .filter((tool) => tool.enabled)
      .map((tool) =>
        createContextSource({
          type: 'tool',
          sourceId: tool.id,
          title: tool.name,
          reasonIncluded: 'Enabled tool is available for this context.',
        }),
      ),
  ];

  if (input.assistantIdentity) {
    sources.push(
      createContextSource({
        type: 'assistant_identity',
        sourceId: input.assistantIdentity.id,
        title: input.assistantIdentity.name,
        reasonIncluded: 'Selected assistant identity shapes prompt assembly.',
      }),
    );
  }

  return {
    ...input,
    sources,
  };
}

export function getEnabledInstructions(context: ContextAssembly): WorkspaceInstructionContext[] {
  return [
    ...context.workspaceInstructions,
    ...context.projectInstructions,
    ...context.sessionInstructions,
  ].filter((instruction) => instruction.enabled);
}
