import { describe, expect, it } from 'vitest';
import type { AssistantWorkspaceEvent } from './events';
import { createToolExecutionRows, getPendingToolExecutionRows, summarizeToolValue } from './tool-execution-view';

const requestedCall: AssistantWorkspaceEvent = {
  id: 'call:1',
  type: 'tool_call',
  workspaceId: 'workspace:main',
  sessionId: 'session:voice',
  payload: {
    toolCallId: 'tool-call:1',
    toolName: 'search',
    arguments: { z: 2, a: 'omnix' },
    approved: false,
  },
  createdAt: '2026-06-23T09:00:00.000Z',
};

const completedResult: AssistantWorkspaceEvent = {
  id: 'result:1',
  type: 'tool_result',
  workspaceId: 'workspace:main',
  sessionId: 'session:voice',
  payload: {
    toolCallId: 'tool-call:1',
    status: 'completed',
    result: { answer: 'found' },
  },
  createdAt: '2026-06-23T09:00:01.000Z',
};

describe('tool execution view', () => {
  it('summarizes object values with stable key ordering', () => {
    expect(summarizeToolValue({ z: 2, a: { y: true, b: false } })).toBe('{"a":{"b":false,"y":true},"z":2}');
  });

  it('creates pending approval rows from unapproved tool calls', () => {
    const rows = createToolExecutionRows([requestedCall], [
      {
        id: 'search',
        name: 'Search',
        description: 'Search workspace knowledge',
        scope: 'workspace',
        enabled: true,
      },
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      toolCallId: 'tool-call:1',
      label: 'Search',
      status: 'requested',
      statusLabel: 'Approval requested',
      actions: ['approve', 'deny'],
      argumentsSummary: '{"a":"omnix","z":2}',
    });
    expect(getPendingToolExecutionRows(rows)).toHaveLength(1);
  });

  it('joins tool results and removes approval actions after completion', () => {
    const rows = createToolExecutionRows([requestedCall, completedResult]);

    expect(rows[0]).toMatchObject({
      status: 'completed',
      statusLabel: 'Completed',
      completedAt: '2026-06-23T09:00:01.000Z',
      resultSummary: '{"answer":"found"}',
      actions: [],
    });
  });

  it('surfaces failed tool results as retryable rows', () => {
    const failedResult: AssistantWorkspaceEvent = {
      ...completedResult,
      id: 'result:failed',
      payload: {
        toolCallId: 'tool-call:1',
        status: 'failed',
        error: 'network down',
      },
    };

    expect(createToolExecutionRows([requestedCall, failedResult])[0]).toMatchObject({
      status: 'failed',
      error: 'network down',
      actions: ['retry'],
    });
  });
});
