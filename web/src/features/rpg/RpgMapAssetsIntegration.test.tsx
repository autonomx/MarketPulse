import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RpgMapSurface } from './RpgMapSurface';
import { createTestQueryClient } from '../../test/renderWithProviders';

const polygon = { kind: 'polygon', points: [[-50, -50], [50, -50], [50, 20], [-50, 20]] };
const definition = {
  schema_version: 1,
  map_id: 'map:assets',
  level: 'settlement',
  definition_revision: 'sha256:assets',
  seed: 1,
  parent_map_id: null,
  bounds: { x: 0, y: 0, width: 1000, height: 600 },
  background: { asset_id: 'asset:rpg-map:frost-haven-base', destination_bounds: { x: 0, y: 0, width: 1000, height: 600 }, source_crop: null },
  route_geometry: [],
  labels: [],
  objects: [{
    id: 'building:inn', kind: 'building', x: 300, y: 420, anchor: 'bottom_center', location_id: 'inn', child_map_id: null,
    label: 'Inn', description: 'Inn', tags: [], render_order: { layer: 'structures', sort_y: 420, offset: 0 },
    sprite: { asset_id: 'asset:rpg-map:timber-inn-01', width: 220, height: 180 }, footprint: polygon, hitbox: polygon,
  }],
};
const overlay = {
  map_id: definition.map_id, session_id: 'session:test', definition_revision: definition.definition_revision,
  overlay_revision: 0, session_turn_index: 0, availability: 'ready', unavailable_reason: '', current_location_id: 'inn',
  discovered_object_ids: ['building:inn'], visible_object_ids: ['building:inn'], object_states: [], fog_polygons: [], routes: [],
  markers: [{ id: 'marker:player', kind: 'player', x: 300, y: 420, object_id: 'building:inn', label: 'You' }],
  capabilities: [], environment: {},
};

afterEach(() => vi.unstubAllGlobals());

it('uses ID-based shared asset URLs while keeping vector fallback geometry', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = typeof input === 'string' ? input : input.toString();
    if (path.includes('/api/rpg/maps/')) return Response.json({ ok: true, map_id: definition.map_id, definition_revision: definition.definition_revision, definition });
    return Response.json({ ok: true, map_id: definition.map_id, definition_revision: definition.definition_revision, overlay_revision: 0, session_turn_index: 0, overlay });
  }));
  const client = createTestQueryClient();
  const view = render(<QueryClientProvider client={client}><RpgMapSurface mapId={definition.map_id} sessionId="session:test" /></QueryClientProvider>);

  expect(await screen.findByRole('img', { name: /interactive map/i })).toBeInTheDocument();
  const background = view.container.querySelector('[data-map-asset-id="asset:rpg-map:frost-haven-base"]');
  const sprite = view.container.querySelector('[data-map-asset-id="asset:rpg-map:timber-inn-01"]');
  expect(background).toHaveAttribute('href', '/api/assets/asset%3Arpg-map%3Afrost-haven-base/file');
  expect(background).toHaveAttribute('preserveAspectRatio', 'none');
  expect(sprite).toHaveAttribute('href', '/api/assets/asset%3Arpg-map%3Atimber-inn-01/file');
  expect(view.container.querySelector('.rpg-map-object-vector-fallback')).toBeInTheDocument();
});
