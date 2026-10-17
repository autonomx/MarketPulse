import { describe, expect, it } from 'vitest';

import { decideInitiative, type InitiativePolicyInput } from './live-conversation-initiative-policy';

const base: InitiativePolicyInput = {
  mode: 'gentle',
  callConnected: true,
  assistantActive: false,
  userSpeaking: false,
  partialTranscript: '',
  userRequestedTime: false,
  sensitiveDictation: false,
  tabVisible: true,
  muted: false,
  requestInFlight: false,
  hasMeaningfulReason: true,
  previousPromptIgnored: false,
  nowMs: 20_000,
  lastActivityAtMs: 0,
  lastPromptAtMs: null,
  idleThresholdMs: 15_000,
  cooldownMs: 30_000,
  promptCount: 0,
  maxPrompts: 1,
};

describe('decideInitiative', () => {
  it('authorizes one meaningful move after genuine idle', () => {
    expect(decideInitiative(base)).toEqual({
      action: 'speak',
      reason: 'social_obligation_available',
      eligibleInMs: 0,
    });
  });

  it('waits while the user or assistant owns the floor', () => {
    expect(decideInitiative({ ...base, userSpeaking: true }).reason).toBe('user_floor_active');
    expect(decideInitiative({ ...base, partialTranscript: 'So I was thinking' }).reason).toBe('user_floor_active');
    expect(decideInitiative({ ...base, assistantActive: true }).reason).toBe('assistant_busy');
  });

  it('suppresses socially inappropriate prompts', () => {
    expect(decideInitiative({ ...base, userRequestedTime: true }).reason).toBe('user_requested_time');
    expect(decideInitiative({ ...base, sensitiveDictation: true }).reason).toBe('sensitive_dictation');
    expect(decideInitiative({ ...base, tabVisible: false }).reason).toBe('tab_hidden');
    expect(decideInitiative({ ...base, previousPromptIgnored: true }).reason).toBe('previous_prompt_ignored');
    expect(decideInitiative({ ...base, hasMeaningfulReason: false }).reason).toBe('no_meaningful_contribution');
  });

  it('enforces idle threshold, cooldown, and prompt limit', () => {
    expect(decideInitiative({ ...base, nowMs: 10_000 }).eligibleInMs).toBe(5_000);
    expect(decideInitiative({ ...base, lastPromptAtMs: 10_000 }).reason).toBe('initiative_cooldown');
    expect(decideInitiative({ ...base, promptCount: 1 }).reason).toBe('quiet_period_prompt_limit');
  });
});
