import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgMapSurface } from './RpgMapSurface';
import { createTestQueryClient } from '../../test/renderWithProviders';

const polygon = { kind: 'polygon' as const, points: [[-80, -80], [80, -80], [80, 20], [-80, 20]] as [number, number][] };
const parentDefinition = {
  schema_version: 1, map_id: 'settlement:frost_haven', level: 'settlement', definition_revision: 'sha256:parent', seed: 1,
  parent_map_id: 'region:northern_pass', bounds: { x: 0, y: 0, width: 1000, height: 600 }, background: null,
  route_geometry: [], labels: [],
  objects: [{
    id: 'building:inn', kind: 'building', x: 300, y: 420, anchor: 'bottom_center' as const,
    location_id: 'rusty_flagon_tavern', child_map_id: 'interior:frosted_flagon', label: 'The Frosted Flagon',
    description: 'A warm inn.', tags: ['inn'], render_order: { layer: 'structures' as const, sort_y: 420, offset: 0 },
    sprite: { asset_id: 'asset:inn', width: 220, height: 180 }, footprint: polygon, hitbox: polygon,
  }],
};
const childDefinition = {
  schema_version: 1, map_id: 'interior:frosted_flagon', level: 'interior', definition_revision: 'sha256:child', seed: 2,
  parent_map_id: 'settlement:frost_haven', bounds: { x: 0, y: 0, width: 800, height: 500 }, background: null,
  route_geometry: [], labels: [],
  objects: [{
    id: 'interior:counter', kind: 'prop', x: 420, y: 340, anchor: 'bottom_center' as const,
    location_id: 'rusty_flagon_counter', child_map_id: null, label: 'Service Counter', description: 'The inn counter.', tags: ['trade'],
    render_order: { layer: 'structures' as const, sort_y: 340, offset: 0 }, sprite: { asset_id: 'asset:counter', width: 180, height: 120 },
    footprint: polygon, hitbox: polygon,
  }],
};

function mapOverlay(mapId: string, revision: string, availability: 'ready' | 'unavailable') {
  const isParent = mapId === parentDefinition.map_id;
  return {
    map_id: mapId, session_id: 'session:test', definition_revision: revision, overlay_revision: 0, session_turn_index: 1,
    availability, unavailable_reason: availability === 'ready' ? '' : 'map_not_active',
    current_location_id: availability === 'ready' ? 'rusty_flagon_tavern' : null,
    discovered_object_ids: isParent ? ['building:inn'] : ['interior:counter'],
    visible_object_ids: isParent ? ['building:inn'] : ['interior:counter'],
    object_states: [], fog_polygons: [], routes: [], markers: availability === 'ready' ? [{ id: 'marker:player', kind: 'player', x: 300, y: 420, object_id: 'building:inn', label: 'You' }] : [],
    capabilities: isParent && availability === 'ready' ? [{ type: 'inspect', enabled: true, target_object_id: 'building:inn', target_location_id: 'rusty_flagon_tavern', route_id: null, disabled_reason: '' }] : [],
    environment: {},
  };
}

function renderMap() {
  const client = createTestQueryClient();
  return render(<QueryClientProvider client={client}><RpgMapSurface mapId={parentDefinition.map_id} sessionId="session:test" /></QueryClientProvider>);
}

function pathOf(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

afterEach(() => vi.unstubAllGlobals());

describe('RpgMapSurface hierarchy', () => {
  it('peeks into a child with the same renderer and restores the parent view', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = pathOf(input);
      if (path.includes('/api/rpg/maps/interior%3Afrosted_flagon')) return Response.json({ ok: true, map_id: childDefinition.map_id, definition_revision: childDefinition.definition_revision, definition: childDefinition });
      if (path.includes('/api/rpg/maps/settlement%3Afrost_haven')) return Response.json({ ok: true, map_id: parentDefinition.map_id, definition_revision: parentDefinition.definition_revision, definition: parentDefinition });
      if (path.includes('/maps/interior%3Afrosted_flagon/overlay')) return Response.json({ ok: true, map_id: childDefinition.map_id, definition_revision: childDefinition.definition_revision, overlay_revision: 0, session_turn_index: 1, overlay: mapOverlay(childDefinition.map_id, childDefinition.definition_revision, 'unavailable') });
      if (path.includes('/maps/settlement%3Afrost_haven/overlay')) return Response.json({ ok: true, map_id: parentDefinition.map_id, definition_revision: parentDefinition.definition_revision, overlay_revision: 0, session_turn_index: 1, overlay: mapOverlay(parentDefinition.map_id, parentDefinition.definition_revision, 'ready') });
      return new Response('not found', { status: 404 });
    }));

    renderMap();
    fireEvent.click(await screen.findByRole('button', { name: 'The Frosted Flagon map object' }));
    fireEvent.click(screen.getByRole('button', { name: 'Peek inside' }));

    expect(await screen.findByRole('button', { name: 'Service Counter map object' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Back to Frost Haven/i })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /interior:frosted_flagon interactive map/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Back to Frost Haven/i }));
    expect(await screen.findByRole('button', { name: 'The Frosted Flagon map object' })).toBeInTheDocument();
  });
});
