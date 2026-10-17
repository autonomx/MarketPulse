import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RpgWorldCampaignSetup } from './RpgWorldCampaignSetup';
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
  description: 'A living fantasy realm.',
  status: 'published',
  source_mode: 'hybrid',
  genre: 'classic_fantasy',
  tone: 'heroic adventure',
  seed: 7,
  draft_revision: 2,
  metadata: { campaign_template: 'classic_fantasy' },
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

const scenario = {
  id: 'scenario:first-light:def456',
  world_id: world.id,
  title: 'First Light',
  description: 'Begin at the moon market.',
  status: 'published',
  metadata: {},
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

const detail = {
  ok: true,
  world,
  topics: [],
  map_blueprints: [],
  revisions: [],
  releases: [{
    world_revision: 3,
    release: 1,
    document: { certification: { launch_ready: true, missing_requirements: [] } },
    release_hash: 'sha256:release',
    created_at: '2026-07-20T00:00:00Z',
  }],
  scenarios: [scenario],
  scenario_revisions: {
    [scenario.id]: [{
      revision: 2,
      world_id: world.id,
      world_revision: 3,
      document: {
        compatible_release: 1,
        starting_location_id: 'location:moon_market',
        protagonist_options: [{
          name: 'Ward Runner',
          player: { background: 'Ward Courier', build: 'ranger' },
        }],
      },
      content_hash: 'sha256:scenario',
      created_at: '2026-07-20T00:00:00Z',
    }],
  },
  generation_runs: [],
};

function renderSetup(onSessionLaunched = vi.fn(), onEditWorld = vi.fn()) {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldCampaignSetup
        onBack={vi.fn()}
        onEditWorld={onEditWorld}
        onSessionLaunched={onSessionLaunched}
        worldId={world.id}
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldCampaignSetup', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (init?.method === 'POST' && url.includes('/launch')) {
        return jsonResponse({ ok: true, status: 'ready', session_id: 'campaign:new' });
      }
      if (url.includes('/library') && url.includes('/worlds/')) return jsonResponse(detail);
      return jsonResponse({
        ok: true,
        worlds: [world],
        scenarios: [scenario],
        campaigns: [],
        generation_runs: [],
      });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('launches with configured protagonist gameplay features and final review', async () => {
    const onSessionLaunched = vi.fn();
    renderSetup(onSessionLaunched);

    expect(await screen.findByRole('heading', { name: 'Aurelia' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Character template'), { target: { value: '0' } });
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Mira' } });
    fireEvent.change(screen.getByLabelText('Difficulty'), { target: { value: 'harsh' } });
    fireEvent.change(screen.getByLabelText('Economy pressure'), { target: { value: 'strict' } });
    fireEvent.click(screen.getByLabelText('Permadeath'));
    fireEvent.click(screen.getByLabelText('Tts'));

    expect(screen.getByRole('region', { name: 'Campaign launch review' })).toHaveTextContent('Mira');
    fireEvent.click(screen.getByRole('button', { name: 'Launch Campaign' }));

    await waitFor(() => expect(onSessionLaunched).toHaveBeenCalledWith('campaign:new'));
    const launch = requests.find((request) => request.init?.method === 'POST' && request.url.includes('/launch'));
    expect(launch).toBeDefined();
    expect(JSON.parse(String(launch?.init?.body))).toMatchObject({
      world_id: world.id,
      world_revision: 3,
      world_release: 1,
      player: {
        name: 'Mira',
        background: 'Ward Courier',
        build: 'ranger',
      },
      gameplay: {
        difficulty: 'harsh',
        economy_pressure: 'strict',
        permadeath: true,
        companions_enabled: true,
      },
      features: {
        tts: true,
        validator: true,
        llm_narration: true,
      },
    });
  });

  it('routes incomplete worlds back to authoring', async () => {
    const onEditWorld = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/library') && url.includes('/worlds/')) {
        return jsonResponse({ ...detail, scenarios: [], scenario_revisions: {} });
      }
      return jsonResponse({ ok: true, worlds: [world], scenarios: [], campaigns: [], generation_runs: [] });
    }));
    renderSetup(vi.fn(), onEditWorld);

    expect(await screen.findByText('No published opening')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review World Setup' }));
    expect(onEditWorld).toHaveBeenCalledTimes(1);
  });

  it('shows live World Forge progress while imported openings are prepared', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/library') && url.includes('/worlds/')) {
        return jsonResponse({
          ...detail,
          topics: [{ topic_id: 'opening_scenarios', content: { entities: [{}] } }],
          scenarios: [],
          scenario_revisions: {},
          generation_runs: [{
            run_id: 'run:import', world_id: world.id, draft_revision: 1, status: 'running',
            graph: {}, context: {}, settings: {}, plan: { topic_ids: ['places', 'actors', 'opening_scenarios'] },
            progress: { percent: 67, accepted_topic_ids: ['places', 'actors'], active_topic_ids: ['opening_scenarios'] },
            error: {}, created_at: '', updated_at: '',
          }],
        });
      }
      return jsonResponse({ ok: true, worlds: [world], scenarios: [], campaigns: [], generation_runs: [] });
    }));
    renderSetup();

    expect(await screen.findByRole('region', { name: 'World Forge progress' })).toHaveTextContent('World Forge: 67%');
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '67');
    expect(screen.getByText('2 accepted, 0 awaiting review, 0 failed or blocked. Generating opening_scenarios')).toBeInTheDocument();
  });

  it('explains review status and opens World Forge results instead of preparing scenarios', async () => {
    const onEditWorld = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/library') && url.includes('/worlds/')) {
        return jsonResponse({
          ...detail,
          topics: [{ topic_id: 'opening_scenarios', content: { entities: [{}] } }],
          scenarios: [],
          scenario_revisions: {},
          generation_runs: [{
            run_id: 'run:review', world_id: world.id, draft_revision: 1, status: 'review',
            graph: {}, context: {}, settings: {}, plan: { topic_ids: ['places', 'actors'] },
            progress: { percent: 100, accepted_topic_ids: [], flagged_topic_ids: ['places', 'actors'] },
            error: {}, created_at: '', updated_at: '',
          }],
        });
      }
      return jsonResponse({ ok: true, worlds: [world], scenarios: [], campaigns: [], generation_runs: [] });
    }));
    renderSetup(vi.fn(), onEditWorld);

    expect(await screen.findByText(/2 topics need review before this world can be published/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Review World Forge Results' }));
    expect(onEditWorld).toHaveBeenCalledTimes(1);
  });
});
