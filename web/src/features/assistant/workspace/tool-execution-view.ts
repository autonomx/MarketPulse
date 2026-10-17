import type { AssistantWorkspaceEvent } from './events';
import type { CapabilityDefinition } from './capabilities';

export type ToolExecutionStatus = 'requested' | 'running' | 'completed' | 'failed' | 'denied';
export type ToolExecutionAction = 'approve' | 'deny' | 'retry';

export type ToolExecutionRow = {
  id: string;
  toolCallId: string;
  toolName: string;
  label: string;
  description?: string;
  status: ToolExecutionStatus;
  statusLabel: string;
  createdAt: string;
  completedAt?: string;
  argumentsSummary?: string;
  resultSummary?: string;
  error?: string;
  actions: ToolExecutionAction[];
};

type ToolCallEvent = Extract<AssistantWorkspaceEvent, { type: 'tool_call' }>;
type ToolResultEvent = Extract<AssistantWorkspaceEvent, { type: 'tool_result' }>;

function isToolCallEvent(event: AssistantWorkspaceEvent): event is ToolCallEvent {
  return event.type === 'tool_call';
}

function isToolResultEvent(event: AssistantWorkspaceEvent): event is ToolResultEvent {
  return event.type === 'tool_result';
}

function sortStableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortStableValue);
  if (!value || typeof value !== 'object') return value;

  return Object.keys(value as Record<string, unknown>)
    .sort()
    .reduce<Record<string, unknown>>((result, key) => {
      result[key] = sortStableValue((value as Record<string, unknown>)[key]);
      return result;
    }, {});
}

export function summarizeToolValue(value: unknown): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || value === null) return String(value);

  return JSON.stringify(sortStableValue(value));
}

function getStatusLabel(status: ToolExecutionStatus): string {
  switch (status) {
    case 'requested':
      return 'Approval requested';
    case 'running':
      return 'Running';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    case 'denied':
      return 'Denied';
  }
}

function getActions(status: ToolExecutionStatus): ToolExecutionAction[] {
  if (status === 'requested') return ['approve', 'deny'];
  if (status === 'failed') return ['retry'];
  return [];
}

function createDefinitionLookup(definitions: CapabilityDefinition[]): Map<string, CapabilityDefinition> {
  return new Map(definitions.map((definition) => [definition.id, definition]));
}

function createResultLookup(events: AssistantWorkspaceEvent[]): Map<string, ToolResultEvent> {
  return events
    .filter(isToolResultEvent)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt) || left.id.localeCompare(right.id))
    .reduce<Map<string, ToolResultEvent>>((result, event) => {
      result.set(event.payload.toolCallId, event);
      return result;
    }, new Map());
}

function getExecutionStatus(call: ToolCallEvent, result?: ToolResultEvent): ToolExecutionStatus {
  if (result) return result.payload.status;
  if (call.payload.approved === false) return 'requested';
  return 'running';
}

export function createToolExecutionRows(
  events: AssistantWorkspaceEvent[],
  definitions: CapabilityDefinition[] = [],
): ToolExecutionRow[] {
  const definitionsById = createDefinitionLookup(definitions);
  const resultsByCallId = createResultLookup(events);

  return events
    .filter(isToolCallEvent)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt) || left.id.localeCompare(right.id))
    .map((call): ToolExecutionRow => {
      const definition = definitionsById.get(call.payload.toolName);
      const result = resultsByCallId.get(call.payload.toolCallId);
      const status = getExecutionStatus(call, result);

      return {
        id: call.id,
        toolCallId: call.payload.toolCallId,
        toolName: call.payload.toolName,
        label: definition?.name ?? call.payload.toolName,
        description: definition?.description,
        status,
        statusLabel: getStatusLabel(status),
        createdAt: call.createdAt,
        completedAt: result?.createdAt,
        argumentsSummary: summarizeToolValue(call.payload.arguments),
        resultSummary: summarizeToolValue(result?.payload.result),
        error: result?.payload.error,
        actions: getActions(status),
      };
    });
}

export function getPendingToolExecutionRows(rows: ToolExecutionRow[]): ToolExecutionRow[] {
  return rows.filter((row) => row.status === 'requested');
}
