import { describe, expect, it } from 'vitest';
import type { AssistantWorkspaceEvent } from './events';
import { isAssistantWorkspaceEventType } from './events';

describe('assistant workspace event contracts', () => {
  it('narrows typed message payloads by event type', () => {
    const event: AssistantWorkspaceEvent = {
      id: 'event-1',
      type: 'user_message',
      workspaceId: 'workspace-1',
      sessionId: 'session-1',
      payload: {
        turn: {
          id: 'turn-1',
          sessionId: 'session-1',
          role: 'user',
          content: [{ kind: 'text', text: 'Hello' }],
          metadata: {},
          createdAt: '2026-01-01T00:00:00.000Z',
        },
      },
      createdAt: '2026-01-01T00:00:00.000Z',
    };

    if (event.type !== 'user_message') {
      throw new Error('expected a user message event');
    }

    expect(event.payload.turn.role).toBe('user');
  });

  it('recognizes supported event types', () => {
    expect(isAssistantWorkspaceEventType('tool_result')).toBe(true);
    expect(isAssistantWorkspaceEventType('unknown')).toBe(false);
  });
});
