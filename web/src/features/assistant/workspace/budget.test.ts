import { describe, expect, it } from 'vitest';
import { allocateContextBudget, createDefaultContextBudget } from './budget';
import { assembleContext } from './context';
import { createConversationProjection } from './projections';
import type { WorkspaceChatSession } from './domain';

const session: WorkspaceChatSession = {
  id: 'session-1',
  workspaceId: 'workspace-1',
  title: 'Budgeting',
  mode: 'text',
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
};

describe('context budgeting', () => {
  it('splits included and omitted sources by token budget', () => {
    const context = assembleContext({
      conversation: createConversationProjection(session),
      workspaceInstructions: [],
      projectInstructions: [],
      sessionInstructions: [],
      memories: [],
      retrievedKnowledge: [
        { id: 'chunk-1', sourceId: 'source-1', content: 'small', tokenEstimate: 5 },
        { id: 'chunk-2', sourceId: 'source-1', content: 'large', tokenEstimate: 100 },
      ],
      activeTools: [],
    });

    const budgeted = allocateContextBudget(context, createDefaultContextBudget(50));

    expect(budgeted.includedSources.some((source) => source.sourceId === 'chunk-1')).toBe(true);
    expect(budgeted.omittedSources.some((source) => source.sourceId === 'chunk-2')).toBe(true);
  });
});
