import { sendGatewayCall } from '../../../api/http';
import type { JobRecord, SendChatMessageResponse } from '../../../api/client';
import { api } from './gateway';
import type { components } from './generated';

export type AssistantContextChatRequest = components['schemas']['AssistantContextChatRequest'];
export type AgentRunSnapshot = components['schemas']['AgentRunSnapshot'];
export type AgentRunEvent = components['schemas']['AgentEvent'];
export type AgentArtifact = components['schemas']['AgentArtifact'];
export type AgentApproval = components['schemas']['AgentApproval'];
export type AgentTaskRevision = components['schemas']['TaskRevision'];
export type AgentEvidenceReceipt = components['schemas']['EvidenceReceipt'];
export type AgentEvidenceSet = components['schemas']['EvidenceSet'];
export type TaskGraphRunSnapshot = components['schemas']['TaskGraphRunSnapshot'];
export type TaskGraphEvent = components['schemas']['TaskGraphEvent'];
export type DeepResearchPlanUpdateRequest = components['schemas']['DeepResearchPlanUpdateRequest'];

/** `/api/workflow-runs/{run_id}` returns an untyped object; the fields the UI reads. */
export interface WorkflowRunSnapshot {
  run_id: string;
  workflow_id: string;
  workflow_version: number;
  status: string;
  current_step_id?: string | null;
  input_payload: Record<string, unknown>;
  revision: number;
}

/** The assistant's gateway calls: context chat, agent runs, task graphs, workflow runs and deep research (PA-2.4). */
export const assistantApiClient = {
  /** Sends a chat message with the context tools' fields (research, agent mode, desktop, local folder). */
  async sendAssistantContextChatMessage(sessionId: string, request: AssistantContextChatRequest): Promise<SendChatMessageResponse> {
    return sendGatewayCall(
      (signal) => api.POST('/api/assistant/context/chat/sessions/{session_id}/messages', { params: { path: { session_id: sessionId } }, body: request, signal }),
      { timeoutMs: 15_000, timeoutMessage: 'Chat request was not accepted by the gateway within 15s.' },
    );
  },

  async getAgentRun(runId: string): Promise<AgentRunSnapshot> {
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}', { params: { path: { run_id: runId } } }));
  },

  async listAgentRunEvents(runId: string, afterSequence = 0): Promise<AgentRunEvent[]> {
    const query = afterSequence > 0 ? { after_sequence: afterSequence } : {};
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/events', { params: { path: { run_id: runId }, query } }));
  },

  async listAgentArtifacts(runId: string): Promise<AgentArtifact[]> {
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/artifacts', { params: { path: { run_id: runId } } }));
  },

  async listAgentTaskRevisions(runId: string): Promise<AgentTaskRevision[]> {
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/task-revisions', { params: { path: { run_id: runId } } }));
  },

  async listAgentEvidenceReceipts(runId: string): Promise<AgentEvidenceReceipt[]> {
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/evidence/receipts', { params: { path: { run_id: runId } } }));
  },

  async getAgentEvidenceSet(runId: string): Promise<AgentEvidenceSet> {
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/evidence', { params: { path: { run_id: runId } } }));
  },

  async commandAgentRun(
    runId: string,
    commandType: 'steer' | 'pause' | 'resume' | 'cancel' | 'approve' | 'reject',
    payload: Record<string, unknown> = {},
  ): Promise<AgentRunSnapshot> {
    return sendGatewayCall(() => api.POST('/api/agent-runs/{run_id}/commands', {
      params: { path: { run_id: runId } },
      body: { command_type: commandType, payload },
    }));
  },

  async listAgentApprovals(runId: string, state?: string): Promise<AgentApproval[]> {
    const query = state ? { state } : {};
    return sendGatewayCall(() => api.GET('/api/agent-runs/{run_id}/approvals', { params: { path: { run_id: runId }, query } }));
  },

  async getTaskGraphRun(runId: string): Promise<TaskGraphRunSnapshot> {
    return sendGatewayCall(() => api.GET('/api/task-graph-runs/{run_id}', { params: { path: { run_id: runId } } }));
  },

  async listTaskGraphEvents(runId: string, afterSequence = 0): Promise<TaskGraphEvent[]> {
    const query = afterSequence > 0 ? { after_sequence: afterSequence } : {};
    return sendGatewayCall(() => api.GET('/api/task-graph-runs/{run_id}/events', { params: { path: { run_id: runId }, query } }));
  },

  async commandTaskGraphRun(
    runId: string,
    command: 'advance' | 'recover' | 'cancel' | 'approve' | 'reject',
    nodeId?: string,
    approvalId?: string,
  ): Promise<TaskGraphRunSnapshot> {
    return sendGatewayCall(() => api.POST('/api/task-graph-runs/{run_id}/commands', {
      params: { path: { run_id: runId } },
      body: {
        command,
        ...(nodeId ? { node_id: nodeId } : {}),
        ...(approvalId ? { approval_id: approvalId } : {}),
      },
    }));
  },

  async getWorkflowRun(runId: string): Promise<WorkflowRunSnapshot> {
    const run = await sendGatewayCall(() => api.GET('/api/workflow-runs/{run_id}', { params: { path: { run_id: runId } } }));
    return run as unknown as WorkflowRunSnapshot;
  },

  async commandWorkflowRun(
    runId: string,
    command: 'pause' | 'resume' | 'cancel' | 'approve' | 'reject',
    stepId?: string,
  ): Promise<WorkflowRunSnapshot> {
    const run = await sendGatewayCall(() => api.POST('/api/workflow-runs/{run_id}/commands', {
      params: { path: { run_id: runId } },
      body: { command, ...(stepId ? { step_id: stepId } : {}) },
    }));
    return run as unknown as WorkflowRunSnapshot;
  },

  async updateDeepResearchPlan(jobId: string, request: DeepResearchPlanUpdateRequest): Promise<JobRecord> {
    return sendGatewayCall(() => api.PATCH('/api/assistant/context/research/jobs/{job_id}/plan', { params: { path: { job_id: jobId } }, body: request }));
  },

  async startDeepResearchPlan(jobId: string): Promise<JobRecord> {
    return sendGatewayCall(() => api.POST('/api/assistant/context/research/jobs/{job_id}/start', { params: { path: { job_id: jobId } } }));
  },
};
