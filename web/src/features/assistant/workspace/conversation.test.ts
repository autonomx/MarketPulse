import { describe, expect, it } from 'vitest';
import type { WorkspaceChatSession } from './domain';
import {
  appendConversationTurn,
  createConversationTurn,
  getLatestConversationTurn,
  isConversationTurnRole,
  type ConversationState,
} from './conversation';

const session: WorkspaceChatSession = {
  id: 'session-1',
  workspaceId: 'workspace-1',
  title: 'Conversation',
  mode: 'text',
  createdAt: '2026-06-22T00:00:00.000Z',
  updatedAt: '2026-06-22T00:00:00.000Z',
};

describe('assistant workspace conversation engine', () => {
  it('recognizes supported turn roles', () => {
    expect(isConversationTurnRole('user')).toBe(true);
    expect(isConversationTurnRole('assistant')).toBe(true);
    expect(isConversationTurnRole('tool')).toBe(true);
    expect(isConversationTurnRole('system')).toBe(true);
  });

  it('appends turns to the matching session', () => {
    const state: ConversationState = { session, turns: [] };
    const turn = createConversationTurn({
      id: 'turn-1',
      sessionId: session.id,
      role: 'user',
      content: [{ kind: 'text', text: 'Hello' }],
      metadata: {},
      createdAt: session.createdAt,
    });

    const next = appendConversationTurn(state, turn);
    expect(next.turns).toHaveLength(1);
    expect(getLatestConversationTurn(next)).toEqual(turn);
  });
});
