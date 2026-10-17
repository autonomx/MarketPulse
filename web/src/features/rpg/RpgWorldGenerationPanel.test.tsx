import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import type { RpgWorldGenerationRun } from './api/rpgWorldLibraryClient';
import { RpgWorldGenerationPanel } from './RpgWorldGenerationPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

const section: RpgAuthoringSection = {
  id: 'points_of_interest',
  label: 'Points of Interest',
  group: 'world',
  page_kind: 'collection',
  topic_ids: ['points_of_interest'],
  entity_kind: 'point_of_interest',
  dependencies: ['locations'],
  required_before_launch: true,
  supports_generation: true,
  supports_images: true,
  supports_entity_editing: true,
  operational_status: 'empty',
  editorial_status: 'unreviewed',
  entity_count: 0,
};

const reviewRun: RpgWorldGenerationRun = {
  run_id: 'run:review',
  world_id: 'world:aurelia',
  draft_revision: 2,
  status: 'review',
  graph: {},
  context: {},
  settings: {},
  plan: {},
  progress: { percent: 100 },
  error: {},
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPanel(generation?: RpgWorldGenerationRun, profileApproved = true) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldGenerationPanel
        generation={generation}
        profileApproved={profileApproved}
        sections={[section]}
        worldId="world:aurelia"
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldGenerationPanel', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.includes('/publish')) {
        return jsonResponse({
          ok: true,
          status: 'ready',
          run: { ...reviewRun, status: 'ready' },
          publication: { world_revision: 4, world_release: 1 },
        });
      }
      return jsonResponse({
        ok: true,
        worker_started: true,
        run: { ...reviewRun, run_id: 'run:new', status: 'running', progress: { percent: 0 } },
        execution_summary: { queued_count: 1, reused_count: 0, protected_count: 0 },
        resolved_route: { provider: 'lmstudio', model: 'qwen-world-forge', source: 'explicit' },
      });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('sends every configurable generation option for selected topics', async () => {
    renderPanel();

    fireEvent.change(screen.getByLabelText('Depth'), { target: { value: 'epic' } });
    fireEvent.change(screen.getByLabelText('Starting location'), {
      target: { value: 'location:moon_market' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow optional topics/ }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Points of Interest/ }));
    fireEvent.change(screen.getByLabelText('Generation direction for Points of Interest'), {
      target: { value: 'Favor mysterious civic landmarks.' },
    });
    fireEvent.click(screen.getByText('Advanced generation settings'));
    fireEvent.change(screen.getByLabelText('World generation provider route'), {
      target: { value: 'lmstudio' },
    });
    fireEvent.change(screen.getByLabelText('World generation model'), {
      target: { value: 'qwen-world-forge' },
    });
    fireEvent.change(screen.getByLabelText('World generator version'), {
      target: { value: 'world-generator-v2' },
    });
    fireEvent.change(screen.getByLabelText('World prompt version'), {
      target: { value: 'world-prompt-v3' },
    });
    fireEvent.change(screen.getByLabelText('World generation entity manifest'), {
      target: { value: '{"existing_entity_ids":["location:moon_market"]}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Generate Selected' }));

    await waitFor(() => expect(requests.length).toBe(1));
    expect(requests[0].url).toContain('/api/rpg/worlds/world%3Aaurelia/generation');
    expect(JSON.parse(String(requests[0].init?.body))).toEqual({
      depth: 'epic',
      starting_location: 'location:moon_market',
      background_expansion: false,
      scope: { mode: 'selected', topic_ids: ['points_of_interest'] },
      strategy: 'reuse_unchanged',
      replace_locked: false,
      directives: {
        points_of_interest: { direction: 'Favor mysterious civic landmarks.' },
      },
      entity_manifest: { existing_entity_ids: ['location:moon_market'] },
      generator_version: 'world-generator-v2',
      prompt_version: 'world-prompt-v3',
      provider_route: 'lmstudio',
      model: 'qwen-world-forge',
    });
    expect(await screen.findByText(/Generation started: run:new/)).toBeInTheDocument();
    expect(screen.getByText(/1 provider topic job queued/)).toBeInTheDocument();
    expect(screen.getByText(/Route: lmstudio \/ qwen-world-forge/)).toBeInTheDocument();
  });

  it('publishes a generation run after it reaches review', async () => {
    renderPanel(reviewRun);

    const publish = screen.getByRole('button', { name: 'Publish World' });
    expect(publish).toBeEnabled();
    fireEvent.click(publish);

    await waitFor(() => expect(
      requests.some((request) => request.url.includes('/api/rpg/world-generation/run%3Areview/publish')),
    ).toBe(true));
    expect(await screen.findByText('Published world revision 4, release 1.')).toBeInTheDocument();
  });

  it('locks generation actions until the profile is approved', () => {
    renderPanel(undefined, false);

    expect(screen.getByRole('button', { name: 'Generate World' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate Selected' })).toBeDisabled();
    expect(screen.getByText(/locked until the profile preview above is approved/i)).toBeInTheDocument();
  });
});
