import { MantineProvider } from '@mantine/core';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../../design/theme';
import { ToolExecutionPanel } from './ToolExecutionPanel';
import type { ToolExecutionRow } from './tool-execution-view';

function renderPanel(rows: ToolExecutionRow[], handlers = {}) {
  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <ToolExecutionPanel rows={rows} {...handlers} />
    </MantineProvider>,
  );
}

const requestedRow: ToolExecutionRow = {
  id: 'call:1',
  toolCallId: 'tool-call:1',
  toolName: 'search',
  label: 'Search',
  description: 'Search workspace knowledge',
  status: 'requested',
  statusLabel: 'Approval requested',
  createdAt: '2026-06-23T09:00:00.000Z',
  argumentsSummary: '{"query":"omnix"}',
  actions: ['approve', 'deny'],
};

describe('ToolExecutionPanel', () => {
  it('renders an empty tool execution state', () => {
    renderPanel([]);

    expect(screen.getByText('No tool calls have been requested for this session.')).toBeInTheDocument();
    expect(screen.getByText('No tools')).toBeInTheDocument();
  });

  it('renders approval actions and invokes handlers', () => {
    const onApprove = vi.fn();
    const onDeny = vi.fn();

    renderPanel([requestedRow], { onApprove, onDeny });

    expect(screen.getByRole('group', { name: 'Search tool execution' })).toHaveTextContent('Approval requested');
    expect(screen.getByRole('group', { name: 'Search tool execution' })).toHaveTextContent('Arguments: {"query":"omnix"}');

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }));

    expect(onApprove).toHaveBeenCalledWith(requestedRow);
    expect(onDeny).toHaveBeenCalledWith(requestedRow);
  });

  it('renders retry action for failed tools', () => {
    const onRetry = vi.fn();
    const failedRow: ToolExecutionRow = {
      ...requestedRow,
      status: 'failed',
      statusLabel: 'Failed',
      error: 'network down',
      actions: ['retry'],
    };

    renderPanel([failedRow], { onRetry });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(screen.getByRole('group', { name: 'Search tool execution' })).toHaveTextContent('Error: network down');
    expect(onRetry).toHaveBeenCalledWith(failedRow);
  });
});
