import { unwrap } from '../../../api/http';
import { api } from './gateway';

import type { components } from './generated';
export type HermesRpgApprovedFlowConfig = components['schemas']['HermesRpgApprovedFlowConfig'];

export type HermesRpgApprovedFlowRequest = components['schemas']['HermesRpgApprovedFlowRequest'];

export type HermesRpgApprovedFlowResponse = components['schemas']['HermesRpgApprovedFlowResponse'];

export type HermesRpgExecutionLedgerItem = components['schemas']['HermesRpgExecutionLedgerItem'];

export type HermesRpgExecutionLedgerResponse = components['schemas']['HermesRpgExecutionLedgerResponse'];

export async function getHermesRpgApprovedFlowConfig(): Promise<HermesRpgApprovedFlowConfig> {
  return await unwrap(api.GET('/api/hermes/rpg/approved-flow/config')) as HermesRpgApprovedFlowConfig;
}

export async function runHermesRpgApprovedFlow(
  request: HermesRpgApprovedFlowRequest,
): Promise<HermesRpgApprovedFlowResponse> {
  return await unwrap(api.POST('/api/hermes/rpg/approved-flow', { body: request })) as HermesRpgApprovedFlowResponse;
}

export async function getHermesRpgExecutionLedger(params: { limit?: number; sessionId?: string; sequenceId?: string } = {}): Promise<HermesRpgExecutionLedgerResponse> {
  const query = {
    ...(params.limit ? { limit: params.limit } : {}),
    ...(params.sessionId ? { session_id: params.sessionId } : {}),
    ...(params.sequenceId ? { sequence_id: params.sequenceId } : {}),
  };
  return await unwrap(api.GET('/api/hermes/rpg/approved-flow/ledger', { params: { query } })) as HermesRpgExecutionLedgerResponse;
}
