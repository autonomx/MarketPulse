import type { AssistantWorkspaceEvent, AssistantWorkspaceEventType } from './events';
import type { MessageContent } from './conversation';

export type TimelineItemKind = 'turn' | 'event' | 'note';

export type TimelineItem = {
  id: string;
  kind: TimelineItemKind;
  createdAt: string;
  label: string;
  sourceEventType?: AssistantWorkspaceEventType;
  sourceEventId?: string;
  status?: string;
};

export function sortTimelineItems(items: TimelineItem[]): TimelineItem[] {
  return [...items].sort((left, right) => left.createdAt.localeCompare(right.createdAt) || left.id.localeCompare(right.id));
}

export function filterTimelineItemsByKind(items: TimelineItem[], kind: TimelineItemKind): TimelineItem[] {
  return items.filter((item) => item.kind === kind);
}

export function createTimelineNote(id: string, label: string, createdAt: string): TimelineItem {
  return { id, kind: 'note', label, createdAt };
}

function getTextContent(content: MessageContent[]): string | undefined {
  return content.find((item) => item.kind === 'text')?.text;
}

function truncateLabel(label: string, maxLength = 72): string {
  if (label.length <= maxLength) return label;
  return `${label.slice(0, maxLength - 1)}…`;
}

function createTurnTimelineItem(event: Extract<AssistantWorkspaceEvent, { type: 'user_message' | 'assistant_message' }>): TimelineItem {
  const turn = event.payload.turn;
  const prefix = event.type === 'user_message' ? 'User' : 'Assistant';
  const text = getTextContent(turn.content);

  return {
    id: event.id,
    kind: 'turn',
    createdAt: event.createdAt,
    label: text ? truncateLabel(`${prefix}: ${text}`) : `${prefix} message`,
    sourceEventType: event.type,
    sourceEventId: event.id,
    status: turn.role,
  };
}

function createToolTimelineItem(event: Extract<AssistantWorkspaceEvent, { type: 'tool_call' | 'tool_result' }>): TimelineItem {
  if (event.type === 'tool_call') {
    const approved = event.payload.approved !== false;
    return {
      id: event.id,
      kind: 'event',
      createdAt: event.createdAt,
      label: `${approved ? 'Tool running' : 'Tool approval requested'}: ${event.payload.toolName}`,
      sourceEventType: event.type,
      sourceEventId: event.id,
      status: approved ? 'running' : 'requested',
    };
  }

  return {
    id: event.id,
    kind: 'event',
    createdAt: event.createdAt,
    label: `Tool ${event.payload.status}: ${event.payload.toolCallId}`,
    sourceEventType: event.type,
    sourceEventId: event.id,
    status: event.payload.status,
  };
}

function createFailureTimelineItem(event: Extract<AssistantWorkspaceEvent, { type: 'operation_failed' }>): TimelineItem {
  return {
    id: event.id,
    kind: 'event',
    createdAt: event.createdAt,
    label: truncateLabel(`${operationLabel(event.payload.operation)} failed: ${event.payload.message}`),
    sourceEventType: event.type,
    sourceEventId: event.id,
    status: 'failed',
  };
}

function operationLabel(operation: string): string {
  return operation.replaceAll('_', ' ');
}

function createGenericEventTimelineItem(event: AssistantWorkspaceEvent): TimelineItem {
  return {
    id: event.id,
    kind: 'event',
    createdAt: event.createdAt,
    label: event.type.replaceAll('_', ' '),
    sourceEventType: event.type,
    sourceEventId: event.id,
  };
}

export function createTimelineItemsFromEvents(events: AssistantWorkspaceEvent[]): TimelineItem[] {
  return sortTimelineItems(
    events.map((event) => {
      if (event.type === 'user_message' || event.type === 'assistant_message') {
        return createTurnTimelineItem(event);
      }
      if (event.type === 'tool_call' || event.type === 'tool_result') {
        return createToolTimelineItem(event);
      }
      if (event.type === 'operation_failed') {
        return createFailureTimelineItem(event);
      }
      return createGenericEventTimelineItem(event);
    }),
  );
}
