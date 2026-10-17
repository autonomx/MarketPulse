import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../../design/theme';
import { LiveAssistantSessionPanel } from './LiveAssistantSessionPanel';
import type { LiveAssistantSessionApi } from './useLiveAssistantSession';

function renderPanel(session: LiveAssistantSessionApi) {
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <LiveAssistantSessionPanel session={session} />
    </MantineProvider>,
  );
}

function session(overrides: Partial<LiveAssistantSessionApi> = {}): LiveAssistantSessionApi {
  return {
    status: 'idle',
    start: vi.fn(async () => undefined),
    submitCapturedTurn: vi.fn(async () => undefined),
    stop: vi.fn(),
    reset: vi.fn(),
    ...overrides,
  };
}

describe('LiveAssistantSessionPanel', () => {
  it('starts capture from idle state', () => {
    const api = session();

    renderPanel(api);
    fireEvent.click(screen.getByRole('button', { name: 'Start capture' }));

    expect(api.start).toHaveBeenCalledOnce();
    expect(screen.getByText('idle')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Submit turn' })).toBeDisabled();
  });

  it('submits a captured turn while capturing', () => {
    const api = session({ status: 'capturing' });

    renderPanel(api);
    fireEvent.click(screen.getByRole('button', { name: 'Submit turn' }));

    expect(api.submitCapturedTurn).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Start capture' })).toBeDisabled();
  });

  it('renders transcript, assistant result, and errors', () => {
    const api = session({
      status: 'error',
      error: 'Microphone denied.',
      result: {
        sessionId: 'session:voice',
        transcript: { text: 'hello' },
        modelRequest: { provider: 'local', model: 'qwen', messages: [] },
        modelResponse: { content: [{ kind: 'text', text: 'hi' }] },
        assistantText: 'hi',
        synthesis: { audioUrl: 'blob:hi' },
        playbackItem: { id: 'playback:1', text: 'hi', createdAt: '2026-06-23T09:00:01Z' },
        stages: ['transcribed', 'responded', 'synthesized', 'queued'],
        events: [],
      },
    });

    renderPanel(api);

    expect(screen.getByRole('status', { name: 'Live assistant result' })).toHaveTextContent('hello');
    expect(screen.getByRole('status', { name: 'Live assistant result' })).toHaveTextContent('hi');
    expect(screen.getByRole('alert')).toHaveTextContent('Microphone denied.');
  });
});
