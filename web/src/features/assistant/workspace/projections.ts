import type { WorkspaceChatSession } from './domain';
import type { ConversationState, ConversationTurn } from './conversation';
import type { AssistantWorkspaceEvent } from './events';

export type ConversationProjection = ConversationState & {
  events: AssistantWorkspaceEvent[];
};

export function createConversationProjection(session: WorkspaceChatSession): ConversationProjection {
  return {
    session,
    turns: [],
    events: [],
  };
}

export function appendProjectionEvent(
  projection: ConversationProjection,
  event: AssistantWorkspaceEvent,
): ConversationProjection {
  const nextTurns = [...projection.turns];

  if (
    (event.type === 'user_message' || event.type === 'assistant_message') &&
    event.sessionId === projection.session.id
  ) {
    const turn = event.payload.turn as ConversationTurn | undefined;
    if (turn && turn.sessionId === projection.session.id) {
      nextTurns.push(turn);
    }
  }

  return {
    ...projection,
    turns: nextTurns,
    events: [...projection.events, event],
  };
}

export function rebuildConversationProjection(
  session: WorkspaceChatSession,
  events: AssistantWorkspaceEvent[],
): ConversationProjection {
  return events.reduce(appendProjectionEvent, createConversationProjection(session));
}
