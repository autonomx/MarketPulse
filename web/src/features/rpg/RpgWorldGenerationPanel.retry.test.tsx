import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgWorldGenerationRun } from './api/rpgWorldLibraryClient';
import { RpgWorldGenerationPanel } from './RpgWorldGenerationPanel';
import { createTestQueryClient } from '../../test/renderWithProviders';

function run(status: string, failedTopicIds: string[] = []): RpgWorldGenerationRun {
  return {
    run_id: `run:${status}`,
    world_id: 'world:aurelia',
    draft_revision: 2,
    status,
    graph: {},
    context: {},
    settings: { provider_route: 'lmstudio', model: 'qwen-world' },
    plan: {},
    progress: {
      percent: status === 'running' ? 27 : 40,
      failed_topic_ids: failedTopicIds,
    },
    error: {},
    created_at: '2026-07-20T00:00:00Z',
    updated_at: '2026-07-20T00:00:00Z',
  };
}

function renderPanel(generation: RpgWorldGenerationRun, profileApproved = true) {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldGenerationPanel
        generation={generation}
        profileApproved={profileApproved}
        sections={[]}
        worldId="world:aurelia"
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldGenerationPanel failed-topic retry guards', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return new Response(JSON.stringify({
        ok: true,
        worker_started: true,
        run: run('running'),
        retry_of_run_id: 'run:failed',
        diagnostic_id: 'world-generation-retry-test',
        diagnostic_log: 'resources\\logs\\rpg\\world-generation-2026-07-21.jsonl',
        execution_summary: { queued_count: 1, reused_count: 2, protected_count: 0 },
        resolved_route: { provider: 'lmstudio', model: 'qwen-world', source: 'retry_durable_run' },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('blocks duplicate generation scopes while the current run is active', () => {
    renderPanel(run('running'));

    expect(screen.getByRole('button', { name: 'Generate World' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Regenerate Stale' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retry Failed' })).toBeDisabled();
    expect(screen.getByText(/Failed-topic and Game Master lore retry become available/)).toBeInTheDocument();
  });

  it('retries the exact failed run instead of rebuilding a failed scope from UI defaults', async () => {
    renderPanel(run('failed', ['points_of_interest']));

    const retry = screen.getByRole('button', { name: 'Retry Failed (1)' });
    expect(retry).toBeEnabled();
    fireEvent.click(retry);

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe('/api/rpg/world-generation/run%3Afailed/retry-failed');
    expect(JSON.parse(String(requests[0].init?.body))).toEqual({});
    expect(await screen.findByText(/Retry started from run:failed/)).toBeInTheDocument();
    expect(screen.getByText(/1 provider topic job queued/)).toBeInTheDocument();
    expect(screen.getByText(/Route: lmstudio \/ qwen-world/)).toBeInTheDocument();
    expect(screen.getByText(/world-generation-2026-07-21\.jsonl/)).toBeInTheDocument();
  });

  it('continues a failed run from remaining topics with its durable settings', async () => {
    renderPanel(run('failed', ['points_of_interest']));

    fireEvent.click(screen.getByRole('button', { name: 'Continue Generation' }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe('/api/rpg/world-generation/run%3Afailed/continue');
    expect(JSON.parse(String(requests[0].init?.body))).toEqual({});
    expect(await screen.findByText(/Continuation started from run:failed/)).toBeInTheDocument();
  });

  it('continues a partial review run instead of disabling recovery', async () => {
    const partialReview = {
      ...run('review'),
      graph: { nodes: [{ topic_id: 'realm' }, { topic_id: 'places' }] },
      plan: { topic_ids: ['realm'] },
    };
    renderPanel(partialReview);

    const continueButton = screen.getByRole('button', { name: 'Continue Generation' });
    expect(continueButton).toBeEnabled();
    fireEvent.click(continueButton);

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].url).toBe('/api/rpg/world-generation/run%3Areview/continue');
  });

  it('explains that the compact log omits generated content', () => {
    renderPanel(run('failed', ['points_of_interest']));

    expect(screen.getByText(/Prompts, completions, and generated world content are omitted/)).toBeInTheDocument();
  });

  it('keeps the failed run resumable and explains a retryable database outage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: {
        ok: false,
        error: 'world_generation_database_unavailable',
        message: 'World generation could not reach PostgreSQL.',
        retryable: true,
      },
    }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })));
    renderPanel(run('failed', ['points_of_interest']));

    fireEvent.click(screen.getByRole('button', { name: 'Retry Failed (1)' }));

    expect(await screen.findByText(/cannot reach PostgreSQL/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry Failed (1)' })).toBeEnabled());
  });

  it('explains a rejected database credential without claiming PostgreSQL is down', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      detail: {
        ok: false,
        error: 'world_generation_database_authentication_failed',
        message: 'PostgreSQL is reachable, but Omnix’s database credential was rejected.',
        retryable: true,
      },
    }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    })));
    renderPanel(run('failed', ['points_of_interest']));

    fireEvent.click(screen.getByRole('button', { name: 'Retry Failed (1)' }));

    expect(await screen.findByText(/database credential was rejected/)).toBeInTheDocument();
  });

  it('keeps retry and continuation locked when the current profile is unapproved', () => {
    renderPanel(run('failed', ['points_of_interest']), false);

    expect(screen.getByRole('button', { name: 'Retry Failed (1)' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Continue Generation' })).toBeDisabled();
  });
});
