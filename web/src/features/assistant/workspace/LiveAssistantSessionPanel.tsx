import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { OmnixStatusPill } from '../../../design/primitives';
import type { LiveAssistantSessionApi } from './useLiveAssistantSession';

export type LiveAssistantSessionPanelProps = {
  session: LiveAssistantSessionApi;
  title?: string;
  description?: string;
};

export function LiveAssistantSessionPanel({
  session,
  title = 'Live assistant',
  description = 'Capture audio, run a live assistant turn, and queue spoken playback.',
}: LiveAssistantSessionPanelProps) {
  const canSubmit = session.status === 'capturing';
  const isBusy = session.status === 'capturing' || session.status === 'processing';

  return (
    <Stack gap="sm" aria-label="Live assistant session">
      <Group justify="space-between" align="start">
        <div>
          <Title order={4}>{title}</Title>
          <Text size="sm">{description}</Text>
        </div>
        <OmnixStatusPill>{session.status}</OmnixStatusPill>
      </Group>

      <Group gap="sm">
        <Button type="button" onClick={() => void session.start()} disabled={isBusy}>
          Start capture
        </Button>
        <Button type="button" onClick={() => void session.submitCapturedTurn()} disabled={!canSubmit}>
          Submit turn
        </Button>
        <Button type="button" variant="light" onClick={session.stop} disabled={session.status === 'idle'}>
          Stop
        </Button>
        <Button type="button" variant="subtle" onClick={session.reset} disabled={session.status === 'idle'}>
          Reset
        </Button>
      </Group>

      {session.result ? (
        <div className="platform-empty" role="status" aria-label="Live assistant result">
          <strong>{session.result.transcript.text}</strong>
          <br />
          {session.result.assistantText}
        </div>
      ) : null}

      {session.error ? (
        <div className="platform-empty" role="alert">
          {session.error}
        </div>
      ) : null}
    </Stack>
  );
}
