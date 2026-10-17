import { describe, expect, it } from 'vitest';

import type { PresencePolicyVersion } from './live-chat-evaluation-client';
import {
  INITIAL_LIVE_CONVERSATION_RUNTIME_STATE,
  createLiveConversationStore,
  replayLiveConversationActions,
  selectLiveChatSnapshot,
  type LiveConversationStoreAction,
} from './live-conversation-store';

const policy: PresencePolicyVersion = {
  preset: 'natural',
  version: 2,
  values: {
    silence_tolerance_ms: 16_000,
    initiative_threshold_ms: 20_000,
    initiative_cooldown_ms: 50_000,
    listener_backchannel_frequency: 0.14,
    typical_turn_words: 65,
    interruption_sensitivity: 0.74,
    response_onset_ms: 450,
  },
  reason: 'evidence-driven',
  evidence_evaluation_ids: ['evaluation-one'],
  active: true,
  created_at: '2026-07-11T00:00:00+00:00',
};

const replay: LiveConversationStoreAction[] = [
  { type: 'session', sessionId: 'chat:maya' },
  { type: 'identity', identity: { characterId: 'maya', displayName: 'Maya', profileVersion: 4 } },
  { type: 'conversation', event: { type: 'connection', value: 'connected' } },
  { type: 'conversation', event: { type: 'user_turn', value: 'speaking' } },
  { type: 'conversation', event: { type: 'floor_owner', value: 'user' } },
  { type: 'transcript_partial', text: 'I was thinking' },
  { type: 'transcript_final', text: 'I was thinking about the launch.' },
  { type: 'conversation', event: { type: 'user_turn', value: 'finalizing' } },
  { type: 'conversation', event: { type: 'assistant_turn', value: 'generating' } },
  { type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } },
  { type: 'conversation', event: { type: 'delivery', value: 'audio_started' } },
  { type: 'conversation', event: { type: 'floor_owner', value: 'assistant' } },
];

describe('live conversation store', () => {
  it('replays the same timestamp-ordered action stream deterministically', () => {
    const first = replayLiveConversationActions(replay);
    const second = replayLiveConversationActions(replay);

    expect(first).toEqual(second);
    expect(first.sessionId).toBe('chat:maya');
    expect(first.identity.displayName).toBe('Maya');
    expect(first.transcript.recentFinals).toEqual(['I was thinking about the launch.']);
    expect(selectLiveChatSnapshot(first)).toMatchObject({
      connected: true,
      identity: 'Maya',
      state: 'Maya is speaking',
      floorOwner: 'assistant',
    });
  });

  it('keeps orthogonal state domains independent and observable', () => {
    const store = createLiveConversationStore();
    let notifications = 0;
    const unsubscribe = store.subscribe(() => { notifications += 1; });

    store.dispatch({ type: 'duplex', duplex: { resolvedMode: 'echo_aware', reason: 'calibration_confident', confidence: 0.91 } });
    store.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'ducking' } });
    store.dispatch({ type: 'quality', summary: { perceivedListeningScore: 5 } });

    expect(store.getState().duplex.resolvedMode).toBe('echo_aware');
    expect(store.getState().conversation.bargeIn).toBe('ducking');
    expect(store.getState().qualitySummary).toEqual({ perceivedListeningScore: 5 });
    expect(store.getState().conversation.connection).toBe('disconnected');
    expect(notifications).toBe(3);
    unsubscribe();
  });

  it('stores an active presence policy and clears it when the profile preset changes', () => {
    const store = createLiveConversationStore();
    store.dispatch({ type: 'presence_policy', policy });
    expect(selectLiveChatSnapshot(store.getState()).presencePolicyVersion).toBe(2);

    store.dispatch({ type: 'profile', profile: {
      presence_preset: 'quiet',
      talkativeness: 0.5,
      conversation_stance: 'automatic',
      conversation_pace: 'balanced',
      interruption_preference: 'balanced',
      assistant_backchannel_mode: 'natural',
      initiative_mode: 'gentle',
      idle_threshold_ms: 15_000,
      long_pause_behavior: 'wait',
      response_length: 'conversational',
      response_onset_style: 'adaptive',
      emotional_attunement: 'subtle',
      topic_continuity: 'natural',
      max_idle_prompts: 1,
      duplex_mode: 'automatic',
      pronunciation_save_policy: 'ask',
      profile_version: 1,
    } });

    expect(store.getState().presencePolicy).toBeNull();
  });

  it('resets conversation activity without losing session identity or calibration', () => {
    const state = replayLiveConversationActions([
      ...replay,
      { type: 'presence_policy', policy },
      { type: 'duplex', duplex: { resolvedMode: 'echo_aware', reason: 'calibration_confident', confidence: 0.9 } },
      { type: 'reset_conversation' },
    ]);

    expect(state.conversation).toEqual(INITIAL_LIVE_CONVERSATION_RUNTIME_STATE.conversation);
    expect(state.sessionId).toBe('chat:maya');
    expect(state.identity.displayName).toBe('Maya');
    expect(state.duplex.resolvedMode).toBe('echo_aware');
    expect(state.presencePolicy?.version).toBe(2);
  });
});
