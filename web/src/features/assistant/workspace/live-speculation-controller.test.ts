import { describe, expect, it } from 'vitest';

import {
  normalizeSpeculationWords,
  shouldStartExtendedHypothesis,
  speculationCandidateCanStart,
  speculativeTtsPrefetchEnabled,
  speculativeTtsPrefetchWindowOpen,
  transcriptCorrectionNeedsStability,
  transcriptExtendsSpeculation,
  transcriptsCanReuseSpeculation,
} from './live-speculation-controller';

describe('live speculation hypothesis policy', () => {
  it('keeps punctuation-insensitive exact final reuse strict at the word level', () => {
    expect(transcriptsCanReuseSpeculation(
      'What kind of nonsense',
      'What kind of nonsense?',
    )).toBe(true);
    expect(transcriptsCanReuseSpeculation(
      'What kind of',
      'What kind of nonsense?',
    )).toBe(false);
  });

  it('recognizes monotonic transcript extensions without treating them as corrections', () => {
    expect(transcriptExtendsSpeculation(
      'What kind of',
      'What kind of nonsense is that',
    )).toBe(true);
    expect(transcriptExtendsSpeculation(
      'What kind of',
      'What type of nonsense',
    )).toBe(false);
  });

  it('stabilizes a non-monotonic ASR correction before replacing an exact hypothesis', () => {
    expect(transcriptCorrectionNeedsStability(
      "I'm going to do something interesting today.",
      "I'm going to something today.",
    )).toBe(true);
    expect(transcriptCorrectionNeedsStability(
      'Tell me about',
      'Tell me about the weather',
    )).toBe(false);
  });

  it('starts a replacement hypothesis after two new words', () => {
    expect(shouldStartExtendedHypothesis(
      'Tell me about',
      'Tell me about the weather',
      0.7,
    )).toBe(true);
    expect(shouldStartExtendedHypothesis(
      'Tell me about',
      'Tell me about weather',
      0.7,
    )).toBe(false);
  });

  it('allows one-word replacement only for a very strong endpoint candidate', () => {
    expect(shouldStartExtendedHypothesis(
      'Tell me about',
      'Tell me about weather',
      0.86,
    )).toBe(true);
  });

  it('allows guarded one-word complete utterances only at high confidence', () => {
    expect(speculationCandidateCanStart('Why?', 0.87)).toBe(false);
    expect(speculationCandidateCanStart('Why?', 0.88)).toBe(true);
    expect(speculationCandidateCanStart('because', 0.99)).toBe(false);
    expect(speculationCandidateCanStart('wait', 0.99)).toBe(false);
  });

  it('normalizes curly apostrophes for final compatibility', () => {
    expect(normalizeSpeculationWords('That’s wild')).toEqual(["that's", 'wild']);
  });

  it('keeps the retained exact-final TTS prefetch window open through chat handoff', () => {
    expect(speculativeTtsPrefetchWindowOpen(null, true)).toBe(true);
    expect(speculativeTtsPrefetchWindowOpen(null, false)).toBe(false);
    expect(speculativeTtsPrefetchWindowOpen('final transcript', true)).toBe(true);
  });

  it('keeps speculative TTS enabled by default but allows an explicit TTS-only opt out', () => {
    expect(speculativeTtsPrefetchEnabled(undefined)).toBe(true);
    expect(speculativeTtsPrefetchEnabled('true')).toBe(true);
    expect(speculativeTtsPrefetchEnabled('FALSE')).toBe(false);
    expect(speculativeTtsPrefetchEnabled(' false ')).toBe(false);
  });
});
