import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpgMapSurface } from './RpgMapSurface';
import { createTestQueryClient } from '../../test/renderWithProviders';

const polygon = { kind: 'polygon' as const, points: [[-110, -90], [110, -90], [110, 20], [-110, 20]] as [number, number][] };
const definition = {
  schema_version: 1,
  map_id: 'settlement:frost_haven',
  level: 'settlement',
  definition_revision: 'sha256:abc123',
  seed: 1,
  parent_map_id: 'region:northern_pass',
  bounds: { x: 0, y: 0, width: 1000, height: 600 },
  background: null,
  objects: [{
    id: 'building:inn', kind: 'building', x: 300, y: 420, anchor: 'bottom_center' as const,
    location_id: 'rusty_flagon_tavern', label: 'The Frosted Flagon', description: 'A warm inn.', tags: ['inn'],
    render_order: { layer: 'structures' as const, sort_y: 420, offset: 0 },
    sprite: { asset_id: 'asset:inn', width: 220, height: 180 }, footprint: polygon, hitbox: polygon, child_map_id: null,
  }],
  route_geometry: [{ route_id: 'route:frost_haven:inn_market', points: [[300, 420], [500, 360], [760, 330]] as [number, number][], style: 'street' }],
  labels: [{ id: 'label:frost_haven', text: 'FROST HAVEN', x: 500, y: 120, priority: 100 }],
};

function overlay(availability: 'ready' | 'unavailable' = 'ready') {
  return {
    map_id: definition.map_id,
    session_id: 'session:test',
    definition_revision: definition.definition_revision,
    overlay_revision: 2,
    session_turn_index: 5,
    availability,
    unavailable_reason: availability === 'ready' ? '' : 'current_location_unavailable',
    current_location_id: availability === 'ready' ? 'rusty_flagon_tavern' : null,
    discovered_object_ids: ['building:inn'],
    visible_object_ids: ['building:inn'],
    object_states: [{ object_id: 'building:inn', discovered: true, visible: true, status: 'occupied', presentation_hint: 'The common room is occupied.' }],
    fog_polygons: [{ id: 'fog:north', points: [[500, 0], [1000, 0], [1000, 300], [500, 300]] as [number, number][] }],
    routes: [{ route_id: 'route:frost_haven:inn_market', status: 'open', known: true, safe: true, reason: '' }],
    markers: availability === 'ready' ? [
      { id: 'marker:player', kind: 'player', x: 300, y: 420, object_id: 'building:inn', label: 'You' },
      { id: 'marker:quest:shipment', kind: 'quest', x: 760, y: 330, object_id: null, label: 'Missing shipment' },
    ] : [],
    capabilities: [{ type: 'inspect', enabled: true, target_object_id: 'building:inn', target_location_id: 'rusty_flagon_tavern', route_id: null, disabled_reason: '' }],
    environment: { weather: 'Snow', light: 'Moonlight', visibility: 'Low' },
  };
}

function renderMap() {
  const client = createTestQueryClient();
  return render(<QueryClientProvider client={client}><RpgMapSurface mapId={definition.map_id} sessionId="session:test" /></QueryClientProvider>);
}

function requestPath(input: RequestInfo | URL): string {
  return typeof input === 'string' ? new URL(input, 'http://localhost').pathname : new URL(input.toString()).pathname;
}

function installFetch(availability: 'ready' | 'unavailable' = 'ready') {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = requestPath(input);
    if (path.includes('/api/rpg/maps/')) return Response.json({ ok: true, map_id: definition.map_id, definition_revision: definition.definition_revision, definition });
    if (path.includes('/overlay')) return Response.json({ ok: true, map_id: definition.map_id, definition_revision: definition.definition_revision, overlay_revision: 2, session_turn_index: 5, overlay: overlay(availability) });
    return new Response('not found', { status: 404 });
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe('RpgMapSurface', () => {
  it('renders objects, dynamic state, fog, environment, routes, labels, and markers', async () => {
    installFetch();
    const view = renderMap();
    expect(await screen.findByRole('img', { name: /interactive map/i })).toBeInTheDocument();
    const object = view.container.querySelector('[data-map-object-id="building:inn"]');
    expect(object).toHaveAttribute('data-map-object-status', 'occupied');
    expect(view.container.querySelector('[data-map-fog-id="fog:north"]')).toHaveAttribute('points', '500,0 1000,0 1000,300 500,300');
    expect(view.container.querySelector('[data-map-environment-weather="snow"]')).toHaveAttribute('data-map-environment-light', 'moonlight');
    expect(view.container.querySelector('[data-map-route-id="route:frost_haven:inn_market"]')).toHaveAttribute('points', '300,420 500,360 760,330');
    expect(view.container.querySelector('[data-map-label-id="label:frost_haven"]')).toHaveTextContent('FROST HAVEN');
    expect(view.container.querySelector('[data-map-marker-kind="player"]')).toBeInTheDocument();
  });

  it('toggles presentation layers without mutating map data', async () => {
    installFetch();
    const view = renderMap();
    await screen.findByRole('img', { name: /interactive map/i });
    fireEvent.click(screen.getByRole('checkbox', { name: 'fog' }));
    expect(view.container.querySelector('[data-map-layer="fog"]')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'routes' }));
    expect(view.container.querySelector('[data-map-layer="routes"]')).not.toBeInTheDocument();
    expect(view.container.querySelector('[data-map-object-id="building:inn"]')).toBeInTheDocument();
  });

  it('shows dynamic presentation details for hover and selection', async () => {
    installFetch();
    renderMap();
    const object = await screen.findByRole('button', { name: 'The Frosted Flagon map object' });
    fireEvent.mouseEnter(object);
    expect(screen.getByRole('tooltip')).toHaveTextContent('The common room is occupied.');
    fireEvent.click(object);
    expect(screen.getByRole('region', { name: 'Selected map object' })).toHaveTextContent('occupied');
    expect(screen.getByText('inspect')).toBeInTheDocument();
  });

  it('renders a truthful unavailable state without a player marker', async () => {
    installFetch('unavailable');
    const view = renderMap();
    expect(await screen.findByText('Live position unavailable')).toBeInTheDocument();
    await waitFor(() => expect(view.container.querySelector('[data-map-marker-kind="player"]')).not.toBeInTheDocument());
  });
});
