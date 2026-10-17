import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixTheme } from '../../design/theme';
import { RpgCreateCampaignWizard } from './RpgCreateCampaignWizard';
import { createTestQueryClient } from '../../test/renderWithProviders';

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string'
    ? new URL(input, 'http://localhost').pathname
    : new URL(input.toString(), 'http://localhost').pathname;
}

function renderWizard() {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
        <RpgCreateCampaignWizard />
      </MantineProvider>
    </QueryClientProvider>,
  );
}

const world = {
  id: 'world:aurelia',
  title: 'Aurelia: Echoes Beyond the Gate',
  description: 'A reusable fantasy isekai world.',
  status: 'published',
  source_mode: 'imported',
  genre: 'fantasy_isekai',
  tone: 'heroic wonder',
  seed: 482193,
  draft_revision: 1,
  metadata: { cover_image_url: '/api/assets/aurelia-cover/content' },
  scenario_count: 1,
  generation: null,
  created_at: '2026-07-17T00:00:00Z',
  updated_at: '2026-07-17T00:00:00Z',
};

const scenario = {
  id: 'scenario:aurelia:arrival',
  world_id: world.id,
  title: 'Beyond the Broken Gate',
  description: 'Arrive in Starfall Grove.',
  status: 'published',
  metadata: {},
  created_at: '2026-07-17T00:00:00Z',
  updated_at: '2026-07-17T00:00:00Z',
};

const scenarioRevision = {
  revision: 1,
  world_id: world.id,
  world_revision: 1,
  document: {
    compatible_release: 1,
    starting_location_id: 'location:aurelia:starfall-grove',
  },
  content_hash: 'sha256:scenario',
  created_at: '2026-07-17T00:00:00Z',
};

const release = {
  world_revision: 1,
  release: 1,
  document: {
    certification: {
      launch_ready: true,
      missing_requirements: [],
    },
  },
  release_hash: 'sha256:release',
  created_at: '2026-07-17T00:00:00Z',
};

function libraryPayload() {
  return {
    ok: true,
    worlds: [world],
    scenarios: [scenario],
    campaigns: [],
    generation_runs: [],
  };
}

function detailPayload() {
  return {
    ok: true,
    world,
    topics: [],
    map_blueprints: [],
    revisions: [],
    releases: [release],
    scenarios: [scenario],
    scenario_revisions: { [scenario.id]: [scenarioRevision] },
    generation_runs: [],
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe('RpgCreateCampaignWizard', () => {
  it('opens on the reusable-world card catalog and withholds campaign setup when no world exists', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (requestPath(input) === '/api/rpg/world-library') {
        return Response.json({
          ok: true,
          worlds: [],
          scenarios: [],
          campaigns: [],
          generation_runs: [],
        });
      }
      return new Response('not found', { status: 404 });
    }));
    renderWizard();

    expect(screen.queryByText('World Forge depth')).not.toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Choose a World' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Search worlds' })).toBeInTheDocument();
    expect(await screen.findByText(/Create or import one from Worlds & Campaigns/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Create Campaign' })).not.toBeInTheDocument();
  });

  it('selects a world card before opening setup and launches its published scenario', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/world-library') return Response.json(libraryPayload());
      if (path === `/api/rpg/worlds/${encodeURIComponent(world.id)}/library`) {
        return Response.json(detailPayload());
      }
      if (
        path === `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`
        && init?.method === 'POST'
      ) {
        return Response.json({
          ok: true,
          status: 'ready',
          session_id: 'campaign:aurelia:one',
          world_forge_invoked: false,
        });
      }
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);
    renderWizard();

    expect(await screen.findByRole('heading', { name: 'Choose a World' })).toBeInTheDocument();
    expect((await screen.findAllByText(world.title)).length).toBeGreaterThan(0);
    expect(screen.getByText('Fantasy Isekai')).toBeInTheDocument();
    expect(screen.getByText('1 published opening')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Create Campaign' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: `New campaign in ${world.title}` }));

    expect(await screen.findByRole('heading', { name: 'Create Campaign' })).toBeInTheDocument();
    expect(screen.getByLabelText('Selected campaign world')).toHaveTextContent(world.title);
    expect(screen.getByRole('button', { name: 'Change world' })).toBeInTheDocument();
    const scenarioSelect = screen.getByRole('combobox', { name: 'Published scenario' });
    await waitFor(() => expect(scenarioSelect).toHaveValue(scenario.id));
    expect(await screen.findByText(`Ready: ${world.title} · ${scenario.title}`)).toBeInTheDocument();
    expect(screen.getByLabelText('Campaign setup summary')).toHaveTextContent(world.title);
    expect(screen.getByLabelText('Campaign setup summary')).toHaveTextContent('Starfall Grove');
    expect(screen.getByLabelText('Campaign setup summary')).toHaveTextContent(scenario.title);
    expect(screen.getByLabelText('Campaign setup summary')).not.toHaveTextContent('Rusty Flagon Tavern');

    fireEvent.click(screen.getByRole('button', { name: 'Create Campaign' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`,
      expect.objectContaining({ method: 'POST' }),
    ));
    const launchCall = fetchMock.mock.calls.find(([input]) => (
      requestPath(input) === `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`
    ));
    const launchBody = JSON.parse(String(launchCall?.[1]?.body)) as {
      gameplay: { genre: string; tone: string };
    };
    expect(launchBody.gameplay).toMatchObject({ genre: world.genre, tone: world.tone });
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input) === '/api/rpg/new-game')).toBe(false);
    expect(await screen.findByRole('dialog', { name: 'Campaign Ready' })).toBeInTheDocument();
    expect(screen.getByText('Session campaign:aurelia:one')).toBeInTheDocument();
  });

  it('explains the publishing requirement when a world only has a draft opening', async () => {
    const draftScenario = { ...scenario, status: 'draft' };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/api/rpg/world-library') {
        return Response.json({
          ...libraryPayload(),
          worlds: [{ ...world, scenario_count: 1 }],
          scenarios: [draftScenario],
        });
      }
      if (path === `/api/rpg/worlds/${encodeURIComponent(world.id)}/library`) {
        return Response.json({
          ...detailPayload(),
          scenarios: [draftScenario],
          scenario_revisions: { [scenario.id]: [] },
        });
      }
      return new Response('not found', { status: 404 });
    }));
    renderWizard();

    expect(await screen.findByText('0 published openings')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: `New campaign in ${world.title}` }));

    expect(await screen.findByText('This world has no published scenario. Publish an opening before starting a campaign.')).toBeInTheDocument();
    expect(screen.getByLabelText('Selected campaign world')).toHaveTextContent(world.title);
    expect(screen.queryByLabelText('Campaign setup summary')).not.toBeInTheDocument();
    expect(screen.queryByText('Rusty Flagon Tavern')).not.toBeInTheDocument();
  });
});
