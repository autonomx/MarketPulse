import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldAuthoringWorkspace } from './RpgWorldAuthoringWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

const world = {
  id: 'world:disposable',
  title: 'Disposable Draft',
  description: 'A temporary world.',
  status: 'draft',
  source_mode: 'hybrid',
  genre: 'fantasy',
  tone: 'heroic',
  seed: 1,
  draft_revision: 1,
  metadata: {},
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
};

function renderWorkspace() {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <RpgWorldAuthoringWorkspace onBack={vi.fn()} onSessionLaunched={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path.includes('deletion-eligibility')) {
      return new Response(JSON.stringify({
        ok: true,
        eligibility: {
          can_delete: true,
          world_id: world.id,
          world_title: world.title,
          world_status: world.status,
          blockers: [],
          deleted_counts: {},
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({
      ok: true,
      worlds: [world],
      scenarios: [],
      campaigns: [],
      generation_runs: [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
});

afterEach(() => vi.unstubAllGlobals());

describe('world library deletion option', () => {
  it('opens the protected deletion flow from a world card', async () => {
    renderWorkspace();

    const heading = await screen.findByRole('heading', { name: world.title });
    const card = heading.closest('article');
    expect(card).not.toBeNull();
    fireEvent.click(within(card as HTMLElement).getByText('More'));
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Delete world' }));

    expect(await screen.findByRole('dialog', { name: `Delete ${world.title}` })).toBeInTheDocument();
    expect(screen.getByText(/Permanently delete this world at any state/)).toBeInTheDocument();
  });
});
