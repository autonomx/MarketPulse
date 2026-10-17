 
import { executeToolProposal } from '../workspace/tool-proposal-client';
import type { components } from '../api/generated';
import { unwrap } from '../../../api/http';
import { api } from '../api/gateway';

export type AssistantActionConfigRecord = components['schemas']['AssistantActionConfigRecord'];

export type AssistantToolConfigRecord = components['schemas']['AssistantToolConfigRecord'];

export type AssistantToolsConfigPayload = components['schemas']['AssistantToolsConfigPayload-Output'];
export type AssistantToolsConfigUpdate = components['schemas']['AssistantToolsConfigPayload-Input'];

export type AssistantToolConnectionStartPayload = components['schemas']['AssistantToolConnectionStartPayload'];
export type AssistantToolDisconnectPayload = components['schemas']['AssistantToolDisconnectPayload'];

export type AssistantToolOAuthClientPayload = components['schemas']['AssistantToolOAuthClientPayload'];

export type AssistantToolLedgerEntry = components['schemas']['AssistantToolLedgerEntry'];

export type AssistantToolLedgerPayload = components['schemas']['AssistantToolLedgerPayload'];

export type AssistantToolIntent = components['schemas']['AssistantToolIntent'];

export type LiveAgentToolProposal = {
  proposal_id: string;
  tool_id: string;
  action_id: string;
  title: string;
  summary: string;
  input: Record<string, unknown>;
  risk_level: 'low' | 'medium' | 'high';
  approval_required: boolean;
  ready_for_approval: boolean;
  connection_required: boolean;
  missing_fields: string[];
  reason?: string | null;
  executes: false;
};

export type AssistantToolExecutionPayload = components['schemas']['HermesAssistantToolExecutePayload'];

export type AssistantCapabilityStatus = components['schemas']['AssistantCapabilityStatus'];

export type AssistantCapabilityDashboard = components['schemas']['AssistantCapabilityDashboard'];

export async function fetchAssistantToolsConfig(): Promise<AssistantToolsConfigPayload> {
  return unwrap(api.GET('/api/assistant/tools/config'));
}

export async function saveAssistantToolsConfig(payload: AssistantToolsConfigUpdate): Promise<AssistantToolsConfigPayload> {
  return unwrap(api.POST('/api/assistant/tools/config', { body: payload }));
}

export async function startAssistantToolConnection(toolId: string): Promise<AssistantToolConnectionStartPayload> {
  return unwrap(api.GET('/api/assistant/tools/connect/{tool_id}', { params: { path: { tool_id: toolId } } }));
}

/** Deletes the account's stored token and revokes its grant at the provider when possible. */
export async function disconnectAssistantToolAccount(toolId: string): Promise<AssistantToolDisconnectPayload> {
  return unwrap(api.POST('/api/assistant/tools/connect/{tool_id}/disconnect', { params: { path: { tool_id: toolId } } }));
}

export async function saveAssistantToolOAuthClient(toolId: string, payload: AssistantToolOAuthClientPayload): Promise<AssistantToolConnectionStartPayload> {
  return unwrap(api.POST('/api/assistant/tools/connect/{tool_id}/oauth-client', { params: { path: { tool_id: toolId } }, body: payload }));
}

export async function fetchAssistantToolLedger(): Promise<AssistantToolLedgerPayload> {
  return unwrap(api.GET('/api/assistant/tools/ledger'));
}

export async function detectAssistantToolIntent(message: string): Promise<AssistantToolIntent> {
  return unwrap(api.POST('/api/assistant/tools/intent', { body: { message } }));
}

export async function executeLiveAgentToolProposal(
  proposal: LiveAgentToolProposal,
  input: Record<string, unknown>,
  sessionId?: string | null,
): Promise<AssistantToolExecutionPayload> {
  return executeToolProposal({
    tool_id: proposal.tool_id, action_id: proposal.action_id,
    session_id: sessionId ?? null, input,
  }, true);
}

export async function fetchAssistantCapabilityDashboard(): Promise<AssistantCapabilityDashboard> {
  return unwrap(api.GET('/api/assistant/tools/dashboard'));
}
