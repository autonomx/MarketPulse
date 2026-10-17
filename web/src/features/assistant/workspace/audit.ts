import type { ContextSource } from './context';
import type { TokenUsage } from './conversation';

export type AssistantResponseAudit = {
  responseEventId: string;
  provider?: string;
  model?: string;
  assistantIdentityId?: string;
  contextSources: ContextSource[];
  tokenUsage?: TokenUsage;
  latencyMs?: number;
};

export type ResponseAuditSummary = {
  responseEventId: string;
  sourceCount: number;
  sourceTypes: string[];
  tokenTotal?: number;
  latencyMs?: number;
};

export function createAssistantResponseAudit(audit: AssistantResponseAudit): AssistantResponseAudit {
  return {
    ...audit,
    contextSources: audit.contextSources.map((source) => ({ ...source })),
  };
}

export function summarizeAssistantResponseAudit(audit: AssistantResponseAudit): ResponseAuditSummary {
  const sourceTypes = Array.from(new Set(audit.contextSources.map((source) => source.type))).sort();
  const tokenTotal = audit.tokenUsage?.totalTokens ??
    (audit.tokenUsage
      ? (audit.tokenUsage.inputTokens ?? 0) + (audit.tokenUsage.outputTokens ?? 0)
      : undefined);

  return {
    responseEventId: audit.responseEventId,
    sourceCount: audit.contextSources.length,
    sourceTypes,
    tokenTotal,
    latencyMs: audit.latencyMs,
  };
}

export function explainContextSource(source: ContextSource): string {
  return [source.type, source.title ?? source.sourceId, source.reasonIncluded].join(' — ');
}
