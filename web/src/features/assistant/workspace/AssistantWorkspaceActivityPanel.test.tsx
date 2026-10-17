import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../../design/theme';
import { AssistantWorkspaceActivityPanel } from './AssistantWorkspaceActivityPanel';
import type { AssistantWorkspaceEvent } from './events';

function renderPanel(events: AssistantWorkspaceEvent[], handlers = {}) {
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <AssistantWorkspaceActivityPanel
        events={events}
        capabilities={[
          {
            id: 'search',
            name: 'Search',
            description: 'Search workspace knowledge',
            scope: 'workspace',
            enabled: true,
          },
        ]}
        {...handlers}
      />
    </MantineProvider>,
  );
}

const userMessage: AssistantWorkspaceEvent = {
  id: 'event:user',
  type: 'user_message',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    turn: {
      id: 'turn:user',
      sessionId: 'session:main',
      role: 'user',
      content: [{ kind: 'text', text: 'Find the latest tool result' }],
      metadata: {},
      createdAt: '2026-06-23T10:00:00.000Z',
    },
  },
  createdAt: '2026-06-23T10:00:00.000Z',
};

const toolCall: AssistantWorkspaceEvent = {
  id: 'event:tool-call',
  type: 'tool_call',
  workspaceId: 'workspace:main',
  sessionId: 'session:main',
  payload: {
    toolCallId: 'tool-call:1',
    toolName: 'search',
    arguments: { query: 'tool result' },
    approved: false,
  },
  createdAt: '2026-06-23T10:00:01.000Z',
};

describe('AssistantWorkspaceActivityPanel', () => {
  it('renders an empty activity state', () => {
    renderPanel([]);

    expect(screen.getByText('No replayable workspace events are available yet.')).toBeInTheDocument();
    expect(screen.getByText('No tool calls have been requested for this session.')).toBeInTheDocument();
  });

  it('renders timeline and tool approval UX from events', () => {
    const onApproveTool = vi.fn();
    const onDenyTool = vi.fn();

    renderPanel([userMessage, toolCall], { onApproveTool, onDenyTool });

    expect(screen.getByRole('listitem', { name: 'User: Find the latest tool result timeline item' })).toHaveTextContent(
      'Source: user_message',
    );
    expect(screen.getByRole('listitem', { name: 'Tool approval requested: search timeline item' })).toHaveTextContent(
      'requested',
    );
    expect(screen.getByRole('group', { name: 'Search tool execution' })).toHaveTextContent('Approval requested');

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onApproveTool).toHaveBeenCalledOnce();
    expect(onDenyTool).toHaveBeenCalledOnce();
  });
});
