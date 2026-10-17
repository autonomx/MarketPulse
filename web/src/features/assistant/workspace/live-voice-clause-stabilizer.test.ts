import { describe, expect, it } from 'vitest';

import {
  StableClauseAccumulator,
  mergeStreamText,
  sanitizeLiveVoiceSpokenText,
} from './live-voice-clause-stabilizer';

describe('stable live voice clauses', () => {
  it('commits strong terminal punctuation incrementally', () => {
    const accumulator = new StableClauseAccumulator({ minimumClauseCharacters: 12 });

    expect(accumulator.append('This is the first complete thought.', 0)).toEqual([
      { text: 'This is the first complete thought.', reason: 'strong-boundary' },
    ]);
    expect(accumulator.pendingText()).toBe('');
  });

  it('uses a bounded 55 ms deadline for the first spoken clause by default', () => {
    const accumulator = new StableClauseAccumulator();

    expect(accumulator.append('Start speaking now with enough text', 0)).toEqual([]);
    expect(accumulator.takeReady(54)).toEqual([]);
    expect(accumulator.takeReady(56)).toEqual([
      { text: 'Start speaking now with enough', reason: 'deadline' },
    ]);
    expect(accumulator.pendingText()).toBe('text');
  });

  it('does not commit a punctuated first prefix below its configured audio floor', () => {
    const accumulator = new StableClauseAccumulator({
      firstClauseMinimumCharacters: 8,
      firstClauseDeadlineMs: 20,
    });

    expect(accumulator.append('Right.', 0)).toEqual([]);
    expect(accumulator.append(' Let me check.', 5)).toEqual([
      { text: 'Right. Let me check.', reason: 'strong-boundary' },
    ]);
  });

  it('returns to the 140 ms policy after the first clause', () => {
    const accumulator = new StableClauseAccumulator();

    expect(accumulator.append('First clause.', 0)).toEqual([
      { text: 'First clause.', reason: 'strong-boundary' },
    ]);
    expect(accumulator.append('The second clause has enough words to split', 10)).toEqual([]);
    expect(accumulator.takeReady(149)).toEqual([]);
    expect(accumulator.takeReady(151)).toEqual([
      { text: 'The second clause has enough words to', reason: 'deadline' },
    ]);
    expect(accumulator.pendingText()).toBe('split');
  });

  it('uses lookahead before committing weaker punctuation', () => {
    const accumulator = new StableClauseAccumulator({
      minimumClauseCharacters: 12,
      stableLookaheadCharacters: 12,
    });

    expect(accumulator.append('There are two options,', 0)).toEqual([]);
    expect(accumulator.append('and the safer one keeps state isolated.', 50)).toEqual([
      { text: 'There are two options,', reason: 'stable-boundary' },
      { text: 'and the safer one keeps state isolated.', reason: 'strong-boundary' },
    ]);
  });

  it('caps later narrative clauses before late punctuation without losing words', () => {
    const accumulator = new StableClauseAccumulator();
    expect(accumulator.append('Opening line.', 0)).toEqual([
      { text: 'Opening line.', reason: 'strong-boundary' },
    ]);
    const source = 'I was a kid who loved stories and I would stay up late imagining every strange place those stories might take me.';

    const clauses = [
      ...accumulator.append(source, 10),
      ...accumulator.flush(),
    ];

    expect(clauses.length).toBeGreaterThan(1);
    expect(clauses[0]?.reason).toBe('maximum');
    expect(clauses.every((clause) => clause.text.length <= 64)).toBe(true);
    expect(clauses.map((clause) => clause.text).join(' ')).toBe(source);
    expect(clauses.map((clause) => clause.text).join(' ')).toContain('I was a kid who loved stories');
  });

  it('does not split decimals, abbreviations, URLs, or unclosed parentheses', () => {
    const accumulator = new StableClauseAccumulator({
      minimumClauseCharacters: 8,
      maximumClauseCharacters: 200,
    });

    expect(accumulator.append('Use version 2.5 and ask Dr. Smith to check (the URL https://example.com/a.b', 0)).toEqual([]);
    expect(accumulator.append('after the call).', 50)).toEqual([
      {
        text: 'Use version 2.5 and ask Dr. Smith to check (the URL https://example.com/a.b after the call).',
        reason: 'strong-boundary',
      },
    ]);
  });

  it('does not commit punctuation inside an unfinished quotation', () => {
    const straight = new StableClauseAccumulator({ minimumClauseCharacters: 8 });
    expect(straight.append('He said, "Wait. I need to explain', 0)).toEqual([]);
    expect(straight.append('before we decide."', 50)).toEqual([
      {
        text: 'He said, "Wait. I need to explain before we decide."',
        reason: 'strong-boundary',
      },
    ]);

    const curly = new StableClauseAccumulator({ minimumClauseCharacters: 8 });
    expect(curly.append('She replied, “Not yet. There is more', 0)).toEqual([]);
    expect(curly.append('to consider.”', 50)).toEqual([
      {
        text: 'She replied, “Not yet. There is more to consider.”',
        reason: 'strong-boundary',
      },
    ]);
  });

  it('commits a safe fallback when the latency deadline expires', () => {
    const accumulator = new StableClauseAccumulator({
      minimumClauseCharacters: 12,
      deadlineMs: 100,
    });

    expect(accumulator.append('This answer has enough words for a safe deadline split', 0)).toEqual([]);
    expect(accumulator.takeReady(101)).toEqual([
      { text: 'This answer has enough words for a safe deadline', reason: 'deadline' },
    ]);
    expect(accumulator.pendingText()).toBe('split');
  });

  it('flushes the unspoken tail only at stream end', () => {
    const accumulator = new StableClauseAccumulator();
    accumulator.append('A final short tail', 0);
    expect(accumulator.flush()).toEqual([{ text: 'A final short tail', reason: 'stream-end' }]);
    expect(accumulator.flush()).toEqual([]);
  });

  it('merges punctuation chunks without introducing spoken spacing artifacts', () => {
    expect(mergeStreamText('Hello', '.')).toBe('Hello.');
    expect(mergeStreamText('Use', '(carefully')).toBe('Use (carefully');
  });

  it('removes screenplay annotations, stacked fillers, markdown, and emoji', () => {
    const malformed = 'Hmm... *(soft pause, typing sounds implied)* Um, okay—trying this out feels weirdly fun? '
      + '*(nervous little "hehe...")* Like... *(sighs lightly, playful tone)* honestly? '
      + 'I love that you asked. *(tilts head gently, curious)* It feels *surprisingly* natural. 😅';

    expect(sanitizeLiveVoiceSpokenText(malformed)).toBe(
      'okay—trying this out feels weirdly fun? Like... honestly? '
      + 'I love that you asked. It feels surprisingly natural.',
    );
  });

  it('preserves ordinary parenthetical language while removing stage directions', () => {
    expect(sanitizeLiveVoiceSpokenText(
      'The result (after normalization) is stable. (sighs lightly) That is *actually* useful.',
    )).toBe('The result (after normalization) is stable. That is actually useful.');
  });

  it('drops annotation-only clauses before they reach delivery or TTS', () => {
    const accumulator = new StableClauseAccumulator({ minimumClauseCharacters: 4 });

    expect(accumulator.append('*(soft pause)*', 0)).toEqual([]);
    expect(accumulator.flush()).toEqual([]);
  });
});
