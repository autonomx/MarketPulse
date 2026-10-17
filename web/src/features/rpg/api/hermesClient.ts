import { unwrap } from '../../../api/http';
import { api } from './gateway';

import type { components, paths } from './generated';
export type HermesStatusResponse = paths['/api/hermes/status']['get']['responses'][200]['content']['application/json'];

export type HermesRecentResponse = components['schemas']['HermesRecentResponse'];

export type HermesCandidatePreview = components['schemas']['HermesCandidatePreview'];

export type HermesCandidatePreviewResponse = components['schemas']['HermesCandidatePreviewResponse'];

export type HermesApprovalRequest = components['schemas']['HermesApprovalRequest'];

export type HermesApprovalResponse = components['schemas']['HermesApprovalResponse'];

export type HermesRouteDecisionResponse = components['schemas']['HermesRouteDecisionResponse'];

export type HermesRpgSuggestion = components['schemas']['HermesRpgSuggestion'];

export type HermesRpgSuggestionsRequest = components['schemas']['HermesRpgSuggestionsRequest'];

export type HermesRpgSuggestionsResponse = components['schemas']['HermesRpgSuggestionsResponse'];

export type HermesRpgTurnReadoutRequest = components['schemas']['HermesRpgTurnReadoutRequest'];

export type HermesRpgTurnReadoutResponse = components['schemas']['HermesRpgTurnReadoutResponse'];

export type HermesTestRequest = components['schemas']['HermesTestRequest'];

export type HermesTestResponse = components['schemas']['HermesTestResponse'];

// The Hermes routes publish their responses as documentation-only contracts (web_contracts.py).
async function hermes<T>(call: Promise<{ data?: unknown; error?: unknown; response: Response }>): Promise<T> {
  return (await unwrap(call)) as T;
}

export function getHermesStatus(): Promise<HermesStatusResponse> {
  return hermes(api.GET('/api/hermes/status'));
}

export function getHermesRecent(): Promise<HermesRecentResponse> {
  return hermes(api.GET('/api/hermes/recent'));
}

export function getHermesCandidateDemo(): Promise<HermesCandidatePreviewResponse> {
  return hermes(api.GET('/api/hermes/candidate/demo'));
}

export function getHermesRouteDecision(mode = 'rpg'): Promise<HermesRouteDecisionResponse> {
  return hermes(api.GET('/api/hermes/route-decision', { params: { query: { mode } } }));
}

export function approveHermesCandidate(request: HermesApprovalRequest): Promise<HermesApprovalResponse> {
  return hermes(api.POST('/api/hermes/approve', { body: request }));
}

export function getHermesRpgSuggestions(request: HermesRpgSuggestionsRequest): Promise<HermesRpgSuggestionsResponse> {
  return hermes(api.POST('/api/hermes/rpg/suggestions', { body: request }));
}

export function readHermesRpgTurn(request: HermesRpgTurnReadoutRequest): Promise<HermesRpgTurnReadoutResponse> {
  return hermes(api.POST('/api/hermes/rpg/turn-readout', { body: request }));
}

export function runHermesTest(request: HermesTestRequest = { content: 'house status', dry_run: true }): Promise<HermesTestResponse> {
  return hermes(api.POST('/api/hermes/test', { body: request }));
}
