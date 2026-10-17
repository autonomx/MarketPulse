import { unwrap } from '../../../api/http';
import { api } from './gateway';

import type { components } from './generated';
export type HermesRpgSequenceRequest = components['schemas']['HermesRpgSequenceRequest'];

export type HermesRpgSequenceResponse = components['schemas']['HermesRpgSequenceResponse'];

export type HermesRpgSequenceStateResponse = components['schemas']['HermesRpgSequenceStateResponse'];

export async function checkHermesRpgSequence(
  request: HermesRpgSequenceRequest,
): Promise<HermesRpgSequenceResponse> {
  return await unwrap(api.POST('/api/hermes/rpg/sequence/review', { body: request })) as HermesRpgSequenceResponse;
}

export async function getHermesRpgSequenceState(sessionId = ''): Promise<HermesRpgSequenceStateResponse> {
  const query = sessionId ? { session_id: sessionId } : {};
  return await unwrap(api.GET('/api/hermes/rpg/sequence/state', { params: { query } })) as HermesRpgSequenceStateResponse;
}
