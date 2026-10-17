import { describe, expect, it, vi } from 'vitest';

import { createLiveConversationStore } from './live-conversation-store';
import { classifyLiveInput } from './live-input-coordination';
import {
  LiveObservationQueue,
  createObservationAnchor,
  type LiveObservation,
} from './live-observation-coordinator';
import { LiveSessionCoordinator } from './live-session-coordinator';
import { inferLiveTaskContract } from './live-task-contract';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, type emitOmnixEvent, LIVE_OBSERVATION_CANDIDATE_EVENT, OmnixEvent, type OmnixEventMap } from '../../../events/bus';

function materialAck(sequence: number, contextVersion = sequence + 1) {
  return {
    segment_id: `segment-${sequence}`,
    accepted_sequence: sequence,
    context_version: contextVersion,
    task_contract_id: 'editing',
    task_contract_version: 2,
    retention: 'ephemeral_session' as const,
    response_policy: 'observe' as const,
    idempotent: false,
    exact_segment_count: sequence + 1,
    exact_text_chars: 12,
    security: {
      instruction_authority: 'none' as const,
      tool_eligibility: 'none' as const,
      memory_write_eligibility: false as const,
      task_contract_mutation: false as const,
    },
  };
}

/** An emit that records each event instead of dispatching it. */
function recordEvents(events: Event[]): typeof emitOmnixEvent {
  return (type, ...[detail]) => {
    events.push(new OmnixEvent(type, (detail ?? null) as OmnixEventMap[typeof type]));
  };
}

describe('Live input coordination', () => {
  it('keeps embedded stop words as material', () => {
    const contract = inferLiveTaskContract('Correct my grammar while I read.', 2);
    expect(classifyLiveInput('Stop by the store before the meeting.', {
      taskContract: contract,
      assistantSpeaking: true,
    })).toMatchObject({ inputRole: 'ongoing_material', action: 'append_and_observe' });
  });

  it('uses an addressed stop as a hard control', () => {
    const contract = inferLiveTaskContract('Correct my grammar while I read.', 2);
    expect(classifyLiveInput('Maya, stop.', {
      taskContract: contract,
      assistantSpeaking: true,
    })).toMatchObject({ inputRole: 'assistant_control', action: 'stop_output' });
  });

  it('distinguishes interrupting questions and backchannels', () => {
    const contract = inferLiveTaskContract('Have a normal conversation.', 2);
    expect(classifyLiveInput('Wait, why?', { taskContract: contract, assistantSpeaking: true }).action)
      .toBe('interrupt_and_respond');
    expect(classifyLiveInput('mhm', { taskContract: contract, assistantSpeaking: true }).action)
      .toBe('ignore');
  });
});

describe('LiveSessionCoordinator', () => {
  it('appends untrusted material without normal chat submission', async () => {
    const store = createLiveConversationStore();
    store.dispatch({ type: 'session', sessionId: 'chat:one' });
    store.dispatch({ type: 'task_contract', contract: inferLiveTaskContract('Correct my grammar while I read.', 2) });
    const append = vi.fn(async () => materialAck(0));
    const events: Event[] = [];
    const coordinator = new LiveSessionCoordinator({
      store,
      materialClient: { append, acknowledgeTaskContract: vi.fn() },
      chatGateway: { submit: vi.fn() },
      now: () => 100,
      emit: recordEvents(events),
    });

    const result = await coordinator.coordinate({
      text: 'Stop by the store before the meeting.',
      segmentId: 'source-0',
      sourceSequence: 4,
      assistantSpeaking: true,
    });

    expect(result.coordination.action).toBe('append_and_observe');
    expect(append).toHaveBeenCalledWith('chat:one', expect.objectContaining({
      segment_id: 'source-0',
      sequence: 0,
      response_policy: 'observe',
      retention: 'ephemeral_session',
      task_contract_id: 'editing',
    }));
    expect(events.some((event) => event.type === 'live-chat-gateway-submit')).toBe(false);
    expect(events.some((event) => event.type === LIVE_OBSERVATION_CANDIDATE_EVENT)).toBe(true);
    expect(store.getState().coordination).toMatchObject({ acceptedSequence: 0, contextVersion: 1, queuedObservationCount: 1 });
  });

  it('routes controls and conversation through distinct effects', async () => {
    const store = createLiveConversationStore();
    store.dispatch({ type: 'session', sessionId: 'chat:two' });
    const events: Event[] = [];
    const submit = vi.fn(async () => undefined);
    const coordinator = new LiveSessionCoordinator({
      store,
      materialClient: { append: vi.fn(), acknowledgeTaskContract: vi.fn() },
      chatGateway: { submit },
      now: () => 100,
      emit: recordEvents(events),
    });

    await coordinator.coordinate({ text: 'Maya, stop.', segmentId: 's0', sourceSequence: 0, assistantSpeaking: true });
    await coordinator.coordinate({ text: 'Wait, why?', segmentId: 's1', sourceSequence: 1, assistantSpeaking: true });
    await coordinator.coordinate({ text: 'mhm', segmentId: 's2', sourceSequence: 2, assistantSpeaking: true });

    expect(events.filter((event) => event.type === ASSISTANT_VOICE_INTERRUPT_EVENT)).toHaveLength(2);
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('versions a task instruction before later material arrives', async () => {
    const store = createLiveConversationStore();
    store.dispatch({ type: 'session', sessionId: 'chat:three' });
    const events: Event[] = [];
    const coordinator = new LiveSessionCoordinator({
      store,
      materialClient: { append: vi.fn(), acknowledgeTaskContract: vi.fn() },
      chatGateway: { submit: vi.fn() },
      now: () => 100,
      emit: recordEvents(events),
    });

    const result = await coordinator.coordinate({
      text: 'Translate the Japanese audio as it plays.',
      segmentId: 'instruction',
      sourceSequence: 0,
      assistantSpeaking: false,
    });

    expect(result.submitted).toBe(true);
    expect(store.getState().coordination.taskContract).toMatchObject({ taskContractId: 'translation', version: 2 });
  });
});

describe('Live observation admission', () => {
  it('suppresses self-corrected, stale, duplicated, and backpressured observations', () => {
    const contract = inferLiveTaskContract('Correct my grammar while I read.', 2);
    const queue = new LiveObservationQueue({ maxItems: 2, maxQueuedSpeechMs: 1_500, maxAgeMs: 1_000 });
    const observation: LiveObservation = {
      observationId: 'o1',
      basedOnSequence: 0,
      contextVersion: 1,
      taskContractId: contract.taskContractId,
      taskContractVersion: contract.version,
      anchors: [createObservationAnchor('s0', 'going good')],
      priority: 'normal',
      status: 'candidate',
      createdAtMs: 0,
      estimatedSpeechMs: 1_000,
      equivalenceKey: 'grammar:going-good',
    };
    const context = {
      nowMs: 100,
      contextVersion: 1,
      taskContract: contract,
      deliveredEquivalenceKeys: new Set<string>(),
      queuedSpeechMs: 0,
    };
    expect(queue.admitCandidate(observation, context).admitted).toBe(true);
    expect(queue.admitCandidate({ ...observation, observationId: 'o2' }, context).reason).toBe('equivalent_observation_pending');
    expect(queue.transition('o1', 'generating', { ...context, queuedSpeechMs: 1_000 }).reason).toBe('speech_backpressure');

    const second = { ...observation, observationId: 'o3', equivalenceKey: 'grammar:other', anchors: [createObservationAnchor('s1', 'other')] };
    expect(queue.admitCandidate(second, context).admitted).toBe(true);
    const anchorId = second.anchors[0].anchorId;
    expect(queue.markAnchorState(anchorId, 'self_corrected')).toContain('o3');
  });
});
