 
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LiveConversationProfile } from '../chat/liveConversationProfileClient';
import { liveConversationStore } from './live-conversation-store';
import {
  initializeLivePresencePolicyController,
  refreshPresencePolicies,
} from './live-presence-policy-controller';

const profile: LiveConversationProfile = {
  presence_preset: 'natural',
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
};

const activePolicy = {
  preset: 'natural',
  version: 3,
  values: {
    silence_tolerance_ms: 17_000,
    initiative_threshold_ms: 22_000,
    initiative_cooldown_ms: 55_000,
    listener_backchannel_frequency: 0.12,
    typical_turn_words: 60,
    interruption_sensitivity: 0.78,
    response_onset_ms: 500,
  },
  reason: 'operator-approved',
  evidence_evaluation_ids: ['evaluation-one'],
  active: true,
  created_at: '2026-07-11T00:00:00+00:00',
};

let cleanup: (() => void) | null = null;

describe('live presence policy controller', () => {
  beforeEach(() => {
    liveConversationStore.reset();
    liveConversationStore.dispatch({ type: 'profile', profile });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ natural: activePolicy }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));
  });

  afterEach(() => {
    cleanup?.();
    cleanup = null;
    vi.unstubAllGlobals();
    liveConversationStore.reset();
  });

  it('loads the active server policy for the selected presence preset', async () => {
    cleanup = initializeLivePresencePolicyController();
    await refreshPresencePolicies();

    expect(liveConversationStore.getState().presencePolicy).toMatchObject({
      preset: 'natural',
      version: 3,
      active: true,
    });
  });

  it('clears the policy when the profile switches to an unavailable preset', async () => {
    cleanup = initializeLivePresencePolicyController();
    await refreshPresencePolicies();
    liveConversationStore.dispatch({ type: 'profile', profile: { ...profile, presence_preset: 'quiet' } });

    expect(liveConversationStore.getState().presencePolicy).toBeNull();
  });
});
