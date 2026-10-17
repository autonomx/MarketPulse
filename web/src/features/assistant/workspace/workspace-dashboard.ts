import type { ChatSessionMode } from './domain';
import { getWorkspaceQualityStatus, summarizeWorkspaceQuality, type WorkspaceQualitySignal } from './quality';

export type AssistantWorkspaceDashboardStatus = 'ready' | 'review' | 'blocked';

export type AssistantWorkspaceDashboardMetric = {
  id: string;
  label: string;
  value: string;
};

export type AssistantWorkspaceDashboardInput = {
  workspaceName: string;
  projectName?: string;
  sessionTitle: string;
  sessionMode: ChatSessionMode;
  providerLabel?: string;
  modelLabel?: string;
  messageCount: number;
  contextSourceCount: number;
  memoryCount: number;
  knowledgeChunkCount: number;
  enabledToolCount: number;
  qualitySignals: WorkspaceQualitySignal[];
  liveStatus?: string;
};

export type AssistantWorkspaceDashboardView = {
  title: string;
  subtitle: string;
  status: AssistantWorkspaceDashboardStatus;
  statusLabel: string;
  metrics: AssistantWorkspaceDashboardMetric[];
  badges: string[];
  failedQualitySignals: WorkspaceQualitySignal[];
};

export function createAssistantWorkspaceDashboard(input: AssistantWorkspaceDashboardInput): AssistantWorkspaceDashboardView {
  const qualitySummary = summarizeWorkspaceQuality(input.qualitySignals);
  const status = getWorkspaceQualityStatus(qualitySummary);
  const providerLabel = input.providerLabel?.trim() || 'Default provider';
  const modelLabel = input.modelLabel?.trim() || 'Default model';
  const projectLabel = input.projectName?.trim() || 'No project';

  return {
    title: input.workspaceName,
    subtitle: `${projectLabel} · ${input.sessionTitle}`,
    status,
    statusLabel: status === 'ready' ? 'Workspace ready' : status === 'review' ? 'Needs review' : 'Blocked',
    metrics: [
      { id: 'messages', label: 'Messages', value: String(Math.max(0, input.messageCount)) },
      { id: 'context', label: 'Context sources', value: String(Math.max(0, input.contextSourceCount)) },
      { id: 'memory', label: 'Memories', value: String(Math.max(0, input.memoryCount)) },
      { id: 'knowledge', label: 'Knowledge chunks', value: String(Math.max(0, input.knowledgeChunkCount)) },
      { id: 'tools', label: 'Enabled tools', value: String(Math.max(0, input.enabledToolCount)) },
    ],
    badges: [input.sessionMode, providerLabel, modelLabel, input.liveStatus?.trim() || 'text only'],
    failedQualitySignals: input.qualitySignals.filter((signal) => !signal.passed),
  };
}
