export type ClauseCommitReason = 'strong-boundary' | 'stable-boundary' | 'deadline' | 'maximum' | 'stream-end';

export type StableClause = {
  text: string;
  reason: ClauseCommitReason;
};

export type ClauseStabilizerOptions = {
  minimumClauseCharacters?: number;
  stableLookaheadCharacters?: number;
  maximumClauseCharacters?: number;
  deadlineMs?: number;
  firstClauseMinimumCharacters?: number;
  firstClauseStableLookaheadCharacters?: number;
  firstClauseMaximumCharacters?: number;
  firstClauseDeadlineMs?: number;
};

type ClausePolicy = {
  minimum: number;
  lookahead: number;
  maximum: number;
  deadlineMs: number;
};

// Later clauses retain enough text for stable prosody while staying short enough
// for reliable local TTS lexical coverage. The first clause uses a smaller
// bounded window because the user has already experienced STT + LLM latency
// before any audio can begin.
const DEFAULT_MINIMUM = 12;
const DEFAULT_LOOKAHEAD = 12;
const DEFAULT_MAXIMUM = 64;
const DEFAULT_DEADLINE_MS = 140;
const DEFAULT_FIRST_MINIMUM = 8;
const DEFAULT_FIRST_LOOKAHEAD = 4;
const DEFAULT_FIRST_MAXIMUM = 56;
const DEFAULT_FIRST_DEADLINE_MS = 55;
const STRONG_BOUNDARY = /[.!?][\]})"'’”]*(?=\s|$)/g;
const WEAK_BOUNDARY = /[,;:][\]})"'’”]*(?=\s|$)/g;
const ABBREVIATION = /(?:\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e)|\b[A-Z])\.$/i;
const DECIMAL_OR_VERSION = /\d\.\d$/;
const URL_TAIL = /(?:https?:\/\/|www\.)\S*$/i;
const OPENING_QUOTE = /^["“‘]/;
const EMPHASISED_PARENTHETICAL = /(?:\*{1,3}|_{1,3})\s*\([^)*_\n]{0,240}\)\s*(?:\*{1,3}|_{1,3})/gu;
const PLAIN_PARENTHETICAL = /\(([^)\n]{1,180})\)/gu;
const MARKDOWN_SPAN = /(\*{1,3}|_{1,3})([^*_\n]{1,160})\1/gu;
const EMOJI_SEQUENCE = /\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier}|\u200D\p{Extended_Pictographic})*/gu;
const STACKED_LEADING_HESITATIONS = /^(?:(?:h+m+|u+m+|uh+|erm+|er+)\b(?:\s|[.…,!—–-])*){2,}/iu;
const STAGE_DIRECTION_START = /^(?:(?:a|an|the)\s+)?(?:(?:soft|small|little|nervous|playful|gentle|quiet)\s+)*(?:pause|pauses|sigh|sighs|laugh|laughs|laughter|chuckle|chuckles|giggle|giggles|breath|breathes|inhale|inhales|exhale|exhales|nod|nods|smile|smiles|grin|grins|shrug|shrugs|tilt|tilts|whisper|whispers|murmur|murmurs|typing sounds?)(?:\b|$)/iu;
const STAGE_DIRECTION_META = /\b(?:tone|sounds?\s+implied|stage\s+direction|gesture|facial\s+expression)\b/iu;
const WRITTEN_SOUND_EFFECT = /^["“”'‘’]?(?:he+he+|ha+ha+|hm+|mm+|ugh+|ah+)[.!…]*["“”'‘’]?$/iu;
const NON_SPEECH_EMOJI_ONLY = /^(?=[\s\S]*\p{Extended_Pictographic})[\s\p{Extended_Pictographic}\uFE0F\p{Emoji_Modifier}\u200D\p{Punctuation}\p{Symbol}]+$/u;

export class StableClauseAccumulator {
  private buffer = '';
  private openedAtMs: number | null = null;
  private committedClauseCount = 0;
  private readonly normalPolicy: ClausePolicy;
  private readonly firstPolicy: ClausePolicy;

  constructor(options: ClauseStabilizerOptions = {}) {
    const normalMinimum = positiveInteger(options.minimumClauseCharacters, DEFAULT_MINIMUM);
    const normalMaximum = Math.max(
      normalMinimum,
      positiveInteger(options.maximumClauseCharacters, DEFAULT_MAXIMUM),
    );
    this.normalPolicy = {
      minimum: normalMinimum,
      lookahead: positiveInteger(options.stableLookaheadCharacters, DEFAULT_LOOKAHEAD),
      maximum: normalMaximum,
      deadlineMs: positiveInteger(options.deadlineMs, DEFAULT_DEADLINE_MS),
    };

    const firstMinimum = positiveInteger(
      options.firstClauseMinimumCharacters,
      options.minimumClauseCharacters ?? DEFAULT_FIRST_MINIMUM,
    );
    this.firstPolicy = {
      minimum: firstMinimum,
      lookahead: positiveInteger(
        options.firstClauseStableLookaheadCharacters,
        options.stableLookaheadCharacters ?? DEFAULT_FIRST_LOOKAHEAD,
      ),
      maximum: Math.max(
        firstMinimum,
        positiveInteger(
          options.firstClauseMaximumCharacters,
          options.maximumClauseCharacters ?? DEFAULT_FIRST_MAXIMUM,
        ),
      ),
      deadlineMs: positiveInteger(
        options.firstClauseDeadlineMs,
        options.deadlineMs ?? DEFAULT_FIRST_DEADLINE_MS,
      ),
    };
  }

  append(fragment: string, nowMs = performance.now()): StableClause[] {
    const normalized = fragment.trim();
    if (!normalized) return [];
    this.buffer = mergeStreamText(this.buffer, normalized);
    if (this.openedAtMs === null) this.openedAtMs = nowMs;
    return this.takeReady(nowMs);
  }

  takeReady(nowMs = performance.now()): StableClause[] {
    const committed: StableClause[] = [];
    while (true) {
      const boundary = this.nextBoundary(nowMs);
      if (!boundary) break;
      const text = this.buffer.slice(0, boundary.end).trim();
      this.buffer = this.buffer.slice(boundary.end).trimStart();
      const spokenText = sanitizeLiveVoiceSpokenText(text);
      if (spokenText) {
        committed.push({ text: spokenText, reason: boundary.reason });
        this.committedClauseCount += 1;
      }
      this.openedAtMs = this.buffer ? nowMs : null;
    }
    return committed;
  }

  flush(): StableClause[] {
    const text = sanitizeLiveVoiceSpokenText(this.buffer.trim());
    this.buffer = '';
    this.openedAtMs = null;
    if (!text) return [];
    this.committedClauseCount += 1;
    return [{ text, reason: 'stream-end' }];
  }

  pendingText(): string {
    return this.buffer;
  }

  deadlineRemainingMs(nowMs = performance.now()): number | null {
    if (this.openedAtMs === null || !this.buffer) return null;
    const policy = this.currentPolicy();
    return Math.max(0, policy.deadlineMs - (nowMs - this.openedAtMs));
  }

  private currentPolicy(): ClausePolicy {
    return this.committedClauseCount === 0 ? this.firstPolicy : this.normalPolicy;
  }

  private nextBoundary(nowMs: number): { end: number; reason: ClauseCommitReason } | null {
    const policy = this.currentPolicy();
    const strong = findSafeBoundary(this.buffer, STRONG_BOUNDARY, policy.minimum);
    const weak = findStableWeakBoundary(this.buffer, policy.minimum, policy.lookahead);
    const naturalBoundary = weak !== null && (strong === null || weak < strong)
      ? { end: weak, reason: 'stable-boundary' as const }
      : strong !== null ? { end: strong, reason: 'strong-boundary' as const } : null;

    // Do not let late punctuation defeat the TTS fidelity ceiling. If a known
    // natural boundary lies beyond the ceiling, reserve at least one minimum
    // clause for its tail and split the prefix at whitespace. Joining emitted
    // clauses therefore reconstructs the source text without dropping words.
    if (naturalBoundary && naturalBoundary.end <= policy.maximum) return naturalBoundary;

    if (this.buffer.length >= policy.maximum) {
      const splitLimit = naturalBoundary
        ? Math.min(policy.maximum, Math.max(policy.minimum, naturalBoundary.end - policy.minimum))
        : policy.maximum;
      const fallback = safeWhitespaceBoundary(this.buffer, splitLimit, policy.minimum);
      if (fallback !== null) return { end: fallback, reason: 'maximum' };
    }

    if (naturalBoundary) return naturalBoundary;

    if (
      this.openedAtMs !== null
      && nowMs - this.openedAtMs >= policy.deadlineMs
      && this.buffer.length >= policy.minimum
    ) {
      const deadlineBoundary = safeWhitespaceBoundary(
        this.buffer,
        Math.min(policy.maximum, this.buffer.length),
        policy.minimum,
      );
      if (deadlineBoundary !== null) return { end: deadlineBoundary, reason: 'deadline' };
    }
    return null;
  }
}

export function sanitizeLiveVoiceSpokenText(text: string): string {
  const original = text.trim();
  let cleaned = text
    .replace(EMPHASISED_PARENTHETICAL, ' ')
    .replace(PLAIN_PARENTHETICAL, (match, content: string) => (
      isStageDirection(content) ? ' ' : match
    ))
    .replace(MARKDOWN_SPAN, (_match, _marker: string, content: string) => (
      isStageDirection(content) ? ' ' : content
    ))
    .replace(EMOJI_SEQUENCE, ' ');

  cleaned = cleaned.replace(STACKED_LEADING_HESITATIONS, '');

  const spokenText = cleaned
    .replace(/\s+/gu, ' ')
    .replace(/\s+([,.;:!?])/gu, '$1')
    .replace(/\s+([)\]}])/gu, '$1')
    .replace(/^[\s,;:!?…—–.-]+/u, '')
    .trim();

  if (spokenText) return spokenText;
  return NON_SPEECH_EMOJI_ONLY.test(original) ? original : '';
}

export function mergeStreamText(current: string, next: string): string {
  const left = current.trimEnd();
  const right = next.trimStart();
  if (!left) return right;
  if (!right) return left;
  if (/^[,.;:!?%\]})]/.test(right) || /[([{“‘]$/.test(left)) return `${left}${right}`;
  return `${left} ${right}`;
}

function isStageDirection(content: string): boolean {
  const normalized = content.trim();
  return STAGE_DIRECTION_START.test(normalized)
    || STAGE_DIRECTION_META.test(normalized)
    || WRITTEN_SOUND_EFFECT.test(normalized);
}

function findSafeBoundary(text: string, pattern: RegExp, minimum: number): number | null {
  pattern.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const end = match.index + match[0].length;
    if (end < minimum) continue;
    const prefix = text.slice(0, end);
    if (isProtectedBoundary(prefix)) continue;
    return end;
  }
  return null;
}

function findStableWeakBoundary(text: string, minimum: number, lookahead: number): number | null {
  WEAK_BOUNDARY.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = WEAK_BOUNDARY.exec(text)) !== null) {
    const end = match.index + match[0].length;
    if (end < minimum || text.length - end < lookahead) continue;
    if (OPENING_QUOTE.test(text.slice(end).trimStart())) continue;
    if (isProtectedBoundary(text.slice(0, end))) continue;
    return end;
  }
  return null;
}

function safeWhitespaceBoundary(text: string, limit: number, minimum: number): number | null {
  const bounded = text.slice(0, Math.max(minimum, limit));
  for (let index = bounded.length - 1; index >= minimum; index -= 1) {
    if (!/\s/.test(bounded[index])) continue;
    const prefix = bounded.slice(0, index);
    if (!isProtectedBoundary(prefix)) return index;
  }
  return null;
}

function isProtectedBoundary(prefix: string): boolean {
  const trimmed = prefix.trimEnd();
  return ABBREVIATION.test(trimmed)
    || DECIMAL_OR_VERSION.test(trimmed)
    || URL_TAIL.test(trimmed)
    || hasUnclosedPair(trimmed, '(', ')')
    || hasUnclosedPair(trimmed, '[', ']')
    || hasUnclosedPair(trimmed, '{', '}')
    || hasUnclosedPair(trimmed, '“', '”')
    || hasUnclosedPair(trimmed, '‘', '’')
    || hasOddUnescapedQuoteCount(trimmed, '"');
}

function hasUnclosedPair(text: string, open: string, close: string): boolean {
  return text.lastIndexOf(open) > text.lastIndexOf(close);
}

function hasOddUnescapedQuoteCount(text: string, quote: string): boolean {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] !== quote) continue;
    let backslashes = 0;
    for (let cursor = index - 1; cursor >= 0 && text[cursor] === '\\'; cursor -= 1) backslashes += 1;
    if (backslashes % 2 === 0) count += 1;
  }
  return count % 2 === 1;
}

function positiveInteger(value: number | undefined, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.round(value)
    : fallback;
}
