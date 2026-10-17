import { describe, expect, it } from 'vitest';

import {
  INITIAL_LIVE_CONVERSATION_STATE,
  deriveLiveConversationStatus,
  projectLegacyLiveVoiceState,
  reduceLiveConversationState,
} from './live-conversation-state';

describe('live conversation state', () => {
  it('updates orthogonal domains without overwriting the user floor', () => {
    const speaking = reduceLiveConversationState(INITIAL_LIVE_CONVERSATION_STATE, { type: 'user_turn', value: 'speaking' });
    const generating = reduceLiveConversationState(speaking, { type: 'assistant_turn', value: 'generating' });
    const considering = reduceLiveConversationState(generating, { type: 'initiative', value: 'considering' });

    expect(considering.userTurn).toBe('speaking');
    expect(considering.assistantTurn).toBe('generating');
    expect(considering.initiative).toBe('considering');
  });

  it('derives user-facing status from authoritative state', () => {
    let state = reduceLiveConversationState(INITIAL_LIVE_CONVERSATION_STATE, { type: 'connection', value: 'connected' });
    state = reduceLiveConversationState(state, { type: 'assistant_turn', value: 'generating' });
    expect(deriveLiveConversationStatus(state, 'Maya')).toBe('Maya is thinking');

    state = reduceLiveConversationState(state, { type: 'user_turn', value: 'speaking' });
    state = reduceLiveConversationState(state, { type: 'floor_owner', value: 'user' });
    expect(deriveLiveConversationStatus(state, 'Maya')).toBe('Maya is listening');
  });

  it('projects legacy live-call labels during staged migration', () => {
    expect(projectLegacyLiveVoiceState(false, 'Idle').connection).toBe('disconnected');
    const assistantSpeaking = projectLegacyLiveVoiceState(true, 'Speaking');
    expect(assistantSpeaking.assistantTurn).toBe('speaking');
    expect(assistantSpeaking.floorOwner).toBe('assistant');

    const userSpeaking = projectLegacyLiveVoiceState(true, 'User speaking');
    expect(userSpeaking.userTurn).toBe('speaking');
    expect(userSpeaking.floorOwner).toBe('user');
    expect(userSpeaking.assistantTurn).toBe('idle');
  });
});
