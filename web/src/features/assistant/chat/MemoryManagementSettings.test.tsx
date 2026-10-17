import { QueryClientProvider } from '@tanstack/react-query';
import { fixture } from '../../../test/fixture';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryManagementPanel } from './MemoryManagementPanel';
import type { AssistantMemoryRuntimeStatus, ManagedMemoryRecord } from './memoryClient';
import { createTestQueryClient } from '../../../test/renderWithProviders';

function renderPanel() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryManagementPanel sessionId="chat:one" />
    </QueryClientProvider>,
  );
}

function settings(curated = false): AssistantMemoryRuntimeStatus {
  return fixture({
    settings: {
      curated_memory_enabled: curated,
      suggestions_enabled: false,
      history_recall_enabled: false,
      compaction_enabled: false,
      hermes_sync_enabled: false,
      require_approval_for_inferred_memory: true,
      automatic_direct_assertion_memory: false,
      proactive_memory_enabled: true,
      paralinguistic_signals_enabled: true,
      transcript_retention_enabled: true,
      companion_master_enabled: true,
      companion_rollout_stage: 'paralinguistic_pilot',
      memory_token_budget: 4000,
      history_token_budget: 8000,
      retention_days: 365,
      show_memory_use_indicator: true,
    },
    environment_overrides: [],
    approval_policy_locked: true,
    diagnostics_policy: 'content_free',
  });
}

function record(overrides: Partial<ManagedMemoryRecord> = {}): ManagedMemoryRecord {
  return fixture({
    id: 'memory:one',
    scope: 'session',
    scope_id: 'chat:one',
    category: 'preference',
    kind: 'preference',
    structured_payload: { automatic_direct_assertion: true },
    source: 'user_saved',
    content: 'The user prefers quiet mornings',
    confidence: 0.95,
    pinned: false,
    trust_level: 'user_approved',
    provenance_type: 'user_message',
    provenance_id: 'msg:one',
    status: 'active',
    revision: 1,
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    ...overrides,
  });
}

function commonResponse(url: URL) {
  if (url.pathname === '/api/assistant/memory/metrics') {
    return Response.json({ turns: 7, counters: {}, totals: {}, maxima: {}, diagnostics_policy: 'content_free' });
  }
  if (url.pathname === '/api/assistant/memory/archived') {
    return Response.json({ session_id: 'chat:one', total: 0, records: [] });
  }
  if (url.pathname === '/api/assistant/memory/recent-automatic') {
    return Response.json({ session_id: 'chat:one', records: [] });
  }
  if (url.pathname === '/api/assistant/memory/usage') {
    return Response.json({ session_id: 'chat:one', recorded_at: '2026-07-19T00:00:00Z', items: [], diagnostics_policy: 'content_free' });
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
}

afterEach(() => vi.unstubAllGlobals());

describe('MemoryManagementPanel settings', () => {
  it('shows server settings and persists independent toggles and rollout stage', async () => {
    const updates: unknown[] = [];
    let current = settings(false);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/assistant/memory/settings') {
        if (init?.method === 'POST') {
          const update = JSON.parse(String(init.body));
          updates.push(update);
          current = {
            ...current,
            settings: { ...current.settings, ...update },
          };
        }
        return Response.json(current);
      }
      return commonResponse(url);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();
    const toggle = await screen.findByRole('checkbox', { name: 'Use approved memory in Chat' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(updates).toContainEqual({ curated_memory_enabled: true }));

    const stage = await screen.findByRole('combobox', { name: 'Companion rollout stage' });
    fireEvent.change(stage, { target: { value: 'active_initiative' } });
    await waitFor(() => expect(updates).toContainEqual({ companion_rollout_stage: 'active_initiative' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Memory settings saved.');
    expect(await screen.findByText('7')).toBeInTheDocument();
  });

  it('disables environment-controlled settings and explains approval privacy', async () => {
    const controlled = settings(true);
    controlled.environment_overrides = ['curated_memory_enabled'];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString(), 'http://localhost');
      if (url.pathname === '/api/assistant/memory/settings') return Response.json(controlled);
      return commonResponse(url);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();

    const toggle = await screen.findByRole('checkbox', { name: /Use approved memory in Chat/ });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Inferred memory approval remains required/)).toBeInTheDocument();
  });

  it('shows automatic-memory undo, usage reasons, archive, export, and reset controls', async () => {
    const saved = record();
    const requests: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString(), 'http://localhost');
      requests.push(`${init?.method ?? 'GET'} ${url.pathname}`);
      if (url.pathname === '/api/assistant/memory/settings') return Response.json(settings(true));
      if (url.pathname === '/api/assistant/memory') return Response.json({ session_id: 'chat:one', total: 1, records: [saved] });
      if (url.pathname === '/api/assistant/memory/archived') return Response.json({ session_id: 'chat:one', total: 0, records: [] });
      if (url.pathname === '/api/assistant/memory/recent-automatic') return Response.json({ session_id: 'chat:one', records: [saved] });
      if (url.pathname === '/api/assistant/memory/usage') return Response.json({ session_id: 'chat:one', recorded_at: '2026-07-19T00:00:00Z', diagnostics_policy: 'content_free', items: [{ memory_id: saved.id, selection_reason: 'current_turn_term_overlap', activation_score: 625, section: 'communication_preferences', source_revision: 1 }] });
      if (url.pathname.endsWith('/undo')) return Response.json({ ok: true, memory_id: saved.id });
      if (url.pathname.endsWith('/archive')) return Response.json({ ...saved, status: 'archived', revision: 2 });
      return commonResponse(url);
    });
    vi.stubGlobal('fetch', fetchMock);

    renderPanel();

    expect(await screen.findByLabelText('Automatically remembered memory')).toHaveTextContent('Remembered —');
    expect(await screen.findByText(/Reason: current turn term overlap/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Export memory JSON' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reset all memory' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(requests).toContain('POST /api/assistant/memory/memory%3Aone/undo'));

    fireEvent.click(screen.getByRole('button', { name: 'Archive' }));
    await waitFor(() => expect(requests).toContain('POST /api/assistant/memory/memory%3Aone/archive'));
  });
});
