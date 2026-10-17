import { describe, expect, it } from 'vitest';

import { evaluateLiveConversation, type LiveConversationEvaluationEvent } from './live-conversation-evaluation';

describe('evaluateLiveConversation', () => {
  it('returns empty values without inventing scores', () => {
    const report = evaluateLiveConversation([]);
    expect(report.eventCount).toBe(0);
    expect(report.firstAudioLatencyMs).toEqual({ average: null, p95: null });
    expect(report.falseEndpointRate).toBeNull();
    expect(report.turnDurationMs).toEqual({ median: null, p95: null });
    expect(report.perceivedListeningScore).toBeNull();
  });

  it('aggregates contextual conversation quality deterministically', () => {
    const events: LiveConversationEvaluationEvent[] = [
      { atMs: 20, type: 'first_audio', latencyMs: 1_000 },
      { atMs: 10, type: 'first_audio', latencyMs: 500 },
      { atMs: 30, type: 'endpoint', falsePositive: false },
      { atMs: 31, type: 'endpoint', falsePositive: true },
      { atMs: 40, type: 'talk_over', durationMs: 300 },
      { atMs: 41, type: 'talk_over', durationMs: 200 },
      { atMs: 50, type: 'interruption', success: true, latencyMs: 100 },
      { atMs: 51, type: 'interruption', success: false, latencyMs: 300 },
      { atMs: 60, type: 'proactive_prompt', accepted: true },
      { atMs: 61, type: 'proactive_prompt', accepted: false },
      { atMs: 62, type: 'proactive_prompt', accepted: null },
      { atMs: 70, type: 'backchannel', collision: false },
      { atMs: 71, type: 'backchannel', collision: true },
      { atMs: 80, type: 'turn', role: 'user', durationMs: 4_000, content: 'Here is the situation.' },
      { atMs: 81, type: 'turn', role: 'assistant', durationMs: 6_000, content: 'What happened next?' },
      { atMs: 82, type: 'turn', role: 'assistant', durationMs: 6_000, content: 'That makes sense.' },
      { atMs: 90, type: 'repair', success: true },
      { atMs: 91, type: 'repair', success: false },
      { atMs: 100, type: 'topic', repeated: false },
      { atMs: 101, type: 'topic', repeated: true },
      { atMs: 110, type: 'obligation', answered: true },
      { atMs: 111, type: 'obligation', answered: false },
      { atMs: 120, type: 'survey', listeningScore: 7, pressureScore: 0 },
    ];

    const report = evaluateLiveConversation(events);

    expect(report.firstAudioLatencyMs).toEqual({ average: 750, p95: 1_000 });
    expect(report.falseEndpointRate).toBe(0.5);
    expect(report.talkOverDurationMs).toBe(500);
    expect(report.interruptionSuccessRate).toBe(0.5);
    expect(report.cancellationLatencyMs).toEqual({ average: 200, p95: 300 });
    expect(report.silenceFillRegretRate).toBe(0.5);
    expect(report.proactiveAcceptanceRate).toBe(0.5);
    expect(report.backchannelCollisionRate).toBe(0.5);
    expect(report.questionDensity).toBe(0.5);
    expect(report.assistantUserSpeakingRatio).toBe(3);
    expect(report.turnDurationMs).toEqual({ median: 6_000, p95: 6_000 });
    expect(report.repairSuccessRate).toBe(0.5);
    expect(report.repeatedTopicRate).toBe(0.5);
    expect(report.unansweredObligationRate).toBe(0.5);
    expect(report.perceivedListeningScore).toBe(5);
    expect(report.perceivedPressureScore).toBe(1);
  });
});
