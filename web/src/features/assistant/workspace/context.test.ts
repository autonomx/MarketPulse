import { describe, expect, it } from 'vitest';
import { assembleContext, getEnabledInstructions } from './context';
import { createConversationProjection } from './projections';
import type { WorkspaceChatSession } from './domain';

const session: WorkspaceChatSession = {
  id: 'session-1',
  workspaceId: 'workspace-1',
  projectId: 'project-1',
  title: 'Architecture planning',
  mode: 'text',
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
};

describe('context assembly', () => {
  it('records provenance for conversation, instructions, memory, knowledge, identity, and tools', () => {
    const context = assembleContext({
      conversation: createConversationProjection(session),
      workspaceInstructions: [
        {
          id: 'instruction-1',
          scope: 'workspace',
          content: 'Prefer local models.',
          priority: 10,
          enabled: true,
        },
      ],
      projectInstructions: [],
      sessionInstructions: [],
      memories: [
        {
          id: 'memory-1',
          scope: 'workspace',
          content: 'User works on Omnix.',
          pinned: true,
        },
      ],
      retrievedKnowledge: [
        {
          id: 'chunk-1',
          sourceId: 'source-1',
          title: 'Architecture.md',
          content: 'Omnix is event-driven.',
        },
      ],
      assistantIdentity: {
        id: 'identity-1',
        name: 'Architect',
        systemPrompt: 'Challenge assumptions.',
      },
      activeTools: [{ id: 'tool-1', name: 'Search workspace', enabled: true }],
      provider: 'lmstudio',
      model: 'qwen',
    });

    expect(context.sources.map((source) => source.type)).toEqual([
      'conversation',
      'instruction',
      'memory',
      'knowledge',
      'tool',
      'assistant_identity',
    ]);
    expect(getEnabledInstructions(context)).toHaveLength(1);
  });
});
