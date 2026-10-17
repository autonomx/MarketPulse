import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { LiveAgentToolProposalCard, liveAgentToolProposals } from './LiveAgentToolProposalCard';
import type { LiveAgentToolProposal } from './assistantToolConfigClient';

const proposal: LiveAgentToolProposal = {
  proposal_id: 'proposal-1',
  tool_id: 'calendar',
  action_id: 'calendar.create_event',
  title: 'Create Google Calendar event',
  summary: 'Review the event before it is created.',
  input: {
    query: 'Schedule planning tomorrow at nine.',
    title: 'Planning',
    start_time: '2026-07-10T09:00:00',
    end_time: '2026-07-10T09:30:00',
    timezone: 'America/Vancouver',
  },
  risk_level: 'medium',
  approval_required: true,
  ready_for_approval: true,
  connection_required: false,
  missing_fields: [],
  executes: false,
};

describe('LiveAgentToolProposalCard', () => {
  it('requires an explicit click and proposes, approves, then executes the server id', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ proposal_id: 'server-proposal', approval_required: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ proposal_id: 'server-proposal', decision: 'approved' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
      approval_decision: { executable: true, approval_required: true },
      execution_result: { state_changed: true, result_summary: 'Created calendar event.', output: {} },
      state_changed: true,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    render(<LiveAgentToolProposalCard proposal={proposal} sessionId="chat:1" onOpenTools={vi.fn()} />);

    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Approve and create' }));

    await waitFor(() => expect(screen.getByText('Created calendar event.')).toBeInTheDocument());
    const request = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(request).toEqual({ tool_id: 'calendar', action_id: 'calendar.create_event', session_id: 'chat:1', input: proposal.input });
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/assistant/tools/proposals',
      '/api/assistant/tools/proposals/server-proposal/approve',
      '/api/assistant/tools/proposals/server-proposal/execute',
    ]);
    vi.unstubAllGlobals();
  });

  it('filters malformed message metadata', () => {
    expect(liveAgentToolProposals({ assistant_tool_proposals: [{ proposal_id: 'bad' }] })).toEqual([]);
    expect(liveAgentToolProposals({ assistant_tool_proposals: [proposal] })).toEqual([proposal]);
  });
});
