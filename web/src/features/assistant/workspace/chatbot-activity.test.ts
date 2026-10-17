import { describe, expect, it } from 'vitest';
import { createChatbotActivityEvents } from './chatbot-activity';

const options = {
  workspaceId: 'workspace:default',
  projectId: 'project:chatbot',
};

describe('createChatbotActivityEvents', () => {
  it('projects chat messages into replayable workspace events', () => {
    const events = createChatbotActivityEvents(
      {
        id: 'chat:1',
        messages: [
          {
            id: 'msg:1',
            role: 'user',
            content: 'Hello Omnix',
            created_at: '2026-06-14T00:00:01Z',
            metadata: { provider: 'openai' },
          },
          {
            id: 'msg:2',
            role: 'assistant',
            content: 'Hello from the model.',
            created_at: '2026-06-14T00:00:02Z',
            metadata: { model: 'gpt-mini' },
          },
        ],
      },
      options,
    );

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      id: 'chatbot:chat:1:msg:1:user',
      type: 'user_message',
      workspaceId: 'workspace:default',
      projectId: 'project:chatbot',
      sessionId: 'chat:1',
      payload: {
        turn: {
          id: 'msg:1',
          role: 'user',
          content: [{ kind: 'text', text: 'Hello Omnix' }],
        },
      },
    });
    expect(events[1]?.type).toBe('assistant_message');
  });

  it('ignores system messages because they are not visible activity turns', () => {
    const events = createChatbotActivityEvents(
      {
        id: 'chat:1',
        messages: [
          {
            id: 'msg:system',
            role: 'system',
            content: 'System instruction',
            created_at: '2026-06-14T00:00:00Z',
          },
        ],
      },
      options,
    );

    expect(events).toEqual([]);
  });
});
