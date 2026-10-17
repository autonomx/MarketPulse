import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringEntityCard, RpgAuthoringTopic } from './api/rpgWorldAuthoringClient';
import { RpgWorldEntityEditor } from './RpgWorldEntityEditor';
import { createTestQueryClient } from '../../test/renderWithProviders';

const dossier = {
  schema_version: 'rpg_world_entity_dossier_v1',
  subtitle: 'Keeper of the crossroads',
  quote: null,
  quick_facts: [],
  sections: [
    {
      id: 'overview',
      title: 'Overview',
      paragraphs: ['Bran keeps the Rusty Flagon open to travelers who need shelter and reliable local knowledge.'],
    },
  ],
  related_entity_ids: [],
};

const entity: RpgAuthoringEntityCard = {
  id: 'npc:bran',
  title: 'Bran',
  summary: 'A practical innkeeper.',
  short_summary: 'A practical innkeeper.',
  dossier,
  kind: 'npc',
  card_type: 'npcs',
  presentation: {
    variant: 'npcs',
    eyebrow: 'Character',
    badges: [],
    highlights: [],
    groups: [{ label: 'Goals', items: ['protect the inn'], style: 'list' }],
  },
  metadata: { id: 'npc:bran', name: 'Bran', kind: 'npc', goals: ['protect the inn'], dossier },
};

const topic: RpgAuthoringTopic = {
  topic_id: 'npcs',
  draft_revision: 4,
  source: 'ai',
  status: 'ready',
  content: { entities: [entity.metadata, { id: 'npc:elara', name: 'Elara' }] },
  directives: {},
  dependency_hashes: {},
  input_hash: 'sha256:input',
  content_hash: 'sha256:old',
  provenance: {},
  updated_at: '2026-07-20T00:00:00Z',
};

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderEditor() {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <RpgWorldEntityEditor entity={entity} topic={topic} worldId="world:aurelia" />
    </QueryClientProvider>,
  );
}

describe('RpgWorldEntityEditor', () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  beforeEach(() => {
    requests.length = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith('/regenerate-dossier-preview')) {
        return response({
          ok: true,
          preview_only: true,
          world_id: 'world:aurelia',
          topic_id: 'npcs',
          entity_id: 'npc:bran',
          expected_draft_revision: 4,
          expected_content_hash: 'sha256:old',
          short_summary: 'A steadfast keeper of a threatened crossroads.',
          dossier: {
            ...dossier,
            subtitle: 'The last lamp on the road',
            sections: [
              {
                id: 'overview',
                title: 'Overview',
                paragraphs: ['Bran maintains the inn as neutral ground while rival powers tighten their hold on the road.'],
              },
              {
                id: 'backstory',
                title: 'Backstory',
                paragraphs: ['Years of interrupted trade and broken promises taught Bran to value practical loyalties over grand declarations.'],
              },
            ],
          },
          generation: { provider: 'lmstudio' },
          canonical_fields_preserved: true,
          stored: false,
        });
      }
      if (init?.method === 'PATCH' && url.endsWith('/dossier')) {
        const body = JSON.parse(String(init.body));
        return response({
          ok: true,
          topic: { ...topic, content_hash: 'sha256:dossier' },
          entity: { ...entity.metadata, short_summary: body.short_summary, dossier: body.dossier },
          stale_topic_ids: [],
          stale_entity_ids: [],
          canonical_fields_preserved: true,
          editorial_only: true,
        });
      }
      if (init?.method === 'PATCH') {
        return response({ ok: true, topic: { ...topic, content_hash: 'sha256:new' }, entity: entity.metadata, stale_topic_ids: ['quests'], stale_entity_ids: ['quest:road'] });
      }
      if (init?.method === 'POST') {
        return response({ ok: true, topic: { ...topic, content_hash: 'sha256:regen' }, entity: { ...entity.metadata, personality: 'steadfast' }, stale_topic_ids: [], stale_entity_ids: [] });
      }
      return response({
        ok: true,
        world: { id: 'world:aurelia' },
        topic,
        entity: entity.metadata,
        history: [{ history_sequence: 1, operation: 'manual_edit', before: entity.metadata, after: entity.metadata, topic_content_hash: topic.content_hash, metadata: {}, created_at: '2026-07-20T00:00:00Z' }],
      });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('saves one canonical entity with topic concurrency tokens', async () => {
    renderEditor();
    fireEvent.click(screen.getByText('Edit or regenerate'));
    const editor = await screen.findByLabelText('Entity JSON for Bran');
    fireEvent.change(editor, {
      target: { value: JSON.stringify({ ...entity.metadata, goals: ['protect every traveler'] }) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Canonical Entity' }));

    await waitFor(() => expect(requests.some((request) => request.init?.method === 'PATCH')).toBe(true));
    const saved = requests.find((request) => request.init?.method === 'PATCH');
    expect(saved?.url).toContain('/topics/npcs/entities/npc%3Abran');
    expect(JSON.parse(String(saved?.init?.body))).toMatchObject({
      expected_draft_revision: 4,
      expected_content_hash: 'sha256:old',
      changes: { id: 'npc:bran', goals: ['protect every traveler'] },
    });
    expect(await screen.findByText(/1 dependent entities need review/)).toBeInTheDocument();
  });

  it('previews dossier regeneration without storing and applies only after review', async () => {
    renderEditor();
    fireEvent.click(screen.getByText('Edit or regenerate'));
    fireEvent.change(await screen.findByLabelText('Regeneration directives for Bran'), {
      target: { value: '{"focus":"deepen the inn history"}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview Dossier Regeneration' }));

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/regenerate-dossier-preview'))).toBe(true));
    expect(requests.some((request) => request.init?.method === 'PATCH')).toBe(false);
    expect(await screen.findByText(/Nothing has been stored/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('The last lamp on the road')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Apply Preview' }));
    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/dossier') && request.init?.method === 'PATCH')).toBe(true));
    expect(await screen.findByText(/Applied the reviewed prose preview/)).toBeInTheDocument();
  });

  it('regenerates the entire selected entity with directives', async () => {
    renderEditor();
    fireEvent.click(screen.getByText('Edit or regenerate'));
    await screen.findByLabelText('Entire entity regeneration directives for Bran');
    fireEvent.change(screen.getByLabelText('Entire entity regeneration directives for Bran'), {
      target: { value: '{"focus":"deepen motives"}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate Entire Entity' }));

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/regenerate') && request.init?.method === 'POST')).toBe(true));
    const regenerated = requests.find((request) => request.url.endsWith('/regenerate') && request.init?.method === 'POST');
    expect(regenerated?.url).toContain('/entities/npc%3Abran/regenerate');
    expect(JSON.parse(String(regenerated?.init?.body))).toEqual({
      expected_draft_revision: 4,
      expected_content_hash: 'sha256:old',
      directives: { focus: 'deepen motives' },
    });
    expect(await screen.findByText(/preserving 1 sibling entities/)).toBeInTheDocument();
  });
});
