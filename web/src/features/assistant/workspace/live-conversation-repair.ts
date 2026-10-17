import type { components } from '../api/generated';
export type LiveConversationRepairKind =
  | 'acknowledge_correction'
  | 'clarify_number'
  | 'clarify_name'
  | 'yield_to_user'
  | 'resume_interrupted_thought';

export type LiveConversationRepairContext = components['schemas']['LiveConversationRepairContext'];

export type RepairPlanInput = {
  transcript: string;
  overlapIntent?: string | null;
  overlapReason?: string | null;
  confidence?: number | null;
  assistantWasInterrupted?: boolean;
};

const NUMBER_AMBIGUITY = /\b(?:did you say|was that|is that)\b.*\b(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|fifty|thirteen|thirty)\b/i;
const NAME_AMBIGUITY = /\b(?:did you say|is it|was that|how do you spell|what was the name)\b.*\b(?:name|called|spelled|pronounced)\b/i;
const CORRECTION = /\b(?:actually|correction|i meant|no[, ]|that's wrong|that is wrong|not that)\b/i;
const HARD_STOP = /^(?:stop|wait|hold on|pause|quiet|let me speak)[.!?\s-]*$/i;
const CONTINUER = /^(?:m+h+m+|mhm+|uh[ -]?huh|yeah|yep|right|okay|ok|got it|sure|i see|mm+)[.!\s-]*$/i;

export function planConversationRepair(input: RepairPlanInput): LiveConversationRepairContext | null {
  const text = input.transcript.trim();
  const confidence = clampConfidence(input.confidence);
  if (!text || CONTINUER.test(text)) return null;
  if (NUMBER_AMBIGUITY.test(text)) {
    return repair(
      'clarify_number',
      'Briefly clarify the uncertain number before continuing. Offer the two plausible interpretations when available instead of guessing.',
      input.overlapReason || 'number_ambiguity',
      confidence,
    );
  }
  if (NAME_AMBIGUITY.test(text)) {
    return repair(
      'clarify_name',
      'Briefly confirm the uncertain name or pronunciation before continuing. Do not invent a spelling or pronunciation.',
      input.overlapReason || 'name_ambiguity',
      confidence,
    );
  }
  if (input.overlapReason === 'correction' || CORRECTION.test(text)) {
    return repair(
      'acknowledge_correction',
      'Acknowledge the correction in one short clause, adopt the corrected information, and continue without defensiveness or repeating the full prior answer.',
      input.overlapReason || 'user_correction',
      confidence,
    );
  }
  if (input.overlapIntent === 'hard_stop' || HARD_STOP.test(text)) {
    return repair(
      'yield_to_user',
      'Yield the floor immediately. Respond only after the user finishes, briefly acknowledge that they wanted to speak, and do not resume the interrupted answer unless invited.',
      input.overlapReason || 'explicit_stop',
      confidence,
    );
  }
  if (input.assistantWasInterrupted && input.overlapIntent === 'interrupt') {
    return repair(
      'resume_interrupted_thought',
      'Address the interruption first. Resume only the undelivered part of the prior thought when it remains relevant; do not restart the answer from the beginning.',
      input.overlapReason || 'assistant_interrupted',
      confidence,
    );
  }
  return null;
}

function repair(
  kind: LiveConversationRepairKind,
  instruction: string,
  source_reason: string,
  confidence: number,
): LiveConversationRepairContext {
  return { kind, instruction, source_reason, confidence };
}

function clampConfidence(value: number | null | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1;
}
