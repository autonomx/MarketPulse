 
import type { InitiativeMode } from '../chat/liveConversationProfileClient';

export type InitiativePolicyInput = {
  mode: InitiativeMode;
  callConnected: boolean;
  assistantActive: boolean;
  userSpeaking: boolean;
  partialTranscript: string;
  userRequestedTime: boolean;
  sensitiveDictation: boolean;
  tabVisible: boolean;
  muted: boolean;
  requestInFlight: boolean;
  hasMeaningfulReason: boolean;
  previousPromptIgnored: boolean;
  nowMs: number;
  lastActivityAtMs: number;
  lastPromptAtMs: number | null;
  idleThresholdMs: number;
  cooldownMs: number;
  promptCount: number;
  maxPrompts: number;
};

export type InitiativePolicyDecision = {
  action: 'speak' | 'wait' | 'suppress';
  reason: string;
  eligibleInMs: number | null;
};

export function decideInitiative(input: InitiativePolicyInput): InitiativePolicyDecision {
  if (input.mode === 'off') return suppress('initiative_disabled');
  if (!input.callConnected) return suppress('call_not_connected');
  if (!input.tabVisible) return suppress('tab_hidden');
  if (input.muted) return suppress('microphone_muted');
  if (input.userRequestedTime) return suppress('user_requested_time');
  if (input.sensitiveDictation) return suppress('sensitive_dictation');
  if (input.previousPromptIgnored) return suppress('previous_prompt_ignored');
  if (!input.hasMeaningfulReason) return suppress('no_meaningful_contribution');
  if (input.promptCount >= input.maxPrompts) return suppress('quiet_period_prompt_limit');
  if (input.assistantActive || input.requestInFlight) return wait('assistant_busy');
  if (input.userSpeaking || input.partialTranscript.trim()) return wait('user_floor_active');

  const idleRemaining = Math.max(0, input.idleThresholdMs - (input.nowMs - input.lastActivityAtMs));
  if (idleRemaining > 0) return wait('idle_threshold', idleRemaining);
  if (input.lastPromptAtMs !== null) {
    const cooldownRemaining = Math.max(0, input.cooldownMs - (input.nowMs - input.lastPromptAtMs));
    if (cooldownRemaining > 0) return wait('initiative_cooldown', cooldownRemaining);
  }
  return { action: 'speak', reason: 'social_obligation_available', eligibleInMs: 0 };
}

function wait(reason: string, eligibleInMs: number | null = null): InitiativePolicyDecision {
  return { action: 'wait', reason, eligibleInMs };
}

function suppress(reason: string): InitiativePolicyDecision {
  return { action: 'suppress', reason, eligibleInMs: null };
}
