import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldAuthoringWorkspace } from './RpgWorldAuthoringWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const createdWorld = {
  id: 'world:glass-sea:abc123',
  title: 'The Glass Sea',
  description: 'A realm of mirrored tides.',
  status: 'draft',
  source_mode: 'hybrid',
  genre: 'classic_fantasy',
  tone: 'heroic adventure',
  seed: 0,
  draft_revision: 1,
  metadata: { campaign_template: 'classic_fantasy' },
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
};

describe('RpgWorldAuthoringWorkspace', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('creates worlds without exposing or submitting a technical world id', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    let created = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST' && url.endsWith('/api/rpg/worlds')) {
        created = true;
        return jsonResponse({ ok: true, world: createdWorld });
      }
      if (url.includes('/authoring-manifest')) {
        return jsonResponse({
          ok: true,
          world: createdWorld,
          sections: [{
            id: 'overview',
            label: 'Overview',
            group: 'workspace',
            page_kind: 'document',
            topic_ids: [],
            dependencies: [],
            required_before_launch: false,
            supports_generation: false,
            supports_images: false,
            supports_entity_editing: false,
            operational_status: 'complete',
            editorial_status: 'unreviewed',
            entity_count: 1,
          }],
          generation: {},
        });
      }
      if (url.includes('/authoring-sections/overview')) {
        return jsonResponse({
          ok: true,
          section_id: 'overview',
          page_kind: 'document',
          title: createdWorld.title,
          summary: createdWorld.description,
          body: [],
          related_entities: [],
        });
      }
      return jsonResponse({
        ok: true,
        worlds: created ? [createdWorld] : [],
        scenarios: [],
        campaigns: [],
        generation_runs: [],
      });
    }));

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldAuthoringWorkspace onBack={vi.fn()} onSessionLaunched={vi.fn()} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Create New World' }));
    expect(screen.queryByLabelText(/world id/i)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'The Glass Sea' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'A realm of mirrored tides.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create World' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'POST')).toBe(true));
    const create = requests.find((request) => request.init?.method === 'POST');
    const body = JSON.parse(String(create?.init?.body));
    expect(body).toMatchObject({ title: 'The Glass Sea', source_mode: 'hybrid' });
    expect(body).not.toHaveProperty('world_id');
  });
});
