 
import type { LiveConversationProfile } from '../chat/liveConversationProfileClient';
import type { SpeechPerformancePlan } from './live-speech-performance-contract';
import { ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, emitOmnixEvent, LIVE_CHAT_SESSION_CHANGED_EVENT, LIVE_VOICE_PERFORMANCE_BEHAVIOR_EVENT } from '../../../events/bus';

const STATE_HALF_LIFE_MS = 45_000;
const HABIT_COOLDOWN_OBSERVATIONS = 3;
const MAX_STATE_SCOPES = 32;

const REFLECTION_PATTERN = /\b(?:i think|let me think|on balance|the tradeoff|looking at this|after considering|my read is)\b/i;
const EXPLICIT_CORRECTION_PATTERN = /(?:^|[\s,—-])(?:rather|more precisely|i mean|correction)\b/i;
const ACTUALLY_REVISION_PATTERN = /\bactually\b.{0,100}\b(?:not|instead|rather|meant|should be|more accurate)\b|\b(?:not|instead|rather|meant)\b.{0,100}\bactually\b/i;
const UNCERTAINTY_PATTERN = /\b(?:maybe|perhaps|likely|possibly|might|could|not sure|uncertain|my best estimate)\b/i;
const PLAYFUL_PATTERN = /\b(?:funny|amusing|hilarious|delightful|nice one|good one|made me laugh)\b/i;
const SERIOUS_PATTERN = /\b(?:sorry|grief|loss|afraid|hurt|difficult|serious|take your time)\b/i;

export type VocalHabit =
  | 'none'
  | 'brief_reflection'
  | 'direct_opening'
  | 'gentle_acknowledgement'
  | 'playful_reaction';

export type MeaningfulPerformanceBehavior = {
  reflective: boolean;
  genuineSelfCorrection: boolean;
  calibratedUncertainty: boolean;
  playful: boolean;
  habit: VocalHabit;
};

export type VocalInteractionState = {
  warmth: number;
  energy: number;
  tension: number;
  playfulness: number;
  uncertainty: number;
  observationCount: number;
  lastHabitObservation: number;
  updatedAtMs: number;
};

export type HumanizedPerformanceResult = {
  plan: SpeechPerformancePlan;
  state: VocalInteractionState;
  behavior: MeaningfulPerformanceBehavior;
};

const scopedStates = new Map<string, VocalInteractionState>();
let resetListenersInstalled = false;

export function createVocalInteractionState(now = Date.now()): VocalInteractionState {
  return {
    warmth: 0.5,
    energy: 0.5,
    tension: 0,
    playfulness: 0,
    uncertainty: 0,
    observationCount: 0,
    lastHabitObservation: -HABIT_COOLDOWN_OBSERVATIONS,
    updatedAtMs: now,
  };
}

export function planMeaningfulSpeechPerformance(
  text: string,
  plan: SpeechPerformancePlan,
  profile: LiveConversationProfile,
  previousState: VocalInteractionState,
  now = Date.now(),
): HumanizedPerformanceResult {
  const normalized = text.trim();
  const decayed = decayVocalInteractionState(previousState, now);
  const reflective = plan.speech_act === 'reflection' || REFLECTION_PATTERN.test(normalized);
  const correctionMarker = EXPLICIT_CORRECTION_PATTERN.test(normalized)
    || ACTUALLY_REVISION_PATTERN.test(normalized);
  const genuineSelfCorrection = previousState.observationCount > 0 && correctionMarker;
  const calibratedUncertainty = plan.certainty === 'low' || UNCERTAINTY_PATTERN.test(normalized);
  const playful = profile.emotional_attunement === 'expressive' && PLAYFUL_PATTERN.test(normalized);
  const serious = plan.speech_act === 'reassurance' || SERIOUS_PATTERN.test(normalized);
  const observationCount = previousState.observationCount + 1;
  const habit = selectVocalHabit(
    profile,
    plan,
    { reflective, genuineSelfCorrection, calibratedUncertainty, playful },
    observationCount,
    previousState.lastHabitObservation,
  );

  const state: VocalInteractionState = {
    warmth: blend(decayed.warmth, levelTarget(plan.warmth), serious ? 0.5 : 0.35),
    energy: blend(decayed.energy, levelTarget(plan.energy), 0.4),
    tension: blend(decayed.tension, serious ? 0.7 : 0.05, serious ? 0.45 : 0.25),
    playfulness: blend(decayed.playfulness, playful ? 0.75 : 0.05, playful ? 0.45 : 0.2),
    uncertainty: blend(decayed.uncertainty, calibratedUncertainty ? 0.7 : 0.05, 0.4),
    observationCount,
    lastHabitObservation: habit === 'none' ? previousState.lastHabitObservation : observationCount,
    updatedAtMs: now,
  };

  const behavior: MeaningfulPerformanceBehavior = {
    reflective,
    genuineSelfCorrection,
    calibratedUncertainty,
    playful,
    habit,
  };
  return {
    plan: applyBehaviorToPlan(plan, profile, state, behavior),
    state,
    behavior,
  };
}

export function humanizeSpeechPerformance(
  text: string,
  plan: SpeechPerformancePlan,
  profile: LiveConversationProfile,
  scopeOrNow: string | number = 'default',
  now = Date.now(),
): HumanizedPerformanceResult {
  installResetListeners();
  const scopeKey = normalizeScopeKey(typeof scopeOrNow === 'string' ? scopeOrNow : 'default');
  const observedAt = typeof scopeOrNow === 'number' ? scopeOrNow : now;
  const previous = scopedStates.get(scopeKey) ?? createVocalInteractionState(observedAt);
  const result = planMeaningfulSpeechPerformance(text, plan, profile, previous, observedAt);
  storeScopedState(scopeKey, result.state);
  if (typeof window !== 'undefined') {
    emitOmnixEvent(LIVE_VOICE_PERFORMANCE_BEHAVIOR_EVENT, {
        scope_key: scopeKey,
        reflective: result.behavior.reflective,
        genuine_self_correction: result.behavior.genuineSelfCorrection,
        calibrated_uncertainty: result.behavior.calibratedUncertainty,
        playful: result.behavior.playful,
        habit: result.behavior.habit,
        observation_count: result.state.observationCount,
        warmth: result.state.warmth,
        energy: result.state.energy,
        tension: result.state.tension,
        playfulness: result.state.playfulness,
        uncertainty: result.state.uncertainty,
        canonical_text_modified: false,
      });
  }
  return result;
}

export function readVocalInteractionState(scopeKey = 'default'): VocalInteractionState {
  return {
    ...(scopedStates.get(normalizeScopeKey(scopeKey)) ?? createVocalInteractionState()),
  };
}

export function resetVocalInteractionState(scopeKey?: string, now = Date.now()): void {
  if (scopeKey === undefined) {
    scopedStates.clear();
    return;
  }
  scopedStates.set(normalizeScopeKey(scopeKey), createVocalInteractionState(now));
}

export function decayVocalInteractionState(
  state: VocalInteractionState,
  now = Date.now(),
): VocalInteractionState {
  const elapsedMs = Math.max(0, now - state.updatedAtMs);
  const retention = Math.pow(0.5, elapsedMs / STATE_HALF_LIFE_MS);
  return {
    ...state,
    warmth: decayToward(state.warmth, 0.5, retention),
    energy: decayToward(state.energy, 0.5, retention),
    tension: decayToward(state.tension, 0, retention),
    playfulness: decayToward(state.playfulness, 0, retention),
    uncertainty: decayToward(state.uncertainty, 0, retention),
    updatedAtMs: now,
  };
}

function applyBehaviorToPlan(
  original: SpeechPerformancePlan,
  profile: LiveConversationProfile,
  state: VocalInteractionState,
  behavior: MeaningfulPerformanceBehavior,
): SpeechPerformancePlan {
  const plan: SpeechPerformancePlan = {
    ...original,
    emphasis: [...original.emphasis],
    onset_policy: { ...original.onset_policy },
    nonverbal_eligibility: { ...original.nonverbal_eligibility },
  };

  if (behavior.reflective) {
    plan.pace = 'slightly_slow';
    plan.clause_pause = 'long';
    plan.onset_policy.desired_perceived_onset_ms = Math.max(
      plan.onset_policy.desired_perceived_onset_ms,
      650,
    );
  }
  if (behavior.genuineSelfCorrection) {
    plan.pace = 'slightly_slow';
    plan.clause_pause = 'long';
    if (plan.certainty === 'high') plan.certainty = 'moderate';
  }
  if (behavior.calibratedUncertainty) {
    plan.certainty = 'low';
    if (plan.pace === 'slightly_fast') plan.pace = 'natural';
    if (plan.clause_pause === 'short') plan.clause_pause = 'medium';
  }
  if (profile.emotional_attunement !== 'off') {
    if (state.warmth >= 0.66) plan.warmth = 'high';
    if (state.energy <= 0.34) plan.energy = 'low';
    if (state.energy >= 0.7) plan.energy = 'high';
  }

  switch (behavior.habit) {
    case 'brief_reflection':
      plan.pace = 'slightly_slow';
      plan.clause_pause = 'long';
      plan.onset_policy.desired_perceived_onset_ms = Math.max(
        plan.onset_policy.desired_perceived_onset_ms,
        600,
      );
      plan.nonverbal_eligibility.acknowledgement = true;
      break;
    case 'direct_opening':
      plan.onset_policy.desired_perceived_onset_ms = Math.min(
        plan.onset_policy.desired_perceived_onset_ms,
        320,
      );
      plan.onset_policy.maximum_additional_delay_ms = Math.min(
        plan.onset_policy.maximum_additional_delay_ms,
        180,
      );
      break;
    case 'gentle_acknowledgement':
      plan.warmth = 'high';
      plan.energy = 'low';
      plan.pace = 'slightly_slow';
      plan.clause_pause = 'long';
      plan.nonverbal_eligibility.acknowledgement = true;
      break;
    case 'playful_reaction':
      plan.warmth = 'high';
      plan.energy = 'high';
      plan.nonverbal_eligibility.amused_exhale = true;
      break;
    case 'none':
      break;
  }
  return plan;
}

function selectVocalHabit(
  profile: LiveConversationProfile,
  plan: SpeechPerformancePlan,
  behavior: Omit<MeaningfulPerformanceBehavior, 'habit'>,
  observationCount: number,
  lastHabitObservation: number,
): VocalHabit {
  if (observationCount - lastHabitObservation < HABIT_COOLDOWN_OBSERVATIONS) return 'none';
  if (
    profile.emotional_attunement === 'expressive'
    && behavior.playful
    && plan.speech_act !== 'reassurance'
  ) return 'playful_reaction';
  if (
    (profile.presence_preset === 'listener' || profile.conversation_stance === 'listen')
    && (plan.speech_act === 'reflection' || plan.speech_act === 'reassurance')
  ) return 'gentle_acknowledgement';
  if (profile.conversation_pace === 'reflective' && behavior.reflective) return 'brief_reflection';
  if (
    (profile.conversation_stance === 'advise' || profile.conversation_stance === 'teach')
    && plan.speech_act === 'instruction'
    && !behavior.calibratedUncertainty
  ) return 'direct_opening';
  return 'none';
}

function installResetListeners(): void {
  if (resetListenersInstalled || typeof window === 'undefined') return;
  resetListenersInstalled = true;
  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, () => resetVocalInteractionState());
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, () => resetVocalInteractionState());
  window.addEventListener(LIVE_CHAT_SESSION_CHANGED_EVENT, () => resetVocalInteractionState());
}

function storeScopedState(scopeKey: string, state: VocalInteractionState): void {
  if (!scopedStates.has(scopeKey) && scopedStates.size >= MAX_STATE_SCOPES) {
    const oldest = [...scopedStates.entries()].sort(
      (left, right) => left[1].updatedAtMs - right[1].updatedAtMs,
    )[0]?.[0];
    if (oldest) scopedStates.delete(oldest);
  }
  scopedStates.set(scopeKey, state);
}

function normalizeScopeKey(scopeKey: string): string {
  const normalized = scopeKey.trim();
  return normalized ? normalized.slice(0, 160) : 'default';
}

function levelTarget(level: SpeechPerformancePlan['warmth'] | SpeechPerformancePlan['energy']): number {
  return level === 'high' ? 0.8 : level === 'low' ? 0.2 : 0.5;
}

function blend(current: number, target: number, weight: number): number {
  return clamp(current + ((target - current) * weight));
}

function decayToward(value: number, target: number, retention: number): number {
  return clamp(target + ((value - target) * retention));
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, Number(value.toFixed(4))));
}
