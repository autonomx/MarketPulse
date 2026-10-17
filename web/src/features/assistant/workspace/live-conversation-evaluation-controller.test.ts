import { beforeEach, describe, expect, it } from 'vitest';

import {
  evaluationEventFromPerfDetail,
  initializeLiveConversationEvaluationController,
  readLiveConversationEvaluationSnapshot,
  recordLiveConversationEvaluationEvent,
  recordLiveConversationSurvey,
  resetLiveConversationEvaluation,
} from './live-conversation-evaluation-controller';
import { liveConversationStore } from './live-conversation-store';
import { initializeLiveConversationStoreBridge } from './live-conversation-store-bridge';

describe('live conversation evaluation controller', () => {
  beforeEach(() => {
    window.localStorage.clear();
    liveConversationStore.reset();
    resetLiveConversationEvaluation();
  });

  it('maps structured voice diagnostics without guessing unsupported metrics', () => {
    expect(evaluationEventFromPerfDetail({ stage: 'voice_audio_turnaround', total_ms: 840 }, 10))
      .toEqual({ atMs: 10, type: 'first_audio', latencyMs: 840 });
    expect(evaluationEventFromPerfDetail({ stage: 'endpoint_false_positive' }, 20))
      .toEqual({ atMs: 20, type: 'endpoint', falsePositive: true });
    expect(evaluationEventFromPerfDetail({
      stage: 'overlap_classified', intent: 'correction', cancellation_latency_ms: 120,
    }, 30)).toEqual({ atMs: 30, type: 'interruption', success: true, latencyMs: 120 });
    expect(evaluationEventFromPerfDetail({ stage: 'overlap_classified', intent: 'backchannel' }, 40)).toBeNull();
  });

  it('removes turn content before memory and browser fallback persistence', () => {
    recordLiveConversationEvaluationEvent({ atMs: 1, type: 'first_audio', latencyMs: 500 });
    recordLiveConversationEvaluationEvent({
      atMs: 2, type: 'turn', role: 'assistant', durationMs: 1_000, content: 'Ready?', questionCount: 1,
    });
    const snapshot = recordLiveConversationSurvey(5, 2);

    expect(snapshot.report.firstAudioLatencyMs.average).toBe(500);
    expect(snapshot.report.questionDensity).toBe(1);
    expect(snapshot.report.perceivedListeningScore).toBe(5);
    expect(snapshot.report.perceivedPressureScore).toBe(2);
    expect(readLiveConversationEvaluationSnapshot().events).toHaveLength(3);

    const stored = JSON.parse(window.localStorage.getItem('omnix.liveConversation.evaluation.v1') || '[]');
    expect(stored).toHaveLength(3);
    expect(stored.find((event: { type: string }) => event.type === 'turn').content).toBeUndefined();
    expect(JSON.stringify(stored)).not.toContain('Ready?');
  });

  it('derives question, repeated-topic, and obligation outcomes from content-free summaries', () => {
    const dispose = initializeLiveConversationEvaluationController();

    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'audio_started' } });
    window.dispatchEvent(new CustomEvent('omnix:live-conversation-assistant-summary', {
      detail: {
        turnId: 'assistant-one', turnKind: 'response', wordCount: 8, questionCount: 1,
        topicFingerprint: 'topic-launch', createsObligation: true,
      },
    }));
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'idle' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'completed' } });
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-user-speech'));
    window.dispatchEvent(new CustomEvent('omnix:assistant-voice-perf', {
      detail: { stage: 'stt_final_received' },
    }));

    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'audio_started' } });
    window.dispatchEvent(new CustomEvent('omnix:live-conversation-assistant-summary', {
      detail: {
        turnId: 'assistant-two', turnKind: 'response', wordCount: 7, questionCount: 1,
        topicFingerprint: 'topic-launch', createsObligation: true,
      },
    }));
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'idle' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'completed' } });
    window.dispatchEvent(new CustomEvent('omnix:assistant-live-voice-stop'));

    const snapshot = readLiveConversationEvaluationSnapshot();
    expect(snapshot.report.questionDensity).toBe(1);
    expect(snapshot.report.repeatedTopicRate).toBe(0.5);
    expect(snapshot.report.unansweredObligationRate).toBe(0.5);
    expect(JSON.stringify(snapshot.events)).not.toMatch(/launch plan|assistant-one|assistant-two/);
    dispose();
  });

  it('publishes a completed assistant turn once when the store bridge feeds the report back into the store', () => {
    const disposeBridge = initializeLiveConversationStoreBridge();
    const disposeEvaluation = initializeLiveConversationEvaluationController();

    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'speaking' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'audio_started' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'assistant_turn', value: 'idle' } });
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'delivery', value: 'completed' } });

    const assistantTurns = readLiveConversationEvaluationSnapshot().events.filter(
      (event) => event.type === 'turn' && event.role === 'assistant',
    );
    expect(assistantTurns).toHaveLength(1);

    disposeEvaluation();
    disposeBridge();
  });
});
