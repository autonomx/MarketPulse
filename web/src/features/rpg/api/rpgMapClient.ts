import { unwrap } from '../../../api/http';
import { api } from './gateway';
import type { components } from './generated';

export type RpgMapAvailability = 'ready' | 'unavailable' | 'stale' | 'error';
export type RpgMapLayer =
  | 'background'
  | 'terrain'
  | 'routes'
  | 'ground_props'
  | 'structures'
  | 'markers'
  | 'labels'
  | 'fog'
  | 'interaction';
export type RpgMapObjectStatus = 'normal' | 'open' | 'closed' | 'damaged' | 'burned' | 'occupied';

export interface RpgMapBounds {
  height: number;
  width: number;
  x: number;
  y: number;
}

export interface RpgMapRenderOrder {
  layer: RpgMapLayer;
  offset: number;
  sort_y: number;
}

export interface RpgMapPolygon {
  kind: 'polygon';
  points: [number, number][];
}

export interface RpgMapSprite {
  asset_id: string;
  height: number;
  width: number;
}

export interface RpgMapObjectDefinition {
  anchor: 'bottom_center' | 'center' | 'top_left';
  child_map_id?: string | null;
  description: string;
  footprint?: RpgMapPolygon | null;
  hitbox?: RpgMapPolygon | null;
  id: string;
  kind: string;
  label: string;
  location_id?: string | null;
  render_order: RpgMapRenderOrder;
  sprite?: RpgMapSprite | null;
  tags: string[];
  x: number;
  y: number;
}

export interface RpgMapRouteGeometry {
  points: [number, number][];
  route_id: string;
  style: string;
}

export interface RpgMapLabelDefinition {
  id: string;
  priority: number;
  text: string;
  x: number;
  y: number;
}

export interface RpgMapDefinition {
  background?: {
    asset_id: string;
    destination_bounds: RpgMapBounds;
    source_crop?: RpgMapBounds | null;
  } | null;
  bounds: RpgMapBounds;
  definition_revision: string;
  labels: RpgMapLabelDefinition[];
  level: string;
  map_id: string;
  objects: RpgMapObjectDefinition[];
  parent_map_id?: string | null;
  route_geometry: RpgMapRouteGeometry[];
  schema_version: number;
  seed: number;
}

export interface RpgMapMarker {
  id: string;
  kind: string;
  label: string;
  object_id?: string | null;
  x: number;
  y: number;
}

export interface RpgMapRouteOverlay {
  known: boolean;
  reason: string;
  route_id: string;
  safe: boolean;
  status: 'open' | 'blocked' | 'locked' | 'unknown';
}

export interface RpgMapObjectDynamicState {
  discovered: boolean;
  object_id: string;
  presentation_hint: string;
  status: RpgMapObjectStatus;
  visible: boolean;
}

export interface RpgMapFogPolygon {
  id: string;
  points: [number, number][];
}

export interface RpgMapActionCapability {
  disabled_reason: string;
  enabled: boolean;
  route_id?: string | null;
  target_location_id?: string | null;
  target_object_id: string;
  type: 'travel' | 'inspect' | 'enter' | 'talk' | 'trade';
}

export interface RpgMapOverlay {
  availability: RpgMapAvailability;
  capabilities: RpgMapActionCapability[];
  current_location_id?: string | null;
  definition_revision: string;
  discovered_object_ids: string[];
  environment: Record<string, string>;
  fog_polygons?: RpgMapFogPolygon[];
  map_id: string;
  markers: RpgMapMarker[];
  object_states?: RpgMapObjectDynamicState[];
  overlay_revision: number;
  routes: RpgMapRouteOverlay[];
  session_id: string;
  session_turn_index: number;
  unavailable_reason: string;
  visible_object_ids: string[];
}

// The route validates the envelope; the definition and overlay documents are read through the views above.
export type RpgMapDefinitionResponse = components['schemas']['RpgMapDefinitionResponse'] & { definition: RpgMapDefinition | null };

export type RpgMapOverlayResponse = components['schemas']['RpgMapOverlayResponse'] & { overlay: RpgMapOverlay };

export async function getRpgMapDefinition(
  mapId: string,
  knownDefinitionRevision?: string,
  sessionId?: string,
): Promise<RpgMapDefinitionResponse> {
  const query = {
    ...(knownDefinitionRevision ? { known_definition_revision: knownDefinitionRevision } : {}),
    ...(sessionId ? { session_id: sessionId } : {}),
  };
  return await unwrap(api.GET('/api/rpg/maps/{map_id}', { params: { path: { map_id: mapId }, query } })) as RpgMapDefinitionResponse;
}

export async function getRpgMapOverlay(
  sessionId: string,
  mapId: string,
): Promise<RpgMapOverlayResponse> {
  return await unwrap(api.GET('/api/rpg/sessions/{session_id}/maps/{map_id}/overlay', {
    params: { path: { session_id: sessionId, map_id: mapId } },
  })) as RpgMapOverlayResponse;
}
