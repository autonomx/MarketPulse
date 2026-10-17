import type { AssistantWorkspaceEvent } from './events';

export type CapabilityScope = 'global' | 'workspace' | 'project' | 'session';
export type CapabilityEvent = 'registered' | 'enabled' | 'requested' | 'approved' | 'denied' | 'running' | 'completed' | 'failed';
export type CapabilityRunStatus = 'approved' | 'denied' | 'running' | 'completed' | 'failed';

export type CapabilityDefinition = {
  id: string;
  name: string;
  description: string;
  scope: CapabilityScope;
  enabled: boolean;
};

export type CapabilityInvocation = {
  id: string;
  capabilityId: string;
  scope: CapabilityScope;
  workspaceId: string;
  projectId?: string;
  sessionId?: string;
  arguments: Record<string, unknown>;
  approvalDecision: 'pending' | 'approved' | 'denied';
  requestedAt: string;
};

export type CapabilityInvocationResult = {
  invocationId: string;
  capabilityId: string;
  status: 'completed' | 'failed' | 'denied';
  result?: unknown;
  error?: string;
  completedAt: string;
};

export type CapabilityExecutor = {
  capabilityId: string;
  run(invocation: CapabilityInvocation): Promise<CapabilityInvocationResult>;
};

export type CapabilityExecutionRecord = {
  invocation: CapabilityInvocation;
  result?: CapabilityInvocationResult;
  status: CapabilityRunStatus;
};

export function createCapabilityDefinition(definition: CapabilityDefinition): CapabilityDefinition {
  return { ...definition };
}

export function getEnabledCapabilities(definitions: CapabilityDefinition[]): CapabilityDefinition[] {
  return definitions.filter((definition) => definition.enabled);
}

export function canUseCapability(definition: CapabilityDefinition, scope: CapabilityScope): boolean {
  return definition.enabled && (definition.scope === 'global' || definition.scope === scope);
}

export function createCapabilityInvocation(invocation: CapabilityInvocation): CapabilityInvocation {
  return {
    ...invocation,
    arguments: { ...invocation.arguments },
  };
}

export function canInvokeCapability(
  definition: CapabilityDefinition,
  invocation: Pick<CapabilityInvocation, 'capabilityId' | 'scope' | 'approvalDecision'>,
): boolean {
  return definition.id === invocation.capabilityId && invocation.approvalDecision === 'approved' && canUseCapability(definition, invocation.scope);
}

export async function executeCapabilityInvocation(
  definition: CapabilityDefinition,
  invocation: CapabilityInvocation,
  executor: CapabilityExecutor,
): Promise<CapabilityExecutionRecord> {
  const safeInvocation = createCapabilityInvocation(invocation);

  if (safeInvocation.approvalDecision !== 'approved') {
    return {
      invocation: safeInvocation,
      status: 'denied',
      result: {
        invocationId: safeInvocation.id,
        capabilityId: safeInvocation.capabilityId,
        status: 'denied',
        error: 'Capability invocation requires approval before execution.',
        completedAt: safeInvocation.requestedAt,
      },
    };
  }

  if (!canUseCapability(definition, safeInvocation.scope) || executor.capabilityId !== safeInvocation.capabilityId) {
    return {
      invocation: safeInvocation,
      status: 'failed',
      result: {
        invocationId: safeInvocation.id,
        capabilityId: safeInvocation.capabilityId,
        status: 'failed',
        error: 'Capability executor does not match an enabled capability for this scope.',
        completedAt: safeInvocation.requestedAt,
      },
    };
  }

  const result = await executor.run(safeInvocation);
  return {
    invocation: safeInvocation,
    result,
    status: result.status === 'completed' ? 'completed' : result.status === 'denied' ? 'denied' : 'failed',
  };
}

export function createCapabilityEvents(record: CapabilityExecutionRecord): AssistantWorkspaceEvent[] {
  const { invocation, result } = record;
  const createdAt = invocation.requestedAt;
  const callEvent: AssistantWorkspaceEvent = {
    id: `${invocation.id}:call`,
    type: 'tool_call',
    workspaceId: invocation.workspaceId,
    projectId: invocation.projectId,
    sessionId: invocation.sessionId,
    payload: {
      toolCallId: invocation.id,
      toolName: invocation.capabilityId,
      arguments: { ...invocation.arguments },
      approved: invocation.approvalDecision === 'approved',
    },
    createdAt,
  };

  if (!result) return [callEvent];

  const resultEvent: AssistantWorkspaceEvent = {
    id: `${invocation.id}:result`,
    type: 'tool_result',
    workspaceId: invocation.workspaceId,
    projectId: invocation.projectId,
    sessionId: invocation.sessionId,
    payload: {
      toolCallId: invocation.id,
      status: result.status,
      result: result.result,
      error: result.error,
    },
    createdAt: result.completedAt,
  };

  return [callEvent, resultEvent];
}
