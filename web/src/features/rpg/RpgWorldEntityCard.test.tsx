import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RpgAuthoringEntityCard } from './api/rpgWorldAuthoringClient';
import { formatAuthoringValue, RpgWorldEntityCard } from './RpgWorldEntityCard';

const pointOfInterest: RpgAuthoringEntityCard = {
  id: 'poi:glass_well',
  title: 'The Glass Well',
  summary: 'A public well that reflects possible futures.',
  kind: 'point_of_interest',
  card_type: 'points_of_interest',
  presentation: {
    variant: 'points_of_interest',
    eyebrow: 'Point of Interest',
    badges: ['partially_known'],
    highlights: [
      { label: 'Location', value: 'location:moon_market' },
      { label: 'Region', value: 'region:central_reach' },
    ],
    groups: [
      {
        label: 'Hooks',
        items: ['A reflection asks for help', 'A rival arrives first'],
        style: 'list',
      },
    ],
  },
  metadata: { id: 'poi:glass_well', name: 'The Glass Well' },
};

const characterClass: RpgAuthoringEntityCard = {
  id: 'class:ward_runner',
  title: 'Ward Runner',
  summary: 'A mobile defender trained to cross unstable wards.',
  kind: 'class',
  card_type: 'classes',
  presentation: {
    variant: 'classes',
    eyebrow: 'Class / Discipline',
    badges: ['public'],
    highlights: [],
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
      {
        label: 'Equipment',
        items: ['Ward key', 'Travel cloak'],
        style: 'chips',
      },
    ],
  },
  metadata: { id: 'class:ward_runner', name: 'Ward Runner' },
};

describe('RpgWorldEntityCard', () => {
  it('renders a point of interest with location, region, and hooks', () => {
    const { container } = render(
      <RpgWorldEntityCard entity={pointOfInterest} worldId="world:aurelia" />,
    );

    const card = container.querySelector('.is-points_of_interest');
    expect(card).not.toBeNull();
    expect(screen.getByText('Point of Interest')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'The Glass Well' })).toBeInTheDocument();
    expect(screen.getByText('Moon Market')).toBeInTheDocument();
    expect(screen.getByText('Central Reach')).toBeInTheDocument();
    const hooks = screen.getByRole('heading', { name: 'Hooks' }).closest('section');
    expect(hooks).not.toBeNull();
    expect(within(hooks as HTMLElement).getByText('A reflection asks for help')).toBeInTheDocument();
    expect(within(hooks as HTMLElement).getByText('A rival arrives first')).toBeInTheDocument();
  });

  it('renders a class with capability lists and progression chips', () => {
    const { container } = render(
      <RpgWorldEntityCard entity={characterClass} worldId="world:aurelia" />,
    );

    const card = container.querySelector('.is-classes');
    expect(card).not.toBeNull();
    expect(screen.getByText('Class / Discipline')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Ward Runner' })).toBeInTheDocument();
    expect(screen.getByText('Cross active wards')).toBeInTheDocument();
    expect(screen.getByText('Initiate')).toBeInTheDocument();
    expect(screen.getByText('Pathfinder')).toBeInTheDocument();
    expect(screen.getByText('Ward key')).toBeInTheDocument();
  });

  it('uses approved generated artwork instead of a letter placeholder', () => {
    const { container } = render(
      <RpgWorldEntityCard
        entity={pointOfInterest}
        imageAssetId="image:glass-well"
        worldId="world:aurelia"
      />,
    );

    const preview = container.querySelector('.rpg-authoring-entity-placeholder');
    expect(preview).toHaveClass('has-image');
    expect(preview).toHaveStyle({
      backgroundImage: 'url("/api/assets/image%3Aglass-well/file")',
      backgroundPosition: 'center',
      backgroundSize: 'cover',
    });
    expect(preview).toHaveTextContent('');
  });

  it('formats references, states, numbers, booleans, and structured resources for cards', () => {
    expect(formatAuthoringValue('location:moon_market')).toBe('Moon Market');
    expect(formatAuthoringValue('partially_known')).toBe('Partially Known');
    expect(formatAuthoringValue(2500)).toBe('2,500');
    expect(formatAuthoringValue(true)).toBe('Yes');
    expect(formatAuthoringValue({ resource: 'currency', amount: 25 })).toBe('currency: 25');
  });
});
