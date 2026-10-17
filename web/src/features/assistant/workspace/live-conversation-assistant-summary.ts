import {
  noteAssistantTurnCompletionContext,
  resetAssistantTurnCompletionContext,
} from './live-turn-context';
import { emitOmnixEvent, LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT } from '../../../events/bus';


export type LiveAssistantTurnSummary = {
  turnId: string | null;
  turnKind: 'greeting' | 'response';
  wordCount: number;
  questionCount: number;
  topicFingerprint: string | null;
  createsObligation: boolean;
};

type PendingDiagnosticSummary = {
  text: string;
  turnId: string | null;
  turnKind: 'greeting' | 'response';
  startedAtMs: number;
  updatedAtMs: number;
};

const OBLIGATION_PATTERN = /(?:\?|\b(?:would you like|do you want|can you|could you|will you|should we|shall we|tell me|let me know)\b)/i;
const STOP_WORDS = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from', 'had', 'has', 'have',
  'he', 'her', 'his', 'i', 'if', 'in', 'is', 'it', 'its', 'me', 'my', 'of', 'on', 'or', 'our',
  'she', 'so', 'that', 'the', 'their', 'them', 'there', 'they', 'this', 'to', 'was', 'we', 'were',
  'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with', 'would', 'you', 'your',
]);
const MAX_PENDING_DIAGNOSTIC_SUMMARIES = 16;
const MAX_PENDING_TEXT_CHARS = 12_000;
const PENDING_DIAGNOSTIC_TTL_MS = 60_000;
const pendingDiagnostics = new Map<string, PendingDiagnosticSummary>();

export function summarizeAssistantTurn(
  text: string,
  turnId: string | null,
  turnKind: 'greeting' | 'response',
): LiveAssistantTurnSummary {
  const normalized = text.replace(/\s+/g, ' ').trim();
  const words = normalized.match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) ?? [];
  const questions = normalized.match(/[?？]+/g) ?? [];
  return {
    turnId,
    turnKind,
    wordCount: words.length,
    questionCount: questions.length,
    topicFingerprint: fingerprintTopic(words),
    createsObligation: turnKind === 'response' && OBLIGATION_PATTERN.test(normalized),
  };
}

export function observeAssistantDiagnostic(
  traceId: string,
  event: string,
  details: Record<string, unknown>,
): void {
  const now = performance.now();
  prunePendingDiagnostics(now);
  if (event === 'turn_intercepted') {
    pendingDiagnostics.set(traceId, {
      text: '',
      turnId: null,
      turnKind: details.turn_kind === 'greeting' ? 'greeting' : 'response',
      startedAtMs: now,
      updatedAtMs: now,
    });
    prunePendingDiagnostics(now);
    return;
  }
  const pending = pendingDiagnostics.get(traceId);
  if (!pending) return;
  pending.updatedAtMs = now;
  if (event === 'assistant_turn_linked' && typeof details.assistant_turn_id === 'string') {
    pending.turnId = details.assistant_turn_id;
    return;
  }
  if (event === 'llm_text_chunk_received' && typeof details.text === 'string') {
    pending.text = `${pending.text}${details.text}`.slice(-MAX_PENDING_TEXT_CHARS);
    return;
  }
  if (event === 'llm_stream_finished') {
    dispatchAssistantTurnSummary(summarizeAssistantTurn(pending.text, pending.turnId, pending.turnKind));
    pendingDiagnostics.delete(traceId);
    return;
  }
  if (
    event === 'turn_failed'
    || event === 'turn_stopped'
    || event === 'reporter_closed'
    || event === 'turn_finished'
  ) {
    pendingDiagnostics.delete(traceId);
  }
}

export function readCurrentAssistantDiagnosticText(): string {
  prunePendingDiagnostics(performance.now());
  let current: PendingDiagnosticSummary | null = null;
  for (const pending of pendingDiagnostics.values()) {
    if (!current || pending.updatedAtMs > current.updatedAtMs) current = pending;
  }
  return current?.text ?? '';
}

export function pendingAssistantDiagnosticCount(): number {
  prunePendingDiagnostics(performance.now());
  return pendingDiagnostics.size;
}

export function dispatchAssistantTurnSummary(summary: LiveAssistantTurnSummary): void {
  noteAssistantTurnCompletionContext({
    turnId: summary.turnId,
    questionCount: summary.questionCount,
    createsObligation: summary.createsObligation,
  });
  if (typeof window === 'undefined') return;
  emitOmnixEvent(LIVE_CONVERSATION_ASSISTANT_SUMMARY_EVENT, summary);
}

export function resetAssistantDiagnosticSummaries(): void {
  pendingDiagnostics.clear();
  resetAssistantTurnCompletionContext();
}

function prunePendingDiagnostics(now: number): void {
  for (const [traceId, pending] of pendingDiagnostics) {
    if (now - pending.updatedAtMs > PENDING_DIAGNOSTIC_TTL_MS) pendingDiagnostics.delete(traceId);
  }
  if (pendingDiagnostics.size <= MAX_PENDING_DIAGNOSTIC_SUMMARIES) return;
  const oldest = [...pendingDiagnostics.entries()]
    .sort((left, right) => left[1].updatedAtMs - right[1].updatedAtMs)
    .slice(0, pendingDiagnostics.size - MAX_PENDING_DIAGNOSTIC_SUMMARIES);
  oldest.forEach(([traceId]) => pendingDiagnostics.delete(traceId));
}

function fingerprintTopic(words: string[]): string | null {
  const significant = words
    .map((word) => word.toLocaleLowerCase().replace(/[’']/g, ''))
    .filter((word) => word.length >= 3 && !STOP_WORDS.has(word))
    .slice(0, 24)
    .sort();
  if (significant.length < 2) return null;
  let hash = 2166136261;
  for (const character of significant.join('|')) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `topic-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}
