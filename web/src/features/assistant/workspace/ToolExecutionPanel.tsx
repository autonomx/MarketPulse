import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { OmnixStatusPill } from '../../../design/primitives';
import type { ToolExecutionAction, ToolExecutionRow } from './tool-execution-view';

export type ToolExecutionPanelProps = {
  rows: ToolExecutionRow[];
  title?: string;
  description?: string;
  onApprove?: (row: ToolExecutionRow) => void;
  onDeny?: (row: ToolExecutionRow) => void;
  onRetry?: (row: ToolExecutionRow) => void;
};

function actionLabel(action: ToolExecutionAction): string {
  switch (action) {
    case 'approve':
      return 'Approve';
    case 'deny':
      return 'Deny';
    case 'retry':
      return 'Retry';
  }
}

function invokeAction(
  action: ToolExecutionAction,
  row: ToolExecutionRow,
  handlers: Pick<ToolExecutionPanelProps, 'onApprove' | 'onDeny' | 'onRetry'>,
) {
  if (action === 'approve') handlers.onApprove?.(row);
  if (action === 'deny') handlers.onDeny?.(row);
  if (action === 'retry') handlers.onRetry?.(row);
}

export function ToolExecutionPanel({
  rows,
  title = 'Tool execution',
  description = 'Review approvals and monitor tool execution results.',
  onApprove,
  onDeny,
  onRetry,
}: ToolExecutionPanelProps) {
  return (
    <Stack gap="sm" aria-label="Tool execution panel">
      <Group justify="space-between" align="start">
        <div>
          <Title order={4}>{title}</Title>
          <Text size="sm">{description}</Text>
        </div>
        <OmnixStatusPill>{rows.length ? `${rows.length} tools` : 'No tools'}</OmnixStatusPill>
      </Group>

      {rows.length ? (
        <Stack gap="xs" aria-label="Tool execution rows">
          {rows.map((row) => (
            <div key={row.id} className="platform-empty" role="group" aria-label={`${row.label} tool execution`}>
              <Group justify="space-between" align="start">
                <div>
                  <Text size="sm" fw={600}>
                    {row.label}
                  </Text>
                  {row.description ? <Text size="xs">{row.description}</Text> : null}
                </div>
                <OmnixStatusPill>{row.statusLabel}</OmnixStatusPill>
              </Group>

              {row.argumentsSummary ? <Text size="xs">Arguments: {row.argumentsSummary}</Text> : null}
              {row.resultSummary ? <Text size="xs">Result: {row.resultSummary}</Text> : null}
              {row.error ? <Text size="xs">Error: {row.error}</Text> : null}

              {row.actions.length ? (
                <Group gap="xs" mt="xs">
                  {row.actions.map((action) => (
                    <Button
                      key={action}
                      type="button"
                      size="xs"
                      variant={action === 'approve' ? 'filled' : 'light'}
                      onClick={() => invokeAction(action, row, { onApprove, onDeny, onRetry })}
                    >
                      {actionLabel(action)}
                    </Button>
                  ))}
                </Group>
              ) : null}
            </div>
          ))}
        </Stack>
      ) : (
        <div className="platform-empty" role="status">
          No tool calls have been requested for this session.
        </div>
      )}
    </Stack>
  );
}
