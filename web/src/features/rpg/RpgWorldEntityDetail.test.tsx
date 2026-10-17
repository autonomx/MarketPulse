import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { RpgAuthoringEntityCard } from './api/rpgWorldAuthoringClient';
import { RpgWorldEntityDetail } from './RpgWorldEntityDetail';
import { createTestQueryClient } from '../../test/renderWithProviders';

const characterClass: RpgAuthoringEntityCard = {
  id: 'class:ward_runner',
  title: 'Ward Runner',
  summary: 'A mobile defender trained to cross unstable wards.',
  short_summary: 'A mobile defender trained to cross unstable wards.',
  kind: 'class',
  card_type: 'classes',
  dossier: {
    schema_version: 'rpg_world_entity_dossier_v1',
    subtitle: 'Pathfinders through broken magic',
    quote: {
      text: 'A ward is only a wall until you learn where it breathes.',
      attribution: 'Runner-Captain Sera Vale',
    },
    quick_facts: [
      { label: 'Primary role', value: 'mobile_defender' },
      { label: 'Institution', value: 'institution:wayfinders' },
    ],
    sections: [
      {
        id: 'overview',
        title: 'Overview',
        paragraphs: [
          'Ward Runners cross unstable magical boundaries while conventional defenders hold position.',
          'Their discipline combines fieldcraft, protective magic, and the judgment to retreat before a failing ward collapses.',
        ],
      },
      {
        id: 'training',
        title: 'Training',
        paragraphs: [
          'Initiates learn to read pressure changes in active wards before they attempt a crossing.',
          'Senior runners practice with damaged ward keys and map safe paths for those who follow.',
        ],
      },
    ],
    related_entity_ids: ['institution:wayfinders'],
  },
  presentation: {
    variant: 'classes',
    eyebrow: 'Class / Discipline',
    badges: ['public'],
    highlights: [{ label: 'Primary role', value: 'mobile_defender' }],
    groups: [
      {
        label: 'Capabilities',
        items: ['Cross active wards', 'Redirect one spell'],
        style: 'list',
      },
      {
        label: 'Progression',
        items: ['Initiate', 'Runner', 'Pathfinder'],
        style: 'chips',
      },
    ],
  },
  metadata: {
    id: 'class:ward_runner',
    name: 'Ward Runner',
    kind: 'class',
    description: 'A mobile defender trained to cross unstable wards.',
    visibility: 'public',
    primary_role: 'mobile_defender',
    capabilities: ['Cross active wards', 'Redirect one spell'],
    progression: ['Initiate', 'Runner', 'Pathfinder'],
    equipment: ['Ward key', 'Travel cloak'],
    institution_ids: ['institution:wayfinders'],
  },
};

describe('RpgWorldEntityDetail', () => {
  it('shows the complete rich dossier as a routed reading page', () => {
    const onClose = vi.fn();
    render(
      <RpgWorldEntityDetail
        entity={characterClass}
        onClose={onClose}
        worldId="world:aurelia"
      />,
    );

    expect(screen.getByRole('main', { name: 'Ward Runner details' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '← Back to Classes' })).toBeInTheDocument();
    expect(screen.getByText('Pathfinders through broken magic')).toBeInTheDocument();
    expect(screen.getByText(/A ward is only a wall/)).toBeInTheDocument();
    expect(screen.getByText('Runner-Captain Sera Vale')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Training' })).toBeInTheDocument();
    expect(screen.getByText(/Senior runners practice with damaged ward keys/)).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Ward Runner sections' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Training' })).toHaveAttribute('href', '#training');
    expect(screen.getAllByText('Wayfinders').length).toBeGreaterThan(0);
    expect(screen.getByRole('heading', { name: 'Additional details' })).toBeInTheDocument();
    expect(screen.getByText('Ward key')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close Ward Runner details' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not publish projected fallback prose as a lore reading page', () => {
    const fallbackEntity = {
      ...characterClass,
      dossier: {
        ...characterClass.dossier,
        generated_from_legacy: true,
        sections: [{
          id: 'canon-details',
          title: 'Canon Details',
          paragraphs: ['Registry Role: A raw fallback representation that must not be shown as authored lore.'],
        }],
      },
    } as RpgAuthoringEntityCard;
    render(<RpgWorldEntityDetail entity={fallbackEntity} onClose={vi.fn()} worldId="world:aurelia" />);

    expect(screen.getByRole('heading', { name: 'LLM-authored lore required' })).toBeInTheDocument();
    expect(screen.getByText(/structured canon was accepted/i)).toBeInTheDocument();
    expect(screen.queryByText(/raw fallback representation/)).not.toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Ward Runner sections' })).not.toBeInTheDocument();
  });

  it('publishes curated imported canon projected into dossier sections', () => {
    const importedEntity = {
      ...characterClass,
      dossier: {
        ...characterClass.dossier,
        generated_from_legacy: true,
        sections: [{
          id: 'overview',
          title: 'Overview',
          paragraphs: ['The Chromatic Saints preserve outlawed rituals in the flood levels.'],
        }],
      },
    } as RpgAuthoringEntityCard;
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RpgWorldEntityDetail
          entity={importedEntity}
          onClose={vi.fn()}
          topic={{
            topic_id: 'cultures', draft_revision: 1, source: 'imported', status: 'ready',
            content: {}, directives: {}, dependency_hashes: {}, input_hash: '', content_hash: '', provenance: {}, updated_at: '',
          }}
          worldId="world:vesper-9-city-of-borrowed-minds"
        />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole('heading', { name: 'LLM-authored lore required' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('heading', { name: 'Overview' }).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Chromatic Saints preserve outlawed rituals/).length).toBeGreaterThan(0);
    expect(screen.getByRole('navigation', { name: 'Ward Runner sections' })).toBeInTheDocument();
  });
});
