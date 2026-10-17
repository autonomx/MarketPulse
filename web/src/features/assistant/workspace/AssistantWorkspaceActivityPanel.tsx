import { Group, Stack, Text, Title } from '@mantine/core';
import { OmnixStatusPill } from '../../../design/primitives';
import type { AssistantWorkspaceEvent } from './events';
import type { CapabilityDefinition } from './capabilities';
import { createTimelineItemsFromEvents } from './timeline-items';
import { createToolExecutionRows, type ToolExecutionRow } from './tool-execution-view';
import { ToolExecutionPanel } from './ToolExecutionPanel';

export type AssistantWorkspaceActivityPanelProps = {
  events: AssistantWorkspaceEvent[];
  capabilities?: CapabilityDefinition[];
  onApproveTool?: (row: ToolExecutionRow) => void;
  onDenyTool?: (row: ToolExecutionRow) => void;
  onRetryTool?: (row: ToolExecutionRow) => void;
};

export function AssistantWorkspaceActivityPanel({
  events,
  capabilities = [],
  onApproveTool,
  onDenyTool,
  onRetryTool,
}: AssistantWorkspaceActivityPanelProps) {
  const timelineItems = createTimelineItemsFromEvents(events);
  const toolRows = createToolExecutionRows(events, capabilities);

  return (
    <Stack gap="md" aria-label="Assistant workspace activity">
      <Stack gap="sm">
        <Group justify="space-between" align="start">
          <div>
            <Title order={4}>Workspace activity</Title>
            <Text size="sm">Conversation, tool, and provenance events from the replay stream.</Text>
          </div>
          <OmnixStatusPill>{timelineItems.length ? `${timelineItems.length} events` : 'No events'}</OmnixStatusPill>
        </Group>

        {timelineItems.length ? (
          <Stack gap="xs" aria-label="Workspace timeline">
            {timelineItems.map((item) => (
              <div key={item.id} className="platform-empty" role="listitem" aria-label={`${item.label} timeline item`}>
                <Group justify="space-between" align="start">
                  <div>
                    <Text size="sm" fw={600}>
                      {item.label}
                    </Text>
                    <Text size="xs">{item.createdAt}</Text>
                  </div>
                  <OmnixStatusPill>{item.status ?? item.kind}</OmnixStatusPill>
                </Group>
                {item.sourceEventType ? <Text size="xs">Source: {item.sourceEventType}</Text> : null}
              </div>
            ))}
          </Stack>
        ) : (
          <div className="platform-empty" role="status">
            No replayable workspace events are available yet.
          </div>
        )}
      </Stack>

      <ToolExecutionPanel
        rows={toolRows}
        onApprove={onApproveTool}
        onDeny={onDenyTool}
        onRetry={onRetryTool}
      />
    </Stack>
  );
}
