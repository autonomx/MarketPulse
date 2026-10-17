import { describe, expect, it } from 'vitest';

import { INITIAL_LIVE_CONVERSATION_STATE, reduceLiveConversationState } from './live-conversation-state';
import { deriveAvatarPresenceCue } from './live-avatar-presence';

const restrainedPlan = {
  schema_version: 1 as const,
  speech_act: 'reassurance' as const,
  energy: 'low' as const,
  warmth: 'high' as const,
  certainty: 'moderate' as const,
  pace: 'slightly_slow' as const,
  clause_pause: 'long' as const,
  emphasis: [],
  onset_policy: {
    desired_perceived_onset_ms: 650,
    maximum_additional_delay_ms: 350,
  },
  nonverbal_eligibility: {
    breath: true,
    acknowledgement: true,
    amused_exhale: false,
    sigh: true,
  },
};

describe('deriveAvatarPresenceCue', () => {
  it('derives listening, thinking, speaking, and yielding from shared state', () => {
    let state = reduceLiveConversationState(INITIAL_LIVE_CONVERSATION_STATE, { type: 'connection', value: 'connected' });
    state = reduceLiveConversationState(state, { type: 'floor_owner', value: 'user' });
    state = reduceLiveConversationState(state, { type: 'user_turn', value: 'speaking' });
    expect(deriveAvatarPresenceCue(state)).toBe('listening');

    state = reduceLiveConversationState(state, { type: 'floor_owner', value: 'unclaimed' });
    state = reduceLiveConversationState(state, { type: 'user_turn', value: 'listening' });
    state = reduceLiveConversationState(state, { type: 'assistant_turn', value: 'generating' });
    expect(deriveAvatarPresenceCue(state)).toBe('thinking');

    state = reduceLiveConversationState(state, { type: 'assistant_turn', value: 'speaking' });
    expect(deriveAvatarPresenceCue(state)).toBe('speaking');

    state = reduceLiveConversationState(state, { type: 'barge_in', value: 'accepted' });
    expect(deriveAvatarPresenceCue(state)).toBe('yielding');
  });

  it('uses restrained motion for serious warm delivery', () => {
    let state = reduceLiveConversationState(INITIAL_LIVE_CONVERSATION_STATE, { type: 'connection', value: 'connected' });
    state = reduceLiveConversationState(state, { type: 'assistant_turn', value: 'speaking' });
    expect(deriveAvatarPresenceCue(state, restrainedPlan)).toBe('restrained');
  });
});
