import { describe, expect, it } from 'vitest';
import { buildHermesSequenceReviewRequest } from './hermesSequenceReviewRequest';

const selectedSessionSummary = {
  id: 'session-1',
  source: 'live' as const,
  title: 'Glimmerdeep',
  location: 'Glimmerdeep Pass',
  summary: '',
  turnLabel: 'Turn 1',
  checkpointLabel: 'Checkpoint',
  updatedAt: 'now',
  sortRank: 1,
};

describe('buildHermesSequenceReviewRequest', () => {
  it('builds a sequence from Hermes suggestions first', () => {
    const request = buildHermesSequenceReviewRequest({
      selectedSessionSummary,
      assistMode: 'auto_low_risk',
      quickActions: [{ icon: 'L', label: 'Look', command: 'look around' }],
      suggestions: [{ id: 'ask', label: 'Ask Bran', command: 'ask Bran about the pass', reason: 'Gather local context.' }],
    });

    expect(request.sequence_id).toBe('ui-session-1-review');
    expect(request.session_id).toBe('session-1');
    expect(request.assist_mode).toBe('auto_low_risk');
    expect(request.objective).toContain('Glimmerdeep Pass');
    expect(request.items).toEqual([
      {
        item_id: 'ask',
        statement: 'ask Bran about the pass',
        expected_effect: 'Gather local context.',
        user_gate: false,
      },
    ]);
  });

  it('falls back to quick actions when suggestions are empty', () => {
    const request = buildHermesSequenceReviewRequest({
      selectedSessionSummary,
      quickActions: [{ icon: 'L', label: 'Look', command: 'look around' }],
      suggestions: [],
    });

    expect(request.risk).toBe('low');
    expect(request.items?.[0]).toMatchObject({ item_id: 'quick-1', statement: 'look around', user_gate: false });
  });

  it('marks risky suggestions for review', () => {
    const request = buildHermesSequenceReviewRequest({
      selectedSessionSummary,
      quickActions: [],
      suggestions: [{ id: 'fight', command: 'attack the bandit', risk: 'high', direct_state_write: true }],
    });

    expect(request.risk).toBe('high');
    expect(request.items?.[0]).toMatchObject({ item_id: 'fight', user_gate: true });
  });
});
