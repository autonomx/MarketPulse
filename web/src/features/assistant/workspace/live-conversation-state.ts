import type { UserFloorState } from './live-voice-floor-manager';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'stopping';
export type FloorOwner = 'user' | 'assistant' | 'shared' | 'unclaimed';
export type AssistantTurnState = 'idle' | 'planning' | 'generating' | 'queued' | 'speaking' | 'interrupted';
export type SocialInitiativeState = 'inactive' | 'eligible' | 'considering' | 'prompting' | 'cooldown' | 'suppressed';
export type DeliveryState = 'generated' | 'visual_started' | 'audio_started' | 'completed' | 'interrupted';
export type BargeInState = 'inactive' | 'ducking' | 'confirming' | 'accepted' | 'rejected';

export type LiveConversationState = {
  connection: ConnectionState;
  floorOwner: FloorOwner;
  userTurn: UserFloorState;
  assistantTurn: AssistantTurnState;
  initiative: SocialInitiativeState;
  delivery: DeliveryState;
  bargeIn: BargeInState;
};

export type LiveConversationStateEvent =
  | { type: 'connection'; value: ConnectionState }
  | { type: 'floor_owner'; value: FloorOwner }
  | { type: 'user_turn'; value: UserFloorState }
  | { type: 'assistant_turn'; value: AssistantTurnState }
  | { type: 'initiative'; value: SocialInitiativeState }
  | { type: 'delivery'; value: DeliveryState }
  | { type: 'barge_in'; value: BargeInState }
  | { type: 'reset' };

export const INITIAL_LIVE_CONVERSATION_STATE: LiveConversationState = {
  connection: 'disconnected',
  floorOwner: 'unclaimed',
  userTurn: 'idle',
  assistantTurn: 'idle',
  initiative: 'inactive',
  delivery: 'completed',
  bargeIn: 'inactive',
};

export function reduceLiveConversationState(
  state: LiveConversationState,
  event: LiveConversationStateEvent,
): LiveConversationState {
  switch (event.type) {
    case 'connection': return { ...state, connection: event.value };
    case 'floor_owner': return { ...state, floorOwner: event.value };
    case 'user_turn': return { ...state, userTurn: event.value };
    case 'assistant_turn': return { ...state, assistantTurn: event.value };
    case 'initiative': return { ...state, initiative: event.value };
    case 'delivery': return { ...state, delivery: event.value };
    case 'barge_in': return { ...state, bargeIn: event.value };
    case 'reset': return INITIAL_LIVE_CONVERSATION_STATE;
    default: return state;
  }
}

export function deriveLiveConversationStatus(state: LiveConversationState, characterName = 'Assistant'): string {
  if (state.connection === 'disconnected') return 'Call idle';
  if (state.connection === 'connecting') return 'Connecting';
  if (state.connection === 'stopping') return 'Ending call';
  if (state.bargeIn === 'ducking' || state.bargeIn === 'confirming') return 'Checking interruption';
  if (state.bargeIn === 'accepted' || state.assistantTurn === 'interrupted') return `${characterName} is yielding`;
  if (state.assistantTurn === 'speaking' || state.delivery === 'audio_started' || state.floorOwner === 'assistant') return `${characterName} is speaking`;
  if (state.userTurn === 'paused' || state.userTurn === 'completion_pending' || state.userTurn === 'finalizing') return 'Waiting for you';
  if (state.userTurn === 'speaking' || state.userTurn === 'speech_candidate' || state.floorOwner === 'user') return `${characterName} is listening`;
  if (state.assistantTurn === 'planning' || state.assistantTurn === 'generating') return `${characterName} is thinking`;
  if (state.assistantTurn === 'queued') return `${characterName} is preparing to speak`;
  if (state.initiative === 'considering') return `${characterName} is considering a follow-up`;
  if (state.initiative === 'suppressed') return 'Listening quietly';
  return 'Listening';
}

export function projectLegacyLiveVoiceState(connected: boolean, legacyState: string): LiveConversationState {
  if (!connected) return INITIAL_LIVE_CONVERSATION_STATE;
  const normalized = legacyState.trim().toLocaleLowerCase();
  let state = reduceLiveConversationState(INITIAL_LIVE_CONVERSATION_STATE, { type: 'connection', value: 'connected' });
  if (normalized.includes('user speaking') || normalized.includes('hearing speech')) {
    state = reduceLiveConversationState(state, { type: 'user_turn', value: 'speaking' });
    return reduceLiveConversationState(state, { type: 'floor_owner', value: 'user' });
  }
  if (normalized.includes('speaking') || normalized.includes('playing')) {
    state = reduceLiveConversationState(state, { type: 'assistant_turn', value: 'speaking' });
    state = reduceLiveConversationState(state, { type: 'delivery', value: 'audio_started' });
    return reduceLiveConversationState(state, { type: 'floor_owner', value: 'assistant' });
  }
  if (normalized.includes('generating') || normalized.includes('thinking')) {
    return reduceLiveConversationState(state, { type: 'assistant_turn', value: 'generating' });
  }
  state = reduceLiveConversationState(state, { type: 'user_turn', value: 'listening' });
  return reduceLiveConversationState(state, { type: 'floor_owner', value: 'unclaimed' });
}
