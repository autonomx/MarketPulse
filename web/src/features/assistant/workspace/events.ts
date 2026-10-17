import type { ContextAssembly } from './context';
import type { ConversationTurn, TokenUsage } from './conversation';

export const ASSISTANT_WORKSPACE_EVENT_TYPES = [
  'user_message',
  'assistant_message',
  'tool_call',
  'tool_result',
  'memory_created',
  'memory_recalled',
  'file_attached',
  'knowledge_retrieved',
  'voice_transcript',
  'provider_changed',
  'model_changed',
  'assistant_identity_changed',
  'context_assembled',
  'operation_failed',
] as const;

export type AssistantWorkspaceEventType = (typeof ASSISTANT_WORKSPACE_EVENT_TYPES)[number];

export type AssistantWorkspaceFailureOperation =
  | 'chat_request'
  | 'provider_request'
  | 'stt_request'
  | 'tts_request'
  | 'tool_execution'
  | 'audio_capture'
  | 'unknown';

export type AssistantWorkspaceEventPayloadByType = {
  user_message: {
    turn: ConversationTurn;
  };
  assistant_message: {
    turn: ConversationTurn;
  };
  tool_call: {
    toolCallId: string;
    toolName: string;
    arguments?: Record<string, unknown>;
    approved?: boolean;
  };
  tool_result: {
    toolCallId: string;
    status: 'completed' | 'failed' | 'denied';
    result?: unknown;
    error?: string;
    tokenUsage?: TokenUsage;
  };
  memory_created: {
    memoryId: string;
    scope: 'global' | 'workspace' | 'project' | 'session';
    content: string;
  };
  memory_recalled: {
    memoryIds: string[];
    query?: string;
  };
  file_attached: {
    fileId: string;
    name: string;
    mimeType?: string;
    sizeBytes?: number;
  };
  knowledge_retrieved: {
    query: string;
    chunkIds: string[];
    itemIds?: string[];
  };
  voice_transcript: {
    segmentId: string;
    text: string;
    isFinal: boolean;
  };
  provider_changed: {
    providerId: string;
    previousProviderId?: string;
  };
  model_changed: {
    model: string;
    previousModel?: string;
  };
  assistant_identity_changed: {
    assistantIdentityId: string;
    previousAssistantIdentityId?: string;
  };
  context_assembled: {
    sourceIds: string[];
    estimatedTokens: number;
    assembly?: ContextAssembly;
    tokenUsage?: TokenUsage;
  };
  operation_failed: {
    operation: AssistantWorkspaceFailureOperation;
    message: string;
    statusCode?: number;
    providerId?: string;
    modelId?: string;
    recoverable?: boolean;
    details?: Record<string, unknown>;
  };
};

export type AssistantWorkspaceEventPayload<TType extends AssistantWorkspaceEventType> =
  AssistantWorkspaceEventPayloadByType[TType];

export type AssistantWorkspaceEventBase<
  TType extends AssistantWorkspaceEventType,
  TPayload extends AssistantWorkspaceEventPayload<TType>,
> = {
  id: string;
  type: TType;
  workspaceId: string;
  projectId?: string;
  sessionId?: string;
  payload: TPayload;
  createdAt: string;
};

export type AssistantWorkspaceEvent = {
  [TType in AssistantWorkspaceEventType]: AssistantWorkspaceEventBase<
    TType,
    AssistantWorkspaceEventPayload<TType>
  >;
}[AssistantWorkspaceEventType];

export function isAssistantWorkspaceEventType(value: string): value is AssistantWorkspaceEventType {
  return ASSISTANT_WORKSPACE_EVENT_TYPES.includes(value as AssistantWorkspaceEventType);
}
