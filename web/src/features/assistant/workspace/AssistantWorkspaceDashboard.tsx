import { Group, Stack, Text, Title } from '@mantine/core';
import { OmnixStatusPill } from '../../../design/primitives';
import {
  createAssistantWorkspaceDashboard,
  type AssistantWorkspaceDashboardInput,
  type AssistantWorkspaceDashboardView,
} from './workspace-dashboard';

export function AssistantWorkspaceDashboard({ dashboard }: { dashboard: AssistantWorkspaceDashboardView }) {
  return (
    <Stack gap="sm" aria-label="Assistant workspace dashboard">
      <Group justify="space-between" align="start">
        <div>
          <Title order={4}>Assistant workspace</Title>
          <Text size="sm">{dashboard.subtitle}</Text>
        </div>
        <OmnixStatusPill>{dashboard.statusLabel}</OmnixStatusPill>
      </Group>

      <div role="group" className="feature-list" aria-label="Workspace context metrics">
        {dashboard.metrics.map((metric) => (
          <div key={metric.id}>
            <span>{metric.label}</span>
            <small>{metric.value}</small>
          </div>
        ))}
      </div>

      <Group gap="xs" aria-label="Workspace badges">
        {dashboard.badges.map((badge) => (
          <OmnixStatusPill key={badge}>{badge}</OmnixStatusPill>
        ))}
      </Group>

      {dashboard.failedQualitySignals.length ? (
        <div className="platform-empty" role="status" aria-label="Workspace issues">
          {dashboard.failedQualitySignals.map((signal) => signal.label).join(' · ')}
        </div>
      ) : (
        <div className="platform-empty" role="status">
          Event stream, context, provider, and UI projections are aligned.
        </div>
      )}
    </Stack>
  );
}

export function AssistantWorkspaceDashboardPanel({ input }: { input: AssistantWorkspaceDashboardInput }) {
  return <AssistantWorkspaceDashboard dashboard={createAssistantWorkspaceDashboard(input)} />;
}
