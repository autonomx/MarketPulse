import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import type { RpgWorldGenerationTopicResult } from './api/rpgWorldGenerationReviewClient';
import { RpgWorldGenerationCandidateReview } from './RpgWorldGenerationCandidateReview';
import { createTestQueryClient } from '../../test/renderWithProviders';

const section: RpgAuthoringSection = {
  id: 'history_timeline',
  label: 'History Timeline',
  group: 'lore',
  page_kind: 'document',
  topic_ids: ['history_timeline'],
  entity_kind: 'historical_event',
  dependencies: [],
  required_before_launch: true,
  supports_generation: true,
  supports_images: false,
  supports_entity_editing: false,
  operational_status: 'empty',
  editorial_status: 'needs_review',
  entity_count: 1,
};

const candidate = {
  topic_id: 'history_timeline',
  documents: [{ title: 'The Blackout Accords', content: 'The city grids were divided.' }],
  entities: [{
    id: 'ent:history_timeline:001',
    kind: 'historical_event',
    name: 'The Blackout Accords',
    description: 'The city grids were divided among corporate utilities.',
    short_summary: 'The accords divided the city grids among corporate utilities.',
    dossier: {
      schema_version: 'rpg_world_entity_dossier_v1',
      subtitle: 'The treaty that ended one war and privatized the next',
      quick_facts: [],
      sections: [{
        id: 'aftermath',
        title: 'Aftermath',
        paragraphs: ['Each utility inherited a district and the obligation to keep its lights alive.'],
      }],
      related_entity_ids: [],
    },
  }],
  facts: [{ id: 'fact:history:001', content: 'The accords ended the grid war.' }],
  relationships: [],
  knowledge_rules: [],
  story_threads: [],
  provenance: {},
};

const result: RpgWorldGenerationTopicResult = {
  run_id: 'run:review',
  world_id: 'world:1',
  draft_revision: 1,
  topic_id: 'history_timeline',
  status: 'needs_review',
  candidate,
  candidate_hash: 'sha256:original',
  validation: {
    status: 'needs_review',
    blocking: true,
    reason_codes: ['same_model_extraction'],
    issues: [{
      code: 'same_model_extraction',
      topic_id: 'history_timeline',
      message: 'The original response required structural recovery.',
    }],
    summary: 'Recovered from malformed JSON structure.',
  },
  provider: {},
  dependency_hashes: {},
  dependency_trust: {},
  job_id: 'job:history',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

function renderReview(onAccepted = vi.fn(), reviewEnabled = true) {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldGenerationCandidateReview
        onAccepted={onAccepted}
        onClose={vi.fn()}
        onRetryStarted={vi.fn()}
        reviewEnabled={reviewEnabled}
        result={result}
        runId="run:review"
        section={section}
        worldId="world:1"
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldGenerationCandidateReview', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders recovered lore human-readably and accepts the edited candidate', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ url: String(input), init });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));
    const onAccepted = vi.fn();
    renderReview(onAccepted);

    expect(screen.getByText('Recovered — Needs Review')).toBeInTheDocument();
    expect(screen.getAllByText('The Blackout Accords').length).toBeGreaterThan(0);
    expect(screen.getByText('The city grids were divided.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Edit Candidate' }));
    const textarea = screen.getByRole('textbox', {
      name: 'Edit recovered history_timeline candidate',
    });
    const edited = {
      ...candidate,
      documents: [{ title: 'The Blackout Accords', content: 'Edited recovered history.' }],
    };
    fireEvent.change(textarea, { target: { value: JSON.stringify(edited, null, 2) } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply Preview' }));

    expect(screen.getByText('Edited recovered history.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Accept Candidate' }));

    await waitFor(() => expect(onAccepted).toHaveBeenCalled());
    const request = requests.find((row) => row.url.endsWith('/accept'));
    expect(request).toBeDefined();
    expect(JSON.parse(String(request?.init?.body))).toMatchObject({
      expected_candidate_hash: 'sha256:original',
      candidate: {
        topic_id: 'history_timeline',
        documents: [{ content: 'Edited recovered history.' }],
      },
    });
  });

  it('replaces raw provider validation codes in the review banner with readable guidance', () => {
    const providerFailure = {
      ...result,
      validation: {
        ...result.validation,
        summary: 'world_forge_integrity_failed:provider_presentation_contradiction:places:ent:places:005:entities:entity.dossier;provider_short_summary_required:places:ent:places:006:entities',
      },
    };
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <RpgWorldGenerationCandidateReview
          onAccepted={vi.fn()}
          onClose={vi.fn()}
          onRetryStarted={vi.fn()}
          reviewEnabled
          result={providerFailure}
          runId="run:review"
          section={section}
          worldId="world:1"
        />
      </QueryClientProvider>,
    );

    expect(screen.getByText(/Generation completed with validation issues/)).toBeInTheDocument();
    expect(screen.queryByText(/world_forge_integrity_failed/)).not.toBeInTheDocument();
  });

  it('keeps review decisions locked while the rest of the world is generating', () => {
    renderReview(vi.fn(), false);

    expect(screen.getByText('Provisional candidate — Generation continuing')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit Candidate' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retry Generation' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Accept Candidate' })).toBeDisabled();
  });

  it('opens each generated dossier for full review before acceptance', () => {
    renderReview();

    fireEvent.click(screen.getByRole('button', { name: 'View details' }));

    expect(screen.getByRole('main', { name: 'The Blackout Accords details' })).toBeInTheDocument();
    expect(screen.getByText('The treaty that ended one war and privatized the next')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Aftermath' })).toBeInTheDocument();
    expect(screen.getAllByText(/Each utility inherited a district/).length).toBeGreaterThan(0);
  });
});
