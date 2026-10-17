import type { AssistantWorkspaceEvent } from './events';
import type { ConversationTurnRole } from './conversation';

export type ChatbotActivityMessage = {
  id: string;
  role: 'system' | 'user' | 'assistant';
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type ChatbotActivitySession = {
  id: string;
  messages?: ChatbotActivityMessage[];
};

export const CHATBOT_ACTIVITY_MESSAGE_LIMIT = 200;

export type ChatbotActivityEventOptions = {
  workspaceId: string;
  projectId?: string;
};

export type ChatbotFailureEventOptions = ChatbotActivityEventOptions & {
  sessionId?: string;
  providerId?: string;
  modelId?: string;
  message: string;
  statusCode?: number;
  submittedContent?: string;
  createdAt: string;
};

export function createChatbotActivityEvents(
  session: ChatbotActivitySession | undefined,
  options: ChatbotActivityEventOptions,
): AssistantWorkspaceEvent[] {
  if (!session?.messages?.length) return [];

  // Activity covers the recent conversation; long transcripts would otherwise copy
  // every message into the event store.
  return session.messages
    .slice(-CHATBOT_ACTIVITY_MESSAGE_LIMIT)
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .map((message) => createMessageEvent(session.id, message, options));
}

export function createChatbotFailureEvent(options: ChatbotFailureEventOptions): AssistantWorkspaceEvent {
  const event = {
    id: createChatbotFailureEventId(options),
    type: 'operation_failed',
    workspaceId: options.workspaceId,
    ...(options.projectId ? { projectId: options.projectId } : {}),
    ...(options.sessionId ? { sessionId: options.sessionId } : {}),
    payload: {
      operation: 'chat_request',
      message: options.message,
      ...(options.statusCode ? { statusCode: options.statusCode } : {}),
      ...(options.providerId ? { providerId: options.providerId } : {}),
      ...(options.modelId ? { modelId: options.modelId } : {}),
      recoverable: true,
      details: {
        ...(options.submittedContent ? { submittedContent: options.submittedContent } : {}),
      },
    },
    createdAt: options.createdAt,
  } as const;

  return event as AssistantWorkspaceEvent;
}

function createMessageEvent(
  sessionId: string,
  message: ChatbotActivityMessage,
  options: ChatbotActivityEventOptions,
): AssistantWorkspaceEvent {
  const base = {
    id: createChatbotActivityEventId(sessionId, message),
    workspaceId: options.workspaceId,
    sessionId,
    createdAt: message.created_at,
    payload: {
      turn: {
        id: message.id,
        sessionId,
        role: message.role as ConversationTurnRole,
        content: [{ kind: 'text' as const, text: message.content }],
        metadata: normalizeMessageMetadata(message.metadata),
        createdAt: message.created_at,
      },
    },
  };

  const event = {
    ...base,
    ...(options.projectId ? { projectId: options.projectId } : {}),
    type: message.role === 'user' ? 'user_message' : 'assistant_message',
  } as const;

  return event as AssistantWorkspaceEvent;
}

function createChatbotActivityEventId(sessionId: string, message: ChatbotActivityMessage): string {
  return `chatbot:${sessionId}:${message.id}:${message.role}`;
}

function createChatbotFailureEventId(options: ChatbotFailureEventOptions): string {
  const scope = options.sessionId ?? 'workspace';
  const provider = options.providerId ?? 'default-provider';
  const model = options.modelId ?? 'default-model';
  const status = options.statusCode ?? 'error';
  const submitted = options.submittedContent ? normalizeEventIdSegment(options.submittedContent) : 'no-content';

  return `chatbot:${scope}:failure:${normalizeEventIdSegment(provider)}:${normalizeEventIdSegment(model)}:${status}:${submitted}`;
}

function normalizeMessageMetadata(metadata: Record<string, unknown> | undefined) {
  return metadata ? { ...metadata } : {};
}

function normalizeEventIdSegment(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9:-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);

  return normalized || 'unknown';
}
