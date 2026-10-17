import { describe, expect, it } from 'vitest';
import {
  canInvokeCapability,
  createCapabilityEvents,
  executeCapabilityInvocation,
  getEnabledCapabilities,
} from './capabilities';

const definition = {
  id: 'search',
  name: 'Search',
  description: 'Find things',
  scope: 'workspace' as const,
  enabled: true,
};

const invocation = {
  id: 'invocation-1',
  capabilityId: 'search',
  scope: 'workspace' as const,
  workspaceId: 'workspace-1',
  sessionId: 'session-1',
  arguments: { query: 'omnix' },
  approvalDecision: 'approved' as const,
  requestedAt: '2026-01-01T00:00:00.000Z',
};

describe('capability contracts', () => {
  it('filters enabled records', () => {
    const disabled = { ...definition, id: 'disabled', enabled: false };
    expect(getEnabledCapabilities([definition, disabled])).toEqual([definition]);
  });

  it('requires an enabled matching approved capability before execution', () => {
    expect(canInvokeCapability(definition, invocation)).toBe(true);
    expect(canInvokeCapability(definition, { ...invocation, approvalDecision: 'pending' })).toBe(false);
  });

  it('executes approved invocations and emits tool events', async () => {
    const record = await executeCapabilityInvocation(definition, invocation, {
      capabilityId: 'search',
      run: async (request) => ({
        invocationId: request.id,
        capabilityId: request.capabilityId,
        status: 'completed',
        result: { answer: 'found' },
        completedAt: '2026-01-01T00:00:01.000Z',
      }),
    });

    expect(record.status).toBe('completed');
    expect(createCapabilityEvents(record).map((event) => event.type)).toEqual(['tool_call', 'tool_result']);
  });

  it('denies invocations that have not been approved', async () => {
    const record = await executeCapabilityInvocation(definition, { ...invocation, approvalDecision: 'pending' }, {
      capabilityId: 'search',
      run: async () => {
        throw new Error('should not run');
      },
    });

    expect(record.status).toBe('denied');
    expect(record.result?.status).toBe('denied');
    expect(createCapabilityEvents(record)[1]).toMatchObject({
      type: 'tool_result',
      payload: { status: 'denied' },
    });
  });
});
