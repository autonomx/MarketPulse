import {
  liveSttUsesAuthoritativeEou,
  liveSttUsesFinalOnlyEndpointing,
} from './live-stt-capability-state';
import { readAssistantTurnCompletionContext } from './live-turn-context';

export type ConversationPace = 'quick' | 'balanced' | 'reflective';

export type UserFloorState =
  | 'idle'
  | 'listening'
  | 'speech_candidate'
  | 'speaking'
  | 'paused'
  | 'completion_pending'
  | 'finalizing'
  | 'overlap_candidate';

export type SemanticTurnReason =
  | 'definitive_statement'
  | 'complete_question'
  | 'complete_command'
  | 'contextual_short_answer'
  | 'trailing_hesitation'
  | 'unfinished_clause'
  | 'self_correction'
  | 'enumeration_in_progress'
  | 'insufficient_text'
  | 'timeout';

export type SemanticTurnAssessment = {
  probabilityDone: number;
  reason: SemanticTurnReason;
  recommendedWaitMs: number;
};

export type FloorTimingProfile = {
  minimumPauseMs: number;
  clearTurnWaitMs: number;
  ambiguousWaitMs: number;
  maximumWaitMs: number;
};

export type UserFloorEvent =
  | { type: 'listen' }
  | { type: 'speech_candidate' }
  | { type: 'speech_confirmed'; assistantSpeaking: boolean }
  | { type: 'pause' }
  | { type: 'resume' }
  | { type: 'completion_check' }
  | { type: 'commit' }
  | { type: 'reset' };

// Clear questions and commands use a deliberately narrow fast path. Ambiguous
// clauses retain substantially longer waits, so the latency improvement does
// not come from globally shortening the user's floor.
const PROFILES: Record<ConversationPace, FloorTimingProfile> = {
  quick: { minimumPauseMs: 160, clearTurnWaitMs: 180, ambiguousWaitMs: 650, maximumWaitMs: 1_050 },
  balanced: { minimumPauseMs: 180, clearTurnWaitMs: 220, ambiguousWaitMs: 1_000, maximumWaitMs: 1_700 },
  reflective: { minimumPauseMs: 450, clearTurnWaitMs: 650, ambiguousWaitMs: 1_800, maximumWaitMs: 2_800 },
};

// When a provider supplies a dedicated authoritative EOU signal, semantic text
// is no longer the primary turn-ending mechanism. This is only the watchdog if
// EOU is missed; explicit EOU commits immediately in the voice controller.
const AUTHORITATIVE_EOU_FALLBACK_MS: Record<ConversationPace, number> = {
  quick: 450,
  balanced: 500,
  reflective: 800,
};

// Final-only providers cannot supply the transcript needed by semantic EOT until
// after finalization has already been requested. Use a short acoustic fallback
// only when the negotiated capability set proves that no pre-final evidence is
// available. Streaming/semantic providers keep the normal semantic policy.
const FINAL_ONLY_ACOUSTIC_WAIT_MS: Record<ConversationPace, number> = {
  quick: 260,
  balanced: 350,
  reflective: 650,
};

const HESITATION_PATTERN = /(?:\b(?:um+|uh+|erm|hmm|let me think|one moment|give me a second)\b)[,.…\s-]*$/i;
const UNFINISHED_PATTERN = /(?:\b(?:and|or|but|because|so|then|if|when|while|with|to|from|about|like|that|which|who|first|second|also|what|why|where|how|is|are|was|were|do|does|did|can|could|would|should|will|have|has|had)\b|[,;:\-–—])\s*$/i;
const SELF_CORRECTION_PATTERN = /(?:\b(?:actually|rather|I mean|no wait|correction)\b)[,.…\s-]*$/i;
const ENUMERATION_PATTERN = /(?:\b(?:first|second|third|next)|\d+[.)])\s*$/i;
const COMPLETE_QUESTION_PATTERN = /[?？]\s*$/u;
const QUESTION_LEAD_PATTERN = /^(?:what|why|when|where|who|how|can|could|would|should|is|are|was|were|do|does|did|will|have|has|had)\b/i;
const COMPLETE_COMMAND_PATTERN = /^(?:please\s+)?(?:open|close|show|hide|start|stop|send|create|delete|save|load|continue|explain|tell|give|find|search|go|move|call|cancel)\b.+/i;
const CONTEXTUAL_SHORT_ANSWER_BLOCK_PATTERN = /^(?:and|or|but|because|so|then|if|when|while|with|to|from|about|like|that|which|who|what|why|where|how|is|are|was|were|do|does|did|can|could|would|should|will|have|has|had|i|we|they|he|she|it|the|a|an|actually|well|um+|uh+|erm|hmm)[,.…\s-]*$/i;

export function conversationTimingProfile(pace: ConversationPace): FloorTimingProfile {
  return PROFILES[pace];
}

export function reduceUserFloor(state: UserFloorState, event: UserFloorEvent): UserFloorState {
  switch (event.type) {
    case 'reset': return 'idle';
    case 'listen': return state === 'idle' ? 'listening' : state;
    case 'speech_candidate': return 'speech_candidate';
    case 'speech_confirmed': return event.assistantSpeaking ? 'overlap_candidate' : 'speaking';
    case 'pause': return state === 'speaking' || state === 'overlap_candidate' ? 'paused' : state;
    case 'resume': return 'speaking';
    case 'completion_check': return state === 'paused' ? 'completion_pending' : state;
    case 'commit': return 'listening';
    default: return state;
  }
}

export function assessSemanticTurn(
  transcript: string,
  pace: ConversationPace = 'balanced',
): SemanticTurnAssessment {
  const profile = conversationTimingProfile(pace);
  const text = transcript.trim();
  const wordCount = text ? text.split(/\s+/u).length : 0;
  if (wordCount === 0) {
    return { probabilityDone: 0.1, reason: 'insufficient_text', recommendedWaitMs: profile.maximumWaitMs };
  }
  if (wordCount === 1) {
    const context = readAssistantTurnCompletionContext(30_000);
    const expectsAnswer = Boolean(
      context && (context.questionCount > 0 || context.createsObligation),
    );
    if (expectsAnswer && !CONTEXTUAL_SHORT_ANSWER_BLOCK_PATTERN.test(text)) {
      return {
        probabilityDone: 0.96,
        reason: 'contextual_short_answer',
        recommendedWaitMs: profile.clearTurnWaitMs,
      };
    }
    return { probabilityDone: 0.1, reason: 'insufficient_text', recommendedWaitMs: profile.maximumWaitMs };
  }
  if (HESITATION_PATTERN.test(text)) {
    return { probabilityDone: 0.08, reason: 'trailing_hesitation', recommendedWaitMs: profile.maximumWaitMs };
  }
  if (SELF_CORRECTION_PATTERN.test(text)) {
    return { probabilityDone: 0.12, reason: 'self_correction', recommendedWaitMs: profile.maximumWaitMs };
  }
  if (ENUMERATION_PATTERN.test(text)) {
    return { probabilityDone: 0.2, reason: 'enumeration_in_progress', recommendedWaitMs: profile.ambiguousWaitMs };
  }
  if (UNFINISHED_PATTERN.test(text)) {
    return { probabilityDone: 0.18, reason: 'unfinished_clause', recommendedWaitMs: profile.ambiguousWaitMs };
  }
  if (COMPLETE_QUESTION_PATTERN.test(text)) {
    return { probabilityDone: 0.92, reason: 'complete_question', recommendedWaitMs: profile.clearTurnWaitMs };
  }
  const trailingClause = text.split(/[.!?？]+/u).filter(Boolean).at(-1)?.trim() ?? '';
  if (QUESTION_LEAD_PATTERN.test(trailingClause)) {
    return { probabilityDone: 0.35, reason: 'unfinished_clause', recommendedWaitMs: profile.ambiguousWaitMs };
  }
  if (COMPLETE_COMMAND_PATTERN.test(text)) {
    return { probabilityDone: 0.94, reason: 'complete_command', recommendedWaitMs: profile.clearTurnWaitMs };
  }
  return {
    probabilityDone: 0.78,
    reason: 'definitive_statement',
    recommendedWaitMs: definitiveStatementWaitMs(pace, profile),
  };
}

export function semanticFinalizeDelay(
  transcript: string,
  pace: ConversationPace = 'balanced',
): number {
  const profile = conversationTimingProfile(pace);
  if (liveSttUsesAuthoritativeEou()) {
    return authoritativeEouFallbackDelay(pace);
  }
  if (!transcript.trim() && liveSttUsesFinalOnlyEndpointing()) {
    return finalOnlyAcousticFinalizeDelay(pace);
  }
  const assessment = assessSemanticTurn(transcript, pace);
  return Math.min(
    profile.maximumWaitMs,
    Math.max(profile.minimumPauseMs, assessment.recommendedWaitMs),
  );
}

export function authoritativeEouFallbackDelay(pace: ConversationPace = 'balanced'): number {
  return AUTHORITATIVE_EOU_FALLBACK_MS[pace];
}

export function finalOnlyAcousticFinalizeDelay(pace: ConversationPace = 'balanced'): number {
  return FINAL_ONLY_ACOUSTIC_WAIT_MS[pace];
}

function definitiveStatementWaitMs(
  pace: ConversationPace,
  profile: FloorTimingProfile,
): number {
  if (pace === 'quick') return Math.max(profile.clearTurnWaitMs, 260);
  if (pace === 'balanced') return Math.max(profile.clearTurnWaitMs, 360);
  return profile.clearTurnWaitMs;
}
