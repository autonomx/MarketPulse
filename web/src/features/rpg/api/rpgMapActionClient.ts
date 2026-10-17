import { unwrap } from '../../../api/http';
import { api } from './gateway';
import type { components } from './generated';
import type { RpgMapOverlay } from './rpgMapClient';

export type RpgMapActionRequest = components['schemas']['RpgMapActionRequestBody'];

export type RpgMapActionResponse = components['schemas']['RpgMapActionResponse'] & { overlay: RpgMapOverlay };

export async function applyRpgMapAction(
  sessionId: string,
  mapId: string,
  request: RpgMapActionRequest,
): Promise<RpgMapActionResponse> {
  return await unwrap(api.POST('/api/rpg/sessions/{session_id}/maps/{map_id}/map-actions', {
    params: { path: { session_id: sessionId, map_id: mapId } },
    body: request,
  })) as RpgMapActionResponse;
}
