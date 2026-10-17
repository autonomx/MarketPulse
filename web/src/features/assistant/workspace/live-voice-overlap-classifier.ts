import './live-voice-backchannel';
import './live-voice-conversation-settings';

import {
  type InterruptionPreference,
  readLiveConversationSettings,
} from './live-voice-conversation-settings';
import { isPlaybackEchoSuppressed } from './live-voice-echo-suppression';

export type OverlapIntent = 'hard_stop' | 'interrupt' | 'backchannel' | 'noise' | 'uncertain';

export type OverlapAssessment = {
  intent: OverlapIntent;
  confidence: number;
  reason: string;
};

const HARD_STOP_PATTERN = /^(?:stop|wait|hold on|cancel|pause|quiet|be quiet|no|nope|that's wrong|that is wrong)[.!?\s-]*$/i;
const CORRECTION_PATTERN = /\b(?:no|not|wrong|actually|correction|I meant|you said|that's not|that is not)\b/i;
const QUESTION_PATTERN = /(?:[?？]|^(?:what|why|when|where|who|how|can|could|would|should|did|do|does|is|are)\b)/i;
const BACKCHANNEL_PATTERN = /^(?:m+h+m+|mhm+|uh[ -]?huh|yeah|yep|right|okay|ok|got it|sure|I see|mm+)[.!\s-]*$/i;
const NON_SPEECH_PATTERN = /^(?:\[?(?:noise|cough|breath|music|silence|background)\]?|[.!,…\s-]*)$/i;

export function classifyOverlap(
  transcript: string,
  assistantText = '',
): OverlapAssessment {
  const text = normalize(transcript);
  if (!text) return { intent: 'uncertain', confidence: 0.1, reason: 'no_stable_text' };
  if (NON_SPEECH_PATTERN.test(text)) return { intent: 'noise', confidence: 0.96, reason: 'non_speech_marker' };
  if (isLikelyEcho(text, assistantText)) return { intent: 'noise', confidence: 0.9, reason: 'assistant_echo' };
  if (HARD_STOP_PATTERN.test(text)) return { intent: 'hard_stop', confidence: 0.99, reason: 'explicit_stop' };
  if (BACKCHANNEL_PATTERN.test(text)) return { intent: 'backchannel', confidence: 0.92, reason: 'brief_acknowledgement' };
  if (CORRECTION_PATTERN.test(text)) return { intent: 'interrupt', confidence: 0.9, reason: 'correction' };
  if (QUESTION_PATTERN.test(text)) return { intent: 'interrupt', confidence: 0.86, reason: 'question' };
  const words = text.split(/\s+/).filter(Boolean);
  if (words.length >= 4 || text.length >= 24) {
    return { intent: 'interrupt', confidence: 0.74, reason: 'sustained_overlap' };
  }
  return { intent: 'uncertain', confidence: 0.45, reason: 'short_ambiguous_overlap' };
}

export function isLikelyEcho(transcript: string, assistantText: string): boolean {
  const inputWords = wordSet(transcript);
  const outputWords = wordSet(assistantText);
  if (inputWords.size < 3 || outputWords.size < 3) return false;
  let overlap = 0;
  for (const word of inputWords) if (outputWords.has(word)) overlap += 1;
  return overlap / inputWords.size >= 0.78;
}

export function shouldConfirmInterruption(
  assessment: OverlapAssessment,
  preference: InterruptionPreference = readLiveConversationSettings().interruptionPreference,
): boolean {
  // Acoustic echo has stronger evidence than a partial STT overlap transcript.
  // A subsequent independent-speech acoustic verdict clears this latch before
  // the user's real transcript reaches the semantic classifier.
  if (isPlaybackEchoSuppressed()) return false;
  if (assessment.intent === 'hard_stop') return true;
  if (assessment.intent !== 'interrupt') return false;
  const threshold = preference === 'easy' ? 0.58 : preference === 'finish_more' ? 0.86 : 0.7;
  return assessment.confidence >= threshold;
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

function wordSet(value: string): Set<string> {
  return new Set(
    normalize(value)
      .toLocaleLowerCase()
      .replace(/[^\p{L}\p{N}'\s]/gu, ' ')
      .split(/\s+/)
      .filter((word) => word.length > 1),
  );
}
