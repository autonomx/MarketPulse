import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldScenarioAuthoringPanel } from './RpgWorldScenarioAuthoringPanel';
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

const locationTopic = {
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
};

const worldRevision = {
  revision: 3,
  document: {},
  content_hash: 'sha256:world',
  created_at: '2026-07-20T00:00:00Z',
};

const release = {
  world_revision: 3,
  release: 1,
  document: { certification: { launch_ready: true } },
  release_hash: 'sha256:release',
  created_at: '2026-07-20T00:00:00Z',
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    world,
    topics: [locationTopic],
    map_blueprints: [],
    revisions: [worldRevision],
    releases: [release],
    scenarios: [],
    scenario_revisions: {},
    generation_runs: [],
    ...overrides,
  };
}

function renderPanel() {
  const client = createTestQueryClient();
  render(
    <QueryClientProvider client={client}>
      <RpgWorldScenarioAuthoringPanel worldId={world.id} />
    </QueryClientProvider>,
  );
}

describe('RpgWorldScenarioAuthoringPanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('creates a scenario project without submitting a technical scenario id', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST') {
        return jsonResponse({
          ok: true,
          scenario: {
            id: 'scenario:first-light:def456',
            world_id: world.id,
            title: 'First Light',
            description: 'Begin at the Moon Market.',
            status: 'draft',
            metadata: { starting_location: 'location:moon_market' },
            created_at: '2026-07-20T00:00:00Z',
            updated_at: '2026-07-20T00:00:00Z',
          },
        });
      }
      return jsonResponse(detail());
    }));
    renderPanel();

    expect(await screen.findByRole('heading', { name: 'Scenarios' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'First Light' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Begin at the Moon Market.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Scenario' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'POST')).toBe(true));
    const created = requests.find((request) => request.init?.method === 'POST');
    expect(created?.url).toContain('/api/rpg/scenarios');
    const body = JSON.parse(String(created?.init?.body));
    expect(body).toMatchObject({
      world_id: world.id,
      title: 'First Light',
      metadata: { starting_location: 'location:moon_market' },
    });
    expect(body).not.toHaveProperty('scenario_id');
  });

  it('publishes the next immutable revision from the dedicated scenario editor', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const scenario = {
      id: 'scenario:first-light:def456',
      world_id: world.id,
      title: 'First Light',
      description: 'Begin at the Moon Market.',
      status: 'published',
      metadata: { starting_location: 'location:moon_market' },
      created_at: '2026-07-20T00:00:00Z',
      updated_at: '2026-07-20T00:00:00Z',
    };
    const scenarioRevision = {
      revision: 1,
      world_id: world.id,
      world_revision: 3,
      document: {
        starting_epoch: 'Day 1',
        starting_location_id: 'location:moon_market',
        protagonist_options: [],
        starting_resources: {},
      },
      content_hash: 'sha256:scenario',
      created_at: '2026-07-20T00:00:00Z',
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST' && url.includes('/revisions')) {
        return jsonResponse({
          ok: true,
          scenario_revision: { ...scenarioRevision, revision: 2 },
        });
      }
      return jsonResponse(detail({
        scenarios: [scenario],
        scenario_revisions: { [scenario.id]: [scenarioRevision] },
      }));
    }));
    renderPanel();

    expect(await screen.findByText('First Light')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Edit Next Revision' }));
    fireEvent.change(screen.getByLabelText('Protagonist options JSON'), {
      target: { value: '[{"name":"Ward Runner","player":{"build":"ranger"}}]' },
    });
    fireEvent.change(screen.getByLabelText('Starting resources JSON'), {
      target: { value: '{"currency":25}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Publish Revision 2' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'POST')).toBe(true));
    const published = requests.find((request) => request.init?.method === 'POST');
    expect(published?.url).toContain('/api/rpg/scenarios/scenario%3Afirst-light%3Adef456/revisions');
    expect(JSON.parse(String(published?.init?.body))).toMatchObject({
      revision: 2,
      world_id: world.id,
      world_revision: 3,
      world_revision_hash: 'sha256:world',
      compatible_release: 1,
      starting_location_id: 'location:moon_market',
      protagonist_options: [{ name: 'Ward Runner', player: { build: 'ranger' } }],
      starting_resources: { currency: 25 },
    });
    expect(await screen.findByText('Published scenario revision 2.')).toBeInTheDocument();
  });
});
