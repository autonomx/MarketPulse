import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldVisualMapPanel } from './RpgWorldVisualMapPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

const worldId = 'world:aurelia';

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const detail = {
  ok: true,
  world: { id: worldId, title: 'Aurelia', description: '', status: 'draft', source_mode: 'hybrid', genre: 'fantasy', tone: 'heroic', seed: 1, draft_revision: 1, metadata: {}, created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z' },
  topics: [{ topic_id: 'locations', draft_revision: 1, source: 'ai', status: 'ready', content: { entities: [{ id: 'location:moon_market', name: 'Moon Market', kind: 'location' }] }, directives: {}, dependency_hashes: {}, input_hash: '', content_hash: '', provenance: {}, updated_at: '2026-07-20T00:00:00Z' }],
  map_blueprints: [], revisions: [], releases: [], scenarios: [], scenario_revisions: {}, generation_runs: [],
};

const imageTargets = {
  ok: true,
  world: detail.world,
  targets: [
    { world_id: worldId, target_id: 'world:map', target_type: 'map', entity_id: worldId, role: 'map', source_content_hash: 'sha256:map', status: 'ready', review_state: 'approved', suggested_prompt: 'Aurelia map', active_asset_id: 'image:aurelia-map', latest_job_id: 'job:map', metadata: {}, attempts: [], created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z' },
    { world_id: worldId, target_id: 'entity:location:moon_market:scene', target_type: 'location', entity_id: 'location:moon_market', role: 'scene', source_content_hash: 'sha256:market', status: 'ready', review_state: 'approved', suggested_prompt: 'Moon Market', active_asset_id: 'image:moon-market', latest_job_id: 'job:market', metadata: { topic_id: 'locations' }, attempts: [], created_at: '2026-07-20T00:00:00Z', updated_at: '2026-07-20T00:00:00Z' },
  ],
};

describe('RpgWorldVisualMapPanel', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes('/image-targets/world%3Amap/regenerate')) return response({ ok: true, world_id: worldId, jobs: [{ job_id: 'job:replacement' }] });
      if (url.includes('/map-blueprints/materialize')) return response({ ok: true, created: [], created_count: 1 });
      if (url.includes('/image-targets')) return response(imageTargets);
      return response(detail);
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('renders approved map artwork and regenerates it from the Map page', async () => {
    const client = createTestQueryClient();
    const { container } = render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    const regenerate = await screen.findByRole('button', { name: 'Regenerate Map Artwork' });
    expect(container.querySelector('.rpg-atlas-world')).toHaveStyle({ backgroundImage: expect.stringContaining('image%3Aaurelia-map') });
    expect(screen.getByRole('button', { name: 'Open Moon Market' })).toHaveClass('has-artwork');
    expect(screen.getByRole('button', { name: 'Enter full screen map' })).toBeInTheDocument();
    expect(screen.getByText('All area artwork ready (1)')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Area artwork generation progress' })).toHaveAttribute('aria-valuenow', '1');
    expect(screen.getByText('1 / 1 ready')).toBeInTheDocument();
    fireEvent.click(regenerate);

    await waitFor(() => expect(requests.some((request) => request.url.includes('/image-targets/world%3Amap/regenerate'))).toBe(true));
    const request = requests.find((entry) => entry.url.includes('/image-targets/world%3Amap/regenerate'));
    expect(JSON.parse(String(request?.init?.body))).toMatchObject({ width: 1024, height: 768, style: 'illustrated regional map', no_cache: true });
  });

  it('zooms the atlas and opens location information from an icon', async () => {
    const client = createTestQueryClient();
    render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    const marker = await screen.findByRole('button', { name: 'Open Moon Market' });
    fireEvent.click(marker);
    fireEvent.wheel(screen.getByRole('application'), { deltaY: -200 });

    expect(screen.getByRole('heading', { name: 'Moon Market' })).toBeInTheDocument();
    expect(screen.getByText(/generated semantic baseline/i)).toBeInTheDocument();
    expect(screen.getByText(/130%/)).toBeInTheDocument();
  });

  it('maps generated place and region topics used by current world canon', async () => {
    const currentCanonDetail = {
      ...detail,
      topics: [
        {
          ...detail.topics[0],
          topic_id: 'places',
          content: {
            entities: [{
              id: 'ent:places:001',
              entity_id: 'ent:places:001',
              kind: 'place',
              name: 'OmniCorp Spire',
              short_summary: 'A corporate citadel rising above the polluted city.',
            }],
          },
        },
        {
          ...detail.topics[0],
          topic_id: 'regions',
          content: {
            entities: [{
              id: 'ent:region:001',
              entity_id: 'ent:region:001',
              kind: 'region',
              name: 'Neo-Kyoto Arcology',
            }],
          },
        },
      ],
    };
    const currentCanonTargets = {
      ...imageTargets,
      targets: [
        imageTargets.targets[0],
        {
          ...imageTargets.targets[1],
          target_id: 'entity:ent:places:001:map',
          target_type: 'map',
          entity_id: 'ent:places:001',
          role: 'map',
          active_asset_id: 'image:spire-map',
          metadata: { topic_id: 'map', map_level: 'location' },
        },
        {
          ...imageTargets.targets[1],
          target_id: 'entity:ent:region:001:map',
          target_type: 'map',
          entity_id: 'ent:region:001',
          role: 'map',
          active_asset_id: 'image:arcology-map',
          metadata: { topic_id: 'map', map_level: 'location' },
        },
      ],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/image-targets')) return response(currentCanonTargets);
      return response(currentCanonDetail);
    }));
    const client = createTestQueryClient();
    render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    expect(await screen.findByRole('button', { name: 'Open OmniCorp Spire' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open Neo-Kyoto Arcology' })).toBeInTheDocument();
    expect(screen.getByText('Detailed maps ready (2)')).toBeInTheDocument();
    expect(screen.queryByText('No mapped areas yet')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open OmniCorp Spire' }));
    expect(screen.getByText('A corporate citadel rising above the polluted city.')).toBeInTheDocument();
  });

  it('switches to a selected location map at deep zoom', async () => {
    const targetsWithDetailMap = {
      ...imageTargets,
      targets: [
        ...imageTargets.targets,
        {
          world_id: worldId,
          target_id: 'entity:location:moon_market:map',
          target_type: 'map',
          entity_id: 'location:moon_market',
          role: 'map',
          source_content_hash: 'sha256:moon-market-map',
          status: 'ready',
          review_state: 'approved',
          suggested_prompt: 'Moon Market detail map',
          active_asset_id: 'image:moon-market-map',
          latest_job_id: 'job:moon-market-map',
          metadata: { topic_id: 'map', map_level: 'location' },
          attempts: [],
          created_at: '2026-07-20T00:00:00Z',
          updated_at: '2026-07-20T00:00:00Z',
        },
      ],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/image-targets')) return response(targetsWithDetailMap);
      return response(detail);
    }));
    const client = createTestQueryClient();
    const { container } = render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    expect(await screen.findByRole('button', { name: 'Regenerate All Detailed Maps (1)' })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: 'Open Moon Market' }));
    fireEvent.click(screen.getByRole('button', { name: 'Enter Moon Market Map' }));

    const atlas = container.querySelector('.rpg-atlas-world');
    expect(atlas).toHaveAttribute('data-map-level', 'location');
    expect(atlas).toHaveStyle({ backgroundImage: expect.stringContaining('image%3Amoon-market-map') });
    expect(screen.getByText('Local detail map')).toBeInTheDocument();
  });

  it('shows the authored canonical description when the topic row is lightweight', async () => {
    const detailWithCanonicalDescription = {
      ...detail,
      revisions: [{
        revision: 1,
        document: {
          canon: {
            entities: {
              'location:moon_market': {
                id: 'location:moon_market',
                kind: 'location',
                description: 'Lantern-lit stalls crowd a market that opens only beneath a new moon.',
              },
            },
          },
        },
        content_hash: 'sha256:revision',
        created_at: '2026-07-20T00:00:00Z',
      }],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/image-targets')) return response(imageTargets);
      return response(detailWithCanonicalDescription);
    }));
    const client = createTestQueryClient();
    render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    fireEvent.click(await screen.findByRole('button', { name: 'Open Moon Market' }));

    expect(screen.getByText('Lantern-lit stalls crowd a market that opens only beneath a new moon.')).toBeInTheDocument();
    expect(screen.queryByText(/generated semantic baseline/i)).not.toBeInTheDocument();
  });

  it('keeps the last map artwork visible while an updated map is pending review', async () => {
    const staleTargets = {
      ...imageTargets,
      targets: imageTargets.targets.map((target) => target.target_id === 'world:map'
        ? { ...target, status: 'stale', review_state: 'pending' }
        : target),
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/image-targets')) return response(staleTargets);
      return response(detail);
    }));
    const client = createTestQueryClient();
    const { container } = render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    await screen.findByRole('button', { name: 'Regenerate Map Artwork' });
    expect(container.querySelector('.rpg-atlas-world')).toHaveStyle({ backgroundImage: expect.stringContaining('image%3Aaurelia-map') });
  });

  it('offers one bulk action for all generated areas missing blueprints', async () => {
    const client = createTestQueryClient();
    render(<QueryClientProvider client={client}><RpgWorldVisualMapPanel worldId={worldId} /></QueryClientProvider>);

    fireEvent.click(await screen.findByRole('button', { name: 'Generate Area Blueprints (1)' }));

    await waitFor(() => expect(requests.some((request) => request.url.includes('/map-blueprints/materialize'))).toBe(true));
  });
});
