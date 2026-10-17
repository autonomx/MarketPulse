import { describe, expect, it } from 'vitest';

import { acceptedFinalSuppressionReason } from './live-accepted-final-routing';

describe('accepted live final routing', () => {
  it('routes meaningful uncertain overlap instead of dropping the user comment', () => {
    expect(acceptedFinalSuppressionReason('I was talking about the other issue.', 'uncertain')).toBeNull();
    expect(acceptedFinalSuppressionReason('banana', 'uncertain')).toBeNull();
  });

  it('routes confirmed interruptions through the coordinator', () => {
    expect(acceptedFinalSuppressionReason('Wait, that is not what I meant.', 'interrupt')).toBeNull();
    expect(acceptedFinalSuppressionReason('What about tomorrow?', null)).toBeNull();
  });

  it('suppresses only explicit control, backchannel, noise, or empty finals', () => {
    expect(acceptedFinalSuppressionReason('stop', 'hard_stop')).toBe('hard_stop');
    expect(acceptedFinalSuppressionReason('mhm', 'backchannel')).toBe('backchannel');
    expect(acceptedFinalSuppressionReason('[noise]', 'noise')).toBe('noise');
    expect(acceptedFinalSuppressionReason('   ', 'uncertain')).toBe('empty_transcript');
  });
});
