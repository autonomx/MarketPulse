import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { omnixModules } from '../../app/modules';
import { omnixTheme } from '../../design/theme';
import { RpgWorkspace } from './RpgWorkspace';
import { createTestQueryClient } from '../../test/renderWithProviders';

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string'
    ? new URL(input, 'http://localhost').pathname
    : new URL(input.toString(), 'http://localhost').pathname;
}

function renderRpg() {
  const queryClient = createTestQueryClient();
  const module = omnixModules.find((entry) => entry.id === 'rpg');
  if (!module) throw new Error('RPG module is missing');

  return render(
    <MantineProvider theme={omnixTheme} defaultColorScheme="dark">
      <QueryClientProvider client={queryClient}>
        <RpgWorkspace module={module} />
      </QueryClientProvider>
    </MantineProvider>,
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
  metadata: {},
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
  document: { compatible_release: 1, starting_location_id: 'location:aurelia:starfall-grove' },
  content_hash: 'sha256:scenario',
  created_at: '2026-07-17T00:00:00Z',
};

const release = {
  world_revision: 1,
  release: 1,
  document: { certification: { launch_ready: true, missing_requirements: [] } },
  release_hash: 'sha256:release',
  created_at: '2026-07-17T00:00:00Z',
};

function worldLibraryPayload() {
  return {
    ok: true,
    worlds: [world],
    scenarios: [scenario],
    campaigns: [],
    generation_runs: [],
  };
}

function worldDetailPayload() {
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

function previousSessionSummary() {
  return {
    session_id: 'rpg-previous-1',
    title: 'Previous Campaign',
    location: 'Old Road',
    summary: 'The previous campaign is still selected.',
    turn_count: 4,
    updated_at: '2026-06-19T00:00:00Z',
    timeline: [{ title: 'Old conversation', detail: 'Bran speaks from the previous campaign.', turn: 4 }],
  };
}

function createdSessionSummary() {
  return {
    session_id: 'rpg-created-1',
    title: 'Created Campaign',
    location: 'Starfall Grove',
    summary: 'A new campaign is ready beyond the broken gate.',
    turn_count: 0,
    updated_at: '2026-07-17T00:00:00Z',
  };
}

function createdSessionState() {
  return {
    manifest: { session_id: 'rpg-created-1', title: 'Created Campaign' },
    state: {
      ability_tree: { abilities: [{ ability_id: 'recon_aimed_shot', icon: '✦', name: 'Aimed Shot' }] },
      encounter: { status: 'inactive', title: 'No active combat', summary: 'All quiet for now.' },
      environment_snapshot: {
        display: { day_time: 'Day 1', weather: 'Clear' },
        context: { location_label: 'Starfall Grove' },
        region_id: 'aurelia_starfall',
      },
      hotbar: { 1: 'recon_aimed_shot' },
      party: [],
      player: {
        name: 'Elara',
        level: 1,
        class: 'Echo Wanderer',
        background: 'Wanderer',
        currency: { gold: 0 },
        equipment: [{ name: 'Travel cloak', slot: 'clothing' }],
        inventory: [{ name: 'Trail rations', quantity: 3 }],
        renown: 'Unknown (0)',
        resources: {
          hp: { current: 92, max: 92 },
          stamina: { current: 91, max: 91 },
          mana: { current: 30, max: 30 },
        },
        xp: { current: 0, max: 100 },
      },
      quests: [{ title: 'Echoes Beyond the Gate', status: 'active', objective: 'Reach Starfall Village.' }],
      quick_actions: ['Survey the grove'],
      relationships: [],
      timeline: [{ title: 'Campaign begins', detail: 'Elara awakens beneath the broken gate.', turn: 0 }],
    },
  };
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe('RpgWorkspace campaign handoff', () => {
  it('stores the published-world campaign selection before inventory catches up', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') return Response.json({ sessions: [previousSessionSummary()], diagnostics: [] });
      if (path === '/api/rpg/sessions/rpg-previous-1') {
        return Response.json({ ok: true, session_id: 'rpg-previous-1', session: previousSessionSummary() });
      }
      if (path === '/api/rpg/world-library') return Response.json(worldLibraryPayload());
      if (path === `/api/rpg/worlds/${encodeURIComponent(world.id)}/library`) return Response.json(worldDetailPayload());
      if (
        path === `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`
        && init?.method === 'POST'
      ) {
        return Response.json({ ok: true, status: 'ready', session_id: 'rpg-created-lagging', world_forge_invoked: false });
      }
      if (path === '/api/jobs') return Response.json({ jobs: [] });
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/reports') return Response.json({ reports: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    renderRpg();
    fireEvent.click(await screen.findByRole('button', { name: 'Campaign Menu' }));
    fireEvent.click(screen.getByRole('button', { name: /^New Campaign/ }));
    fireEvent.click(await screen.findByRole('button', { name: `New campaign in ${world.title}` }));
    expect(await screen.findByText(`Ready: ${world.title} · ${scenario.title}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create Campaign' }));

    expect(await screen.findByRole('dialog', { name: 'Campaign Ready' })).toBeInTheDocument();
    expect(window.localStorage.getItem('omnix:rpg:selected-session-id')).toBe('rpg-created-lagging');
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input) === '/api/rpg/new-game')).toBe(false);
    const launchCall = fetchMock.mock.calls.find(([input]) => (
      requestPath(input) === `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`
    ));
    expect(String(launchCall?.[1]?.body)).toContain(`"world_id":"${world.id}"`);
    expect(String(launchCall?.[1]?.body)).toContain('"world_release":1');
  });

  it('restores the launched campaign after entry reload and queues its first turn', async () => {
    let launched = false;
    let turnApplied = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/api/rpg/sessions') {
        return Response.json({ sessions: launched ? [createdSessionSummary()] : [previousSessionSummary()], diagnostics: [] });
      }
      if (path === '/api/rpg/sessions/rpg-previous-1') {
        return Response.json({ ok: true, session_id: 'rpg-previous-1', session: previousSessionSummary() });
      }
      if (path === '/api/rpg/sessions/rpg-created-1') {
        return Response.json({ ok: true, session_id: 'rpg-created-1', session: createdSessionState() });
      }
      if (path === '/api/rpg/world-library') return Response.json(worldLibraryPayload());
      if (path === `/api/rpg/worlds/${encodeURIComponent(world.id)}/library`) return Response.json(worldDetailPayload());
      if (
        path === `/api/rpg/scenarios/${encodeURIComponent(scenario.id)}/revisions/1/launch`
        && init?.method === 'POST'
      ) {
        launched = true;
        return Response.json({ ok: true, status: 'ready', session_id: 'rpg-created-1', world_forge_invoked: false });
      }
      if (path === '/api/rpg/sessions/rpg-created-1/turn' && init?.method === 'POST') {
        turnApplied = true;
        return Response.json({
          ok: true,
          contract_version: 'rpg_turn_response_v2',
          session_id: 'rpg-created-1',
          job_id: 'job:fg-1',
          command: 'I survey the broken gate.',
          response: 'Silver motes gather around the fractured arch.',
          content: 'Silver motes gather around the fractured arch.',
        });
      }
      // The record-only job the gateway completes in the turn's transaction.
      const foregroundJob = {
        id: 'job:fg-1',
        module: 'rpg',
        type: 'rpg.turn.foreground_record',
        status: 'completed',
        resource_class: 'gpu:llm',
        priority: 0,
        stages: [],
        input_ref: { session_id: 'rpg-created-1' },
        input_payload: { command: 'I survey the broken gate.', submission_id: 'submit:1' },
        output_refs: [{
          type: 'rpg_turn_response',
          module: 'rpg',
          session_id: 'rpg-created-1',
          turn_response: { response: 'Silver motes gather around the fractured arch.' },
        }],
        created_at: '2026-07-17T00:00:01Z',
        updated_at: '2026-07-17T00:00:04Z',
        completed_at: '2026-07-17T00:00:04Z',
      };
      if (path === '/api/jobs/job%3Afg-1' || path === '/api/jobs/job:fg-1') return Response.json(foregroundJob);
      if (path === '/api/jobs') {
        return Response.json({ jobs: turnApplied ? [foregroundJob] : [] });
      }
      if (path === '/api/assets') return Response.json({ assets: [] });
      if (path === '/api/reports') return Response.json({ reports: [] });
      return new Response('not found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    const firstView = renderRpg();
    fireEvent.click(await screen.findByRole('button', { name: 'Campaign Menu' }));
    fireEvent.click(screen.getByRole('button', { name: /^New Campaign/ }));
    fireEvent.click(await screen.findByRole('button', { name: `New campaign in ${world.title}` }));
    expect(await screen.findByText(`Ready: ${world.title} · ${scenario.title}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create Campaign' }));
    expect(await screen.findByRole('dialog', { name: 'Campaign Ready' })).toBeInTheDocument();
    expect(window.localStorage.getItem('omnix:rpg:selected-session-id')).toBe('rpg-created-1');
    firstView.unmount();

    renderRpg();
    expect(await screen.findByRole('option', { name: 'Created Campaign — rpg-created-1' })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Elara' })).toBeInTheDocument();
    const playerRail = screen.getByRole('complementary', { name: 'Player, party, and quests' });
    expect(within(playerRail).getByText('Travel cloak')).toBeInTheDocument();
    expect(within(playerRail).getByText('Echoes Beyond the Gate')).toBeInTheDocument();
    expect(screen.getAllByText('Elara awakens beneath the broken gate.').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'I survey the broken gate.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Queue RPG turn' }));
    await waitFor(() => {
      const turnCall = fetchMock.mock.calls.find(([input, init]) => (
        requestPath(input) === '/api/rpg/sessions/rpg-created-1/turn' && init?.method === 'POST'
      ));
      expect(String(turnCall?.[1]?.body)).toContain('"command":"I survey the broken gate."');
    });
    const storyScene = screen.getByRole('region', { name: /Starfall Grove/ });
    expect(within(storyScene).getByText('I survey the broken gate.')).toBeInTheDocument();
    expect(within(storyScene).getByText('Silver motes gather around the fractured arch.')).toBeInTheDocument();
  });
});
