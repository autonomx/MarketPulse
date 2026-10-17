import type { WorkspaceChatSession } from './domain';

export const CONVERSATION_TURN_ROLES = ['user', 'assistant', 'tool', 'system'] as const;

export type ConversationTurnRole = (typeof CONVERSATION_TURN_ROLES)[number];

export type MessageContent =
  | { kind: 'text'; text: string }
  | { kind: 'status'; text: string };

export type TokenUsage = {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
};

export type ConversationTurnMetadata = {
  provider?: string;
  model?: string;
  assistantIdentityId?: string;
  latencyMs?: number;
  tokenUsage?: TokenUsage;
  voiceSessionId?: string;
};

export type ConversationTurn = {
  id: string;
  sessionId: string;
  role: ConversationTurnRole;
  content: MessageContent[];
  metadata: ConversationTurnMetadata;
  createdAt: string;
};

export type ConversationState = {
  session: WorkspaceChatSession;
  turns: ConversationTurn[];
};

export function isConversationTurnRole(value: string): value is ConversationTurnRole {
  return CONVERSATION_TURN_ROLES.includes(value as ConversationTurnRole);
}

export function createConversationTurn(turn: ConversationTurn): ConversationTurn {
  return { ...turn, metadata: turn.metadata ?? {} };
}

export function appendConversationTurn(state: ConversationState, turn: ConversationTurn): ConversationState {
  if (turn.sessionId !== state.session.id) {
    return state;
  }
  return { ...state, turns: [...state.turns, turn] };
}

export function getLatestConversationTurn(state: ConversationState): ConversationTurn | undefined {
  return state.turns.at(-1);
}
