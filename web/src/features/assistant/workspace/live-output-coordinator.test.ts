import { describe, expect, it, vi } from 'vitest';

import {
  createLiveConversationStore,
  INITIAL_LIVE_CONVERSATION_RUNTIME_STATE,
} from './live-conversation-store';
import type { LiveObservation } from './live-observation-coordinator';
import { LiveOutputCoordinator } from './live-output-coordinator';
import type { LiveVoicePcmSession, LiveVoicePcmSessionOptions } from './live-voice-pcm-session';

function observation(id: string, contextVersion: number): LiveObservation {
  return {
    observationId: id,
    basedOnSequence: contextVersion - 1,
    contextVersion,
    taskContractId: 'editing',
    taskContractVersion: 2,
    anchors: [{
      anchorId: `anchor-${id}`,
      segmentIds: [`segment-${id}`],
      sourceFingerprint: `fingerprint-${id}`,
      state: 'open',
    }],
    priority: 'normal',
    status: 'candidate',
    createdAtMs: 1,
    estimatedSpeechMs: 1_500,
    equivalenceKey: `editing:${id}`,
  };
}

function createHarness() {
  const store = createLiveConversationStore(INITIAL_LIVE_CONVERSATION_RUNTIME_STATE);
  store.dispatch({ type: 'session', sessionId: 'session-1' });
  store.dispatch({
    type: 'task_contract',
    contract: {
      taskContractId: 'editing',
      version: 2,
      instruction: 'Correct important grammar mistakes while I read.',
      inputRole: 'ongoing_material',
      assistantInitiative: 'interjecting',
      defaultRetention: 'ephemeral_session',
      observationPolicy: {
        preferredMaximumSpeechMs: 2_500,
        minorFeedback: 'consolidate',
        criticalFeedback: 'interject',
      },
    },
  });
  store.dispatch({ type: 'material_ack', acceptedSequence: 1, contextVersion: 2 });

  const enqueueOutputPhrase = vi.fn<LiveVoicePcmSession['enqueueOutputPhrase']>(async () => undefined);
  const cancelOutputItem = vi.fn(async () => undefined);
  const stop = vi.fn(async () => undefined);
  const pcm: LiveVoicePcmSession = {
    sampleRate: 24_000,
    enqueuePhrase: vi.fn(async () => undefined),
    enqueueOutputPhrase,
    enqueueSilence: vi.fn(async () => undefined),
    enqueueCue: vi.fn(async () => undefined),
    cancelSegment: vi.fn(),
    cancelOutputItem,
    cancelAllAfter: vi.fn(),
    waitForOutputItem: vi.fn(async () => undefined),
    setStartPolicy: vi.fn(),
    finish: vi.fn(async () => undefined),
    stop,
    isClosed: () => false,
  };
  let workletOptions: LiveVoicePcmSessionOptions | undefined;
  const createPcmSession = vi.fn(async (
    _traceId: string,
    _voiceId: string | null,
    _reporter: unknown,
    options?: LiveVoicePcmSessionOptions,
  ) => {
    workletOptions = options;
    return pcm;
  });
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
    const id = String(request.observation_id);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        observation_id: id,
        output_id: request.output_id,
        context_version: request.context_version,
        task_contract_id: 'editing',
        task_contract_version: 2,
        text: `Correction for ${id}.`,
        text_chars: `Correction for ${id}.`.length,
        estimated_speech_ms: 1_000,
      }),
    } as Response;
  });
  const reporter = {
    traceId: 'live-call:session-1:test',
    record: vi.fn(),
    flush: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
  };
  const coordinator = new LiveOutputCoordinator({
    store,
    fetcher,
    createPcmSession: createPcmSession as never,
    createReporter: vi.fn(() => reporter),
    createTraceId: vi.fn(() => reporter.traceId),
  });
  return {
    coordinator,
    store,
    pcm,
    enqueueOutputPhrase,
    cancelOutputItem,
    createPcmSession,
    fetcher,
    reporter,
    workletOptions: () => workletOptions,
  };
}

describe('LiveOutputCoordinator', () => {
  it('reuses one session-scoped PCM transport for ordered observation items', async () => {
    const harness = createHarness();
    await harness.coordinator.handleObservationCandidate({ observation: observation('one', 2) });
    await harness.coordinator.handleObservationCandidate({ observation: observation('two', 2) });

    expect(harness.createPcmSession).toHaveBeenCalledTimes(1);
    expect(harness.enqueueOutputPhrase).toHaveBeenCalledTimes(2);
    const firstOwnership = harness.enqueueOutputPhrase.mock.calls[0]![2];
    const secondOwnership = harness.enqueueOutputPhrase.mock.calls[1]![2];
    expect(firstOwnership.outputOrder).toBeLessThan(secondOwnership.outputOrder);
    expect(firstOwnership.outputId).not.toBe(secondOwnership.outputId);
    expect(harness.coordinator.snapshot.map((item) => item.status)).toEqual(['buffered', 'buffered']);

    harness.workletOptions()?.onWorkletEvent?.({
      type: 'segment_started',
      output_id: firstOwnership.outputId,
      generation_epoch: firstOwnership.generationEpoch,
    });
    harness.workletOptions()?.onWorkletEvent?.({
      type: 'segment_completed',
      output_id: firstOwnership.outputId,
      generation_epoch: firstOwnership.generationEpoch,
    });
    expect(harness.coordinator.snapshot[0].status).toBe('completed');
    expect(harness.coordinator.snapshot[0].delivery.audioDeliveredTextEnd).toBeGreaterThan(0);
    expect(harness.coordinator.snapshot[0].delivery.contextDeliveredTextEnd).toBe(
      harness.coordinator.snapshot[0].delivery.audioDeliveredTextEnd,
    );
  });

  it('cancels only the superseded observation and preserves unrelated queued audio', async () => {
    const harness = createHarness();
    await harness.coordinator.handleObservationCandidate({ observation: observation('one', 2) });
    await harness.coordinator.handleObservationCandidate({ observation: observation('two', 2) });

    const before = harness.coordinator.snapshot;
    await harness.coordinator.cancelObservationIds(['one'], 'source_self_corrected');

    expect(harness.cancelOutputItem).toHaveBeenCalledTimes(1);
    expect(harness.cancelOutputItem).toHaveBeenCalledWith(
      before[0].outputId,
      before[0].generationEpoch,
      'source_self_corrected',
    );
    expect(harness.coordinator.snapshot[0].status).toBe('cancelled');
    expect(harness.coordinator.snapshot[1].status).toBe('buffered');
  });

  it('rejects generated output when the task contract changes before playback', async () => {
    const harness = createHarness();
    let resolveResponse: ((response: Response) => void) | undefined;
    harness.fetcher.mockImplementationOnce(async (_input, init) => new Promise<Response>((resolve) => {
      resolveResponse = resolve;
      const request = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      queueMicrotask(() => {
        harness.store.dispatch({
          type: 'task_contract',
          contract: {
            ...harness.store.getState().coordination.taskContract,
            taskContractId: 'translation',
            version: 3,
            instruction: 'Translate into English.',
          },
        });
        resolve({
          ok: true,
          status: 200,
          json: async () => ({
            observation_id: request.observation_id,
            output_id: request.output_id,
            context_version: request.context_version,
            task_contract_id: 'editing',
            task_contract_version: 2,
            text: 'Stale correction.',
            text_chars: 17,
            estimated_speech_ms: 1_000,
          }),
        } as Response);
      });
    }));

    await harness.coordinator.handleObservationCandidate({ observation: observation('stale', 2) });
    expect(resolveResponse).toBeDefined();
    expect(harness.enqueueOutputPhrase).not.toHaveBeenCalled();
    expect(harness.coordinator.snapshot[0].status).toBe('cancelled');
  });
});
