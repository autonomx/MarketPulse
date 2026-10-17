import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryManagementPanel } from './MemoryManagementPanel';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel(sessionId: string | null = 'chat:one') {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryManagementPanel sessionId={sessionId} />
    </QueryClientProvider>,
  );
}

function record() {
  return {
    id: 'memory:one',
    scope: 'project',
    scope_id: 'project:omnix',
    category: 'instruction',
    source: 'user_saved',
    content: 'Use GitHub Actions as verification truth.',
    normalized_content: 'use github actions as verification truth',
    confidence: 1,
    pinned: false,
    trust_level: 'user_approved',
    sensitivity: 'normal',
    provenance_type: 'user_message',
    provenance_id: 'msg:one',
    status: 'active',
    revision: 1,
    created_at: '2026-07-08T00:00:00+00:00',
    updated_at: '2026-07-08T00:00:00+00:00',
    expires_at: null,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('MemoryManagementPanel', () => {
  it('renders backend-derived snapshot, saved memory, and pending candidates', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname.endsWith('/memory') && url.pathname.includes('/api/chat/sessions/')) {
        return Response.json({
          session_id: 'chat:one',
          memory_enabled: true,
          snapshot_id: 'snapshot:one',
          snapshot_revision: 2,
          memory_record_count: 1,
          snapshot: { snapshot_id: 'snapshot:one', revision: 2, token_estimate: 22, active_count: 1, invalidated_count: 0, items: [] },
        });
      }
      if (url.pathname === '/api/assistant/memory/candidates/pending') {
        return Response.json({
          session_id: 'chat:one',
          total: 1,
          candidates: [{
            id: 'candidate:one', source_session_id: 'chat:one', source_message_id: 'msg:two',
            proposed_scope: 'project', proposed_scope_id: 'project:omnix', proposed_category: 'preference',
            proposed_content: 'Prefer narrow pull requests.', confidence: 0.9, source: 'assistant_suggested',
            trust_level: 'unverified_agent', status: 'pending', created_at: '2026-07-08T00:00:00+00:00',
          }],
        });
      }
      if (url.pathname === '/api/assistant/memory') {
        return Response.json({ session_id: 'chat:one', total: 1, records: [record()] });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();

    expect(await screen.findByText('1 active records')).toBeInTheDocument();
    expect(screen.getByText('Use GitHub Actions as verification truth.')).toBeInTheDocument();
    expect(screen.getByText('Prefer narrow pull requests.')).toBeInTheDocument();
    expect(screen.getByText(/Snapshot revision: 2/)).toBeInTheDocument();
  });

  it('creates explicit memory and refreshes query-backed state', async () => {
    const posted: unknown[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/assistant/memory' && init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)));
        return Response.json(record());
      }
      if (url.pathname.endsWith('/memory') && url.pathname.includes('/api/chat/sessions/')) {
        return Response.json({ session_id: 'chat:one', memory_enabled: false, memory_record_count: 0, snapshot: null });
      }
      if (url.pathname === '/api/assistant/memory/candidates/pending') {
        return Response.json({ session_id: 'chat:one', total: 0, candidates: [] });
      }
      if (url.pathname === '/api/assistant/memory') {
        return Response.json({ session_id: 'chat:one', total: 0, records: [] });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();
    await screen.findByText('No saved memory matches the current filters.');
    fireEvent.change(screen.getByLabelText('New memory content'), { target: { value: 'Remember this workflow.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save memory' }));

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ session_id: 'chat:one', scope: 'session', category: 'preference', content: 'Remember this workflow.' });
    expect(await screen.findByRole('status')).toHaveTextContent('Memory saved. Refresh the active snapshot to use it in this chat.');
  });

  it('requires a selected chat session', () => {
    vi.stubGlobal('fetch', vi.fn());
    renderPanel(null);
    expect(screen.getByText('Create or select a Chat session before managing memory.')).toBeInTheDocument();
  });
});
