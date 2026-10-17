import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringDocumentPage, RpgAuthoringSection } from './api/rpgWorldAuthoringClient';
import type { RpgWorldSummary } from './api/rpgWorldLibraryClient';
import { RpgWorldAuthoringPage } from './RpgWorldAuthoringPage';
import { createTestQueryClient } from '../../test/renderWithProviders';

const world: RpgWorldSummary = {
  id: 'world:hogwarth',
  title: 'Hogwarth',
  description: 'A living magical realm.',
  status: 'draft',
  source_mode: 'hybrid',
  genre: 'fantasy',
  tone: 'mysterious',
  seed: 7,
  draft_revision: 1,
  metadata: {},
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
};

function section(id: string, label: string): RpgAuthoringSection {
  return {
    id,
    label,
    group: 'lore',
    page_kind: 'document',
    topic_ids: [id],
    dependencies: [],
    required_before_launch: true,
    supports_generation: true,
    supports_images: false,
    supports_entity_editing: false,
    operational_status: 'complete',
    editorial_status: 'unreviewed',
    entity_count: 0,
  };
}

function renderPage(page: RpgAuthoringDocumentPage, currentSection: RpgAuthoringSection) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <RpgWorldAuthoringPage
        isLoading={false}
        isSaving={false}
        onSaveWorld={vi.fn()}
        page={page}
        section={currentSection}
        world={world}
        worldId={world.id}
      />
    </QueryClientProvider>,
  );
}

describe('finalized world authoring design', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, targets: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('renders lore as one vertical document with an on-page section navigator', () => {
    const result = renderPage({
      ok: true,
      section_id: 'realm_overview',
      page_kind: 'document',
      title: 'Realm Overview',
      summary: 'A realm shaped by magic and conflict.',
      related_entities: [],
      body: [
        { kind: 'section', title: 'Overview', body: 'The realm endures.' },
        { kind: 'facts', title: 'Canon Facts', items: [{ label: 'Origin', statement: 'Born from the first weave.' }] },
        { kind: 'section', title: 'Major Powers', body: 'Five powers contend for influence.' },
      ],
    }, section('realm_overview', 'Realm Overview'));

    expect(result.container.querySelector('.rpg-authoring-document-shell')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Realm Overview sections' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Canon Facts' })).toHaveAttribute('href', '#canon-facts');
  });

  it('renders history and calendar lore as vertical chronicles', () => {
    const result = renderPage({
      ok: true,
      section_id: 'history',
      page_kind: 'document',
      title: 'History',
      summary: 'A chronicle of ages.',
      related_entities: [],
      body: [
        { kind: 'section', title: 'Age of Origins', body: 'The first age begins.' },
        { kind: 'section', title: 'Age of Fracture', body: 'The realm is divided.' },
      ],
    }, section('history', 'History'));

    expect(result.container.querySelector('.rpg-authoring-document-shell.is-chronicle')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Age of Origins' })).toHaveAttribute('href', '#age-of-origins');
  });
});
