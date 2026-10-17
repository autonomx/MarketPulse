import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringCollectionPage, RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import { RpgWorldAuthoringPage } from './RpgWorldAuthoringPage';
import { createTestQueryClient } from '../../test/renderWithProviders';

const world: RpgWorldSummary = {
  id: 'world:aurelia',
  title: 'Aurelia',
  description: 'A living world.',
  status: 'draft',
  source_mode: 'hybrid',
  genre: 'fantasy',
  tone: 'heroic',
  seed: 1,
  draft_revision: 2,
  metadata: {},
  created_at: '2026-07-20T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
};

const section: RpgAuthoringSection = {
  id: 'classes',
  label: 'Classes and Disciplines',
  group: 'world',
  page_kind: 'collection',
  topic_ids: ['classes'],
  entity_kind: 'class',
  dependencies: [],
  required_before_launch: true,
  supports_generation: true,
  supports_images: true,
  supports_entity_editing: true,
  operational_status: 'complete',
  editorial_status: 'needs_review',
  entity_count: 2,
};

const page: RpgAuthoringCollectionPage = {
  ok: true,
  section_id: 'classes',
  page_kind: 'collection',
  title: 'Classes and Disciplines',
  filters: [],
  sort_options: ['name'],
  entities: [
    {
      id: 'class:ward_runner',
      title: 'Ward Runner',
      summary: 'A mobile defender.',
      kind: 'class',
      card_type: 'classes',
      presentation: {
        variant: 'classes',
        eyebrow: 'Class / Discipline',
        badges: [],
        highlights: [],
        groups: [
          {
            label: 'Capabilities',
            items: ['Cross active wards'],
            style: 'list',
          },
        ],
      },
      metadata: {
        id: 'class:ward_runner',
        name: 'Ward Runner',
        capabilities: ['Cross active wards'],
      },
    },
    {
      id: 'discipline:glass_reader',
      title: 'Glass Reader',
      summary: 'A diviner of reflected futures.',
      kind: 'discipline',
      card_type: 'classes',
      presentation: {
        variant: 'classes',
        eyebrow: 'Class / Discipline',
        badges: [],
        highlights: [],
        groups: [],
      },
      metadata: {},
    },
  ],
};

function renderPage() {
  const client = createTestQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <RpgWorldAuthoringPage
        isLoading={false}
        isSaving={false}
        onSaveWorld={() => undefined}
        page={page}
        section={section}
        world={world}
        worldId={world.id}
      />
    </QueryClientProvider>,
  );
}

describe('RpgWorldAuthoringPage collections', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      world,
      targets: [],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('searches and filters formatted collection cards', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Ward Runner' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Glass Reader' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search Classes and Disciplines'), {
      target: { value: 'glass' },
    });
    expect(screen.queryByRole('heading', { name: 'Ward Runner' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Glass Reader' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Search Classes and Disciplines'), {
      target: { value: '' },
    });
    fireEvent.change(screen.getByLabelText('Filter Classes and Disciplines by type'), {
      target: { value: 'class' },
    });
    expect(screen.getByRole('heading', { name: 'Ward Runner' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Glass Reader' })).not.toBeInTheDocument();
  });

  it('opens a compact class card into its complete routed dossier', () => {
    renderPage();

    expect(screen.queryByText('Cross active wards')).not.toBeInTheDocument();
    const wardRunnerCard = screen.getByRole('heading', { name: 'Ward Runner' }).closest('article');
    expect(wardRunnerCard).not.toBeNull();
    fireEvent.click(within(wardRunnerCard as HTMLElement).getByRole('button', { name: 'View details' }));

    expect(screen.getByRole('main', { name: 'Ward Runner details' })).toBeInTheDocument();
    expect(screen.getByText('Cross active wards')).toBeInTheDocument();
  });

  it('keeps the last active entity artwork visible while a replacement is pending', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      ok: true,
      world,
      targets: [{
        target_id: 'entity:class:ward_runner:illustration',
        entity_id: 'class:ward_runner',
        role: 'illustration',
        status: 'stale',
        review_state: 'pending',
        active_asset_id: 'image:ward-runner-prior',
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    renderPage();

    const card = screen.getByRole('heading', { name: 'Ward Runner' }).closest('article');
    const artwork = card?.querySelector('.rpg-authoring-entity-placeholder');
    await waitFor(() => expect(artwork).toHaveClass('has-image'));
    expect(artwork).toHaveStyle({ backgroundImage: expect.stringContaining('image%3Award-runner-prior') });
  });
});
