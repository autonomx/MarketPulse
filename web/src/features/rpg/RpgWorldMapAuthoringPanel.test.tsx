import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldMapAuthoringPanel } from './RpgWorldMapAuthoringPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const world = {
  id: 'world:aurelia:abc123',
  title: 'Aurelia',
  description: '',
  status: 'published',
  source_mode: 'hybrid',
  genre: 'classic_fantasy',
  tone: 'heroic adventure',
  seed: 2,
  draft_revision: 2,
  metadata: {},
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

const blueprint = {
  world_id: world.id,
  map_id: 'map:moon_market:ground_floor',
  blueprint_revision: 2,
  document: {
    schema_version: 'rpg_map_blueprint_v1',
    map_id: 'map:moon_market:ground_floor',
    location_id: 'location:moon_market',
    level: 'exterior',
    navigation_kind: 'square_grid',
    required_portal_ids: [],
    required_route_ids: ['route:east_gate'],
    required_spawn_point_ids: ['spawn:arrival'],
    required_zone_ids: ['zone:market'],
    required_object_ids: [],
    required_hazard_ids: [],
    size_profile: 'medium',
    directives: {},
    metadata: {},
  },
  content_hash: 'sha256:blueprint',
  semantic_interface_hash: 'sha256:semantic',
  status: 'ready',
  findings: [],
  created_at: '2026-07-20T00:00:00Z',
};

const detail = {
  ok: true,
  world,
  topics: [{
    topic_id: 'locations',
    draft_revision: 2,
    source: 'ai',
    status: 'ready',
    content: { entities: [{ id: 'location:moon_market', name: 'Moon Market', kind: 'location' }] },
    directives: {},
    dependency_hashes: {},
    input_hash: '',
    content_hash: 'sha256:locations',
    provenance: {},
    updated_at: '2026-07-20T00:00:00Z',
  }],
  map_blueprints: [blueprint],
  revisions: [{
    revision: 3,
    document: { blueprint_requirements: [{ location_id: 'location:moon_market' }] },
    content_hash: 'sha256:world',
    created_at: '2026-07-20T00:00:00Z',
  }],
  releases: [],
  scenarios: [],
  scenario_revisions: {},
  generation_runs: [],
};

function renderPanel() {
  const client = createTestQueryClient();
  render(
    <QueryClientProvider client={client}>
      <RpgWorldMapAuthoringPanel worldId={world.id} />
    </QueryClientProvider>,
  );
}

describe('RpgWorldMapAuthoringPanel', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        return jsonResponse({
          ok: true,
          map_blueprint: {
            ...blueprint,
            blueprint_revision: 3,
            document: body.document,
          },
        });
      }
      return jsonResponse(detail);
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('loads an existing blueprint and saves the next semantic revision', async () => {
    renderPanel();

    expect(await screen.findByRole('heading', { name: 'Map' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: /Moon Market/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Next Revision' }));
    expect(screen.getByDisplayValue('2')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Structured blueprint JSON'), {
      target: { value: JSON.stringify({ ...blueprint.document, size_profile: 'large' }) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Next Revision' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'POST')).toBe(true));
    const saved = requests.find((request) => request.init?.method === 'POST');
    expect(saved?.url).toContain('/map-blueprints/map%3Amoon_market%3Aground_floor');
    expect(JSON.parse(String(saved?.init?.body))).toMatchObject({
      expected_revision: 2,
      document: {
        map_id: 'map:moon_market:ground_floor',
        location_id: 'location:moon_market',
        size_profile: 'large',
      },
    });
    expect(await screen.findByText(/r3 is ready/)).toBeInTheDocument();
  });
});
