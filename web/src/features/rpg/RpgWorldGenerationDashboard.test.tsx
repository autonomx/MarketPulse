import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import type { RpgWorldGenerationRun } from './api/rpgWorldLibraryClient';
import { RpgWorldGenerationDashboard } from './RpgWorldGenerationDashboard';
import { createTestQueryClient } from '../../test/renderWithProviders';

const section: RpgAuthoringSection = {
  id: 'regions',
  label: 'Regions',
  group: 'world',
  page_kind: 'collection',
  topic_ids: ['regions'],
  entity_kind: 'region',
  dependencies: [],
  required_before_launch: true,
  supports_generation: true,
  supports_images: true,
  supports_entity_editing: true,
  operational_status: 'complete',
  editorial_status: 'unreviewed',
  entity_count: 4,
};

const previousRun: RpgWorldGenerationRun = {
  run_id: 'run:previous',
  world_id: 'world:aurelia',
  draft_revision: 2,
  status: 'review',
  graph: {},
  context: {},
  settings: { provider_route: 'lmstudio', model: 'qwen-world' },
  plan: {},
  progress: { percent: 100, active_topic_ids: [], failed_topic_ids: [] },
  error: {},
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

const approvedProfileResponse = {
  ok: true,
  review: {
    world_id: 'world:aurelia',
    status: 'approved',
    profile_revision: 1,
    profile_hash: 'sha256:profile',
    approved_profile_hash: 'sha256:profile',
    approved_at: '2026-07-20T00:00:00Z',
    approved_by: 'local-author',
    requested_genre: 'fantasy',
    normalized_genre: 'classic_fantasy',
    source: 'registry',
    generated: false,
    route: {},
    review_findings: [],
    error: {},
    profile: {
      profile_id: 'classic_fantasy',
      version: 2,
      display_name: 'Classic fantasy',
      domains: [
        {
          domain_id: 'regions',
          title: 'Realms, Regions and Wild Frontiers',
          entity_kind: 'region',
          dependencies: [],
          required_before_launch: true,
          fields: [],
          target_range: { quick: [2, 4], standard: [5, 8], epic: [9, 14] },
          semantic_roles: [],
          generation_guidance: {
            presentation: {
              page_kind: 'collection',
              card_variant: 'regions',
              image_role: 'landscape',
              group: 'world',
            },
          },
        },
      ],
    },
  },
};

function renderDashboard() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldGenerationDashboard
        generation={previousRun}
        sections={[section]}
        worldId="world:aurelia"
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldGenerationDashboard', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('starts generation from the prominent action after the profile is approved', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith('/genre-profile')) {
        return new Response(JSON.stringify(approvedProfileResponse), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({
        ok: true,
        worker_started: true,
        run: {
          ...previousRun,
          run_id: 'run:new',
          status: 'running',
          progress: { percent: 0, active_topic_ids: ['regions'], failed_topic_ids: [] },
          settings: { provider_route: 'lmstudio', model: 'qwen-world' },
        },
        execution_summary: {
          queued_count: 1,
          reused_count: 0,
          protected_count: 0,
        },
        resolved_route: {
          provider: 'lmstudio',
          model: 'qwen-world',
          source: 'settings_control_center',
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    renderDashboard();
    const generate = await screen.findByRole('button', { name: '✦ Generate World' });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    await waitFor(() => expect(requests.some((request) => request.url.includes('/generation'))).toBe(true));
    const generationRequest = requests.find((request) => request.url.includes('/generation'));
    expect(generationRequest?.url).toContain('/api/rpg/worlds/world%3Aaurelia/generation');
    expect(JSON.parse(String(generationRequest?.init?.body))).toMatchObject({
      scope: { mode: 'full' },
      provider_route: 'configured',
      model: 'configured',
    });
    expect(await screen.findByText(/1 provider topic job queued/)).toBeInTheDocument();
  });

  it('enables continuation for an approved partial review run', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(approvedProfileResponse), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
    const partialReviewRun = {
      ...previousRun,
      graph: { nodes: [{ topic_id: 'realm' }, { topic_id: 'places' }] },
      plan: { topic_ids: ['realm'] },
    };
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldGenerationDashboard generation={partialReviewRun} sections={[section]} worldId="world:aurelia" />
      </QueryClientProvider>,
    );

    await screen.findByText('Profile Approved');
    expect(screen.getAllByRole('button', { name: 'Continue Generation' })).toEqual(
      expect.arrayContaining([expect.objectContaining({ disabled: false })]),
    );
  });

  it('keeps all generation actions locked for an unapproved profile', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ...approvedProfileResponse,
      review: {
        ...approvedProfileResponse.review,
        status: 'review_required',
        approved_profile_hash: '',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    renderDashboard();

    expect(await screen.findByText('Review Required')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '✦ Generate World' })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: 'Generate Selected' })).not.toHaveLength(0);
    expect(screen.getAllByRole('button', { name: 'Generate Selected' }).every((button) => button.hasAttribute('disabled'))).toBe(true);
    expect(screen.getAllByRole('button', { name: 'Regenerate Stale' })).not.toHaveLength(0);
    expect(screen.getAllByRole('button', { name: 'Regenerate Stale' }).every((button) => button.hasAttribute('disabled'))).toBe(true);
  });

  it('selects a retryable topic without retaining the change event', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/genre-profile')) {
        return new Response(JSON.stringify(approvedProfileResponse), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({
        ok: true,
        run_id: previousRun.run_id,
        topic_results: [{
          run_id: previousRun.run_id,
          world_id: previousRun.world_id,
          draft_revision: previousRun.draft_revision,
          topic_id: section.id,
          status: 'blocked',
          candidate: null,
          candidate_hash: '',
          validation: { status: 'blocked', blocking: true, reason_codes: ['dependency_no_candidate'], issues: [] },
          provider: {},
          dependency_hashes: {},
          dependency_trust: {},
          job_id: '',
          created_at: previousRun.created_at,
          updated_at: previousRun.updated_at,
        }],
        analytics: { status: {}, by_code: {}, by_field: {}, by_topic: {}, by_domain: {}, by_model: {}, by_prompt_version: {}, by_provider: {} },
        review_decisions: {},
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    renderDashboard();
    const checkbox = await screen.findByRole('checkbox', { name: 'Select Regions for retry' });
    fireEvent.click(checkbox);

    expect(checkbox).toBeChecked();
    expect(screen.getByText('1 selected')).toBeInTheDocument();
  });

  it('selects and clears every retryable topic from the table header', async () => {
    const places = { ...section, id: 'places', label: 'Places and Points of Interest' };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/genre-profile')) {
        return new Response(JSON.stringify(approvedProfileResponse), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({
        ok: true,
        run_id: previousRun.run_id,
        topic_results: [section, places].map((topic) => ({
          run_id: previousRun.run_id,
          world_id: previousRun.world_id,
          draft_revision: previousRun.draft_revision,
          topic_id: topic.id,
          status: 'blocked',
          candidate: null,
          candidate_hash: '',
          validation: { status: 'blocked', blocking: true, reason_codes: ['dependency_no_candidate'], issues: [] },
          provider: {},
          dependency_hashes: {},
          dependency_trust: {},
          job_id: '',
          created_at: previousRun.created_at,
          updated_at: previousRun.updated_at,
        })),
        analytics: { status: {}, by_code: {}, by_field: {}, by_topic: {}, by_domain: {}, by_model: {}, by_prompt_version: {}, by_provider: {} },
        review_decisions: {},
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldGenerationDashboard generation={previousRun} sections={[section, places]} worldId="world:aurelia" />
      </QueryClientProvider>,
    );

    const selectAll = await screen.findByRole('checkbox', { name: 'Select all retryable topics' });
    fireEvent.click(selectAll);
    expect(selectAll).toBeChecked();
    expect(screen.getByText('2 selected')).toBeInTheDocument();

    fireEvent.click(selectAll);
    expect(selectAll).not.toBeChecked();
    expect(screen.queryByText('2 selected')).not.toBeInTheDocument();
  });

  it('bulk-generates and accepts every missing dossier after all topics are accepted', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith('/genre-profile')) {
        return new Response(JSON.stringify(approvedProfileResponse), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/results')) {
        return new Response(JSON.stringify({
          ok: true,
          topic_results: [{
            run_id: previousRun.run_id,
            world_id: previousRun.world_id,
            draft_revision: previousRun.draft_revision,
            topic_id: section.id,
            status: 'accepted',
            candidate: { topic_id: section.id },
            candidate_hash: 'sha256:accepted',
            validation: { status: 'accepted', blocking: false, reason_codes: [], issues: [] },
            provider: {}, dependency_hashes: {}, dependency_trust: {}, job_id: '',
            created_at: previousRun.created_at, updated_at: previousRun.updated_at,
          }],
          analytics: { by_code: {}, by_field: {}, by_topic: {}, by_domain: {}, by_model: {}, by_prompt_version: {} },
          review_decisions: {},
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/dossier-quality')) {
        return new Response(JSON.stringify({
          ok: true,
          enrichment_candidates: [{ topic_id: section.id, entity_id: 'region:one', title: 'One', word_count: 0, generated_from_legacy: true, issues: ['dossier_sections_required'] }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/enrich-dossiers')) {
        return new Response(JSON.stringify({ ok: true, completed: [{ topic_id: section.id, entity_id: 'region:one', content_hash: 'sha256:lore' }], failed: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    renderDashboard();
    const action = await screen.findByRole('button', { name: 'Repair Dossiers & Headings (1)' });
    expect(action).toBeEnabled();
    fireEvent.click(action);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Repair All (1)' }));

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/enrich-dossiers'))).toBe(true));
    const request = requests.find((entry) => entry.url.endsWith('/enrich-dossiers'));
    expect(JSON.parse(String(request?.init?.body))).toMatchObject({ all_candidates: true, dry_run: false });
  });

  it('reattaches to shared dossier repair progress after the dashboard remounts', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/genre-profile')) {
        return new Response(JSON.stringify(approvedProfileResponse), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/results')) {
        return new Response(JSON.stringify({
          ok: true,
          topic_results: [{
            run_id: previousRun.run_id,
            world_id: previousRun.world_id,
            draft_revision: previousRun.draft_revision,
            topic_id: section.id,
            status: 'accepted',
            candidate: { topic_id: section.id },
            candidate_hash: 'sha256:accepted',
            validation: { status: 'accepted', blocking: false, reason_codes: [], issues: [] },
            provider: {}, dependency_hashes: {}, dependency_trust: {}, job_id: '',
            created_at: previousRun.created_at, updated_at: previousRun.updated_at,
          }],
          analytics: { by_code: {}, by_field: {}, by_topic: {}, by_domain: {}, by_model: {}, by_prompt_version: {} },
          review_decisions: {},
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/dossier-quality')) {
        return new Response(JSON.stringify({
          ok: true,
          enrichment_candidates: [{ topic_id: section.id, entity_id: 'region:one', title: 'One', word_count: 0, generated_from_legacy: true, issues: ['dossier_sections_required'] }],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(
      ['feature', 'rpg', 'world-dossier-repair-progress', 'world:aurelia'],
      { completed: 7, failed: 2, currentTitle: 'The Net-Ghost Collective', total: 50 },
    );

    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldGenerationDashboard generation={previousRun} sections={[section]} worldId="world:aurelia" />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '9');
    expect(screen.getByText(/The Net-Ghost Collective/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Repairing Dossiers & Headings (1)…' })).toBeDisabled();
  });

  it('shows provider-reported and estimated world-generation token usage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(approvedProfileResponse), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldGenerationDashboard
          generation={previousRun}
          sections={[section]}
          tokenUsage={{
            prompt_tokens: 12_000,
            completion_tokens: 3_000,
            total_tokens: 15_000,
            provider_reported_topics: 3,
            estimated_topics: 1,
            unavailable_topics: 0,
            topic_count: 4,
            in_flight_topics: 1,
            generation_duration_ms: 83_000,
            timed_topics: 4,
          }}
          worldId="world:aurelia"
        />
      </QueryClientProvider>,
    );

    expect(screen.getByRole('region', { name: 'World generation token usage' })).toHaveTextContent('15,000');
    expect(screen.getByText(/4 generated/)).toBeInTheDocument();
    expect(screen.getByText(/live batch usage included/i)).toBeInTheDocument();
    expect(screen.getByText('3 reported · 1 estimated')).toBeInTheDocument();
    expect(screen.getByText('1m 23s')).toBeInTheDocument();
    await screen.findByText('Profile Approved');
  });
});
