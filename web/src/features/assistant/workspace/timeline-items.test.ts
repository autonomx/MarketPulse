import { describe, expect, it } from 'vitest';
import type { AssistantWorkspaceEvent } from './events';
import {
  createTimelineItemsFromEvents,
  createTimelineNote,
  filterTimelineItemsByKind,
  sortTimelineItems,
} from './timeline-items';

const userMessage: AssistantWorkspaceEvent = {
  id: 'event:user',
  type: 'user_message',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    turn: {
      id: 'turn:user',
      sessionId: 'session:main',
      role: 'user',
      content: [{ kind: 'text', text: 'Look up Bran records' }],
      metadata: {},
      createdAt: '2026-06-23T10:00:00.000Z',
    },
  },
  createdAt: '2026-06-23T10:00:00.000Z',
};

const toolCall: AssistantWorkspaceEvent = {
  id: 'event:tool-call',
  type: 'tool_call',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    toolCallId: 'tool-call:1',
    toolName: 'search',
    approved: false,
  },
  createdAt: '2026-06-23T10:00:01.000Z',
};

const toolResult: AssistantWorkspaceEvent = {
  id: 'event:tool-result',
  type: 'tool_result',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    toolCallId: 'tool-call:1',
    status: 'denied',
    error: 'not approved',
  },
  createdAt: '2026-06-23T10:00:02.000Z',
};

const failureEvent: AssistantWorkspaceEvent = {
  id: 'event:failure',
  type: 'operation_failed',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    operation: 'chat_request',
    message: 'Chat request failed with status 503',
    statusCode: 503,
    providerId: 'openai',
    modelId: 'gpt-mini',
    recoverable: true,
  },
  createdAt: '2026-06-23T10:00:03.000Z',
};

describe('timeline item contracts', () => {
  it('sorts items by time and id', () => {
    const late = { id: 'b', kind: 'turn' as const, label: 'Late', createdAt: '2026-01-02' };
    const early = { id: 'a', kind: 'event' as const, label: 'Early', createdAt: '2026-01-01' };
    expect(sortTimelineItems([late, early])).toEqual([early, late]);
  });

  it('filters and creates note items', () => {
    const note = createTimelineNote('n1', 'Ready', '2026-01-01');
    expect(filterTimelineItemsByKind([note], 'note')).toEqual([note]);
  });

  it('projects workspace events into timeline items', () => {
    const items = createTimelineItemsFromEvents([toolResult, userMessage, toolCall, failureEvent]);

    expect(items.map((item) => item.label)).toEqual([
      'User: Look up Bran records',
      'Tool approval requested: search',
      'Tool denied: tool-call:1',
      'chat request failed: Chat request failed with status 503',
    ]);
    expect(items.map((item) => item.sourceEventType)).toEqual(['user_message', 'tool_call', 'tool_result', 'operation_failed']);
    expect(items.map((item) => item.status)).toEqual(['user', 'requested', 'denied', 'failed']);
  });
});
