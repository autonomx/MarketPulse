import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgStarterBubblePromotionPanel } from './RpgStarterBubblePromotionPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

function requestPath(input: RequestInfo | URL): string {
  const pathname = typeof input === 'string'
    ? new URL(input, 'http://localhost').pathname
    : new URL(input.toString(), 'http://localhost').pathname;
  return decodeURIComponent(pathname);
}

function renderPanel() {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <RpgStarterBubblePromotionPanel />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RpgStarterBubblePromotionPanel', () => {
  it('previews, promotes, and materializes progressive map revisions', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/world-library') {
        return Response.json({
          ok: true,
          worlds: [{
            id: 'world:starter',
            title: 'Starter World',
            description: '',
            status: 'published',
            source_mode: 'hybrid',
            genre: 'classic_fantasy',
            tone: 'heroic adventure',
            seed: 1,
            draft_revision: 1,
            metadata: {},
            created_at: '2026-07-16T00:00:00Z',
            updated_at: '2026-07-16T00:00:00Z',
          }],
          scenarios: [],
          campaigns: [],
          generation_runs: [],
        });
      }
      if (path.endsWith('/library')) {
        return Response.json({
          ok: true,
          world: { id: 'world:starter' },
          topics: [],
          revisions: [{
            revision: 1,
            document: {},
            content_hash: 'sha256:revision',
            created_at: '2026-07-16T00:00:00Z',
          }],
          releases: [],
          scenarios: [],
          scenario_revisions: {},
          generation_runs: [],
        });
      }
      if (path.endsWith('/starter-bubble/preview')) {
        return Response.json({
          ok: true,
          starter_bubble: {
            slots: [
              { role: 'region', simulation_readiness: 'semantic', presentation_readiness: 'placeholder' },
              { role: 'settlement', simulation_readiness: 'navigable', presentation_readiness: 'placeholder' },
              { role: 'interior', simulation_readiness: 'navigable', presentation_readiness: 'placeholder' },
              { role: 'neighbor', simulation_readiness: 'navigable', presentation_readiness: 'assets_pending' },
              { role: 'frontier', deferred: true, simulation_readiness: 'semantic', presentation_readiness: 'assets_pending' },
            ],
          },
          predictive_materialization: [{
            location_id: 'northern_road:frontier',
            fallback: 'navigable_placeholder',
          }],
        });
      }
      if (path.includes('/deferred-locations/') && path.endsWith('/materialize') && init?.method === 'POST') {
        return Response.json({
          ok: true,
          status: 'ready',
          reused: false,
          materialization: {
            location_id: 'northern_road:frontier',
            world_revision: 3,
            world_release: 1,
          },
        });
      }
      if (path.endsWith('/starter-bubble/promote') && init?.method === 'POST') {
        return Response.json({
          ok: true,
          status: 'ready',
          reused: false,
          promotion: { world_revision: 2, world_release: 1 },
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderPanel();

    const summary = screen.getByText('Starter bubble promotion').closest('summary');
    expect(summary).not.toBeNull();
    fireEvent.click(summary as HTMLElement);
    expect(await screen.findByRole('option', { name: 'Starter World' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Preview starter bubble' }));
    expect(await screen.findByText('5 planned locations')).toBeInTheDocument();
    expect(screen.getByText('1 predictive jobs')).toBeInTheDocument();
    expect(screen.getByText(/Optional art remains non-blocking/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Materialize northern_road:frontier' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Promote to future revision' }));
    await waitFor(() => {
      expect(screen.getByText('Promoted to world revision 2 / release 1.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Materialize northern_road:frontier' })).toBeEnabled();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Materialize northern_road:frontier' }));
    await waitFor(() => {
      expect(screen.getByText('Materialized northern_road:frontier in world revision 3.')).toBeInTheDocument();
    });
  });
});
