import { afterAll, describe, expect, it } from 'vitest';

import {
  createLiveConversationStore,
  INITIAL_LIVE_CONVERSATION_RUNTIME_STATE,
  type LiveConversationStore,
} from './live-conversation-store';
import { classifyLiveInput, type LiveCoordinationAction } from './live-input-coordination';
import type {
  LiveMaterialAcknowledgement,
  LiveMaterialAppendRequest,
} from './live-material-client';
import {
  createObservationAnchor,
  fingerprint,
  LiveObservationQueue,
  type LiveObservation,
  type LiveObservationPriority,
} from './live-observation-coordinator';
import { LiveOutputQueue } from './live-output-queue';
import { LiveSessionCoordinator } from './live-session-coordinator';
import { inferLiveTaskContract, type LiveTaskContract } from './live-task-contract';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, LIVE_OBSERVATION_CANDIDATE_EVENT, LIVE_OBSERVATION_SUPERSEDED_EVENT } from '../../../events/bus';

type ScenarioEvidence = {
  passed: boolean;
  assertions: number;
};

type FullDuplexEvidence = {
  schema_version: 1;
  content_policy: 'content_free';
  scenarios: Record<string, ScenarioEvidence>;
  metrics: {
    coordinated_segments: number;
    append_actions: number;
    observation_candidates: number;
    superseded_observations: number;
    direct_responses: number;
    interruptions: number;
    stop_controls: number;
    reconnects: number;
    backpressure_rejections: number;
    stale_epoch_rejections: number;
    soak_events: number;
    max_observation_queue_depth: number;
    max_output_queue_depth: number;
    max_queued_speech_ms: number;
  };
  action_counts: Record<LiveCoordinationAction, number>;
  deterministic_digest: string;
};

const evidence: FullDuplexEvidence = {
  schema_version: 1,
  content_policy: 'content_free',
  scenarios: {},
  metrics: {
    coordinated_segments: 0,
    append_actions: 0,
    observation_candidates: 0,
    superseded_observations: 0,
    direct_responses: 0,
    interruptions: 0,
    stop_controls: 0,
    reconnects: 0,
    backpressure_rejections: 0,
    stale_epoch_rejections: 0,
    soak_events: 0,
    max_observation_queue_depth: 0,
    max_output_queue_depth: 0,
    max_queued_speech_ms: 0,
  },
  action_counts: {
    append: 0,
    append_and_observe: 0,
    respond: 0,
    interrupt_and_respond: 0,
    stop_output: 0,
    ignore: 0,
  },
  deterministic_digest: '',
};

class DeterministicMaterialClient {
  readonly requests: Array<{ sessionId: string; request: LiveMaterialAppendRequest }> = [];
  private acceptedSequence = -1;
  private contextVersion = 0;

  constructor(initialAcceptedSequence = -1, initialContextVersion = 0) {
    this.acceptedSequence = initialAcceptedSequence;
    this.contextVersion = initialContextVersion;
  }

  async acknowledgeTaskContract(sessionId: string, request: { task_contract_id: string; task_contract_version: number }) {
    return { session_id: sessionId, task_contract_id: request.task_contract_id, task_contract_version: request.task_contract_version, context_version: this.contextVersion, idempotent: true };
  }

  async append(sessionId: string, request: LiveMaterialAppendRequest): Promise<LiveMaterialAcknowledgement> {
    if (request.sequence !== this.acceptedSequence + 1) throw new Error('segment_sequence_gap');
    this.requests.push({ sessionId, request });
    this.acceptedSequence = request.sequence;
    this.contextVersion += 1;
    return {
      segment_id: request.segment_id,
      accepted_sequence: this.acceptedSequence,
      context_version: this.contextVersion,
      task_contract_id: request.task_contract_id ?? 'default',
      task_contract_version: request.task_contract_version ?? 1,
      retention: request.retention ?? 'ephemeral_session',
      response_policy: request.response_policy ?? 'none',
      idempotent: false,
      exact_segment_count: this.requests.length,
      exact_text_chars: this.requests.length * 16,
      security: {
        instruction_authority: 'none',
        tool_eligibility: 'none',
        memory_write_eligibility: false,
        task_contract_mutation: false,
      },
    };
  }
}

type RecordedEvent = { type: string; detail: unknown };

function createCoordinatorHarness(options: {
  acceptedSequence?: number;
  contextVersion?: number;
  taskContract?: LiveTaskContract;
} = {}): {
  store: LiveConversationStore;
  material: DeterministicMaterialClient;
  coordinator: LiveSessionCoordinator;
  events: RecordedEvent[];
} {
  const store = createLiveConversationStore(INITIAL_LIVE_CONVERSATION_RUNTIME_STATE);
  store.dispatch({ type: 'session', sessionId: 'evaluation-session' });
  if (options.acceptedSequence !== undefined || options.contextVersion !== undefined) {
    store.dispatch({
      type: 'material_ack',
      acceptedSequence: options.acceptedSequence ?? -1,
      contextVersion: options.contextVersion ?? 0,
    });
  }
  if (options.taskContract) store.dispatch({ type: 'task_contract', contract: options.taskContract });
  const material = new DeterministicMaterialClient(
    options.acceptedSequence ?? -1,
    options.contextVersion ?? 0,
  );
  const events: RecordedEvent[] = [];
  let now = 1_000;
  const coordinator = new LiveSessionCoordinator({
    store,
    materialClient: material,
    chatGateway: { submit: async (input) => { events.push({ type: 'live-chat-gateway-submit', detail: input }); } },
    now: () => now++,
    emit: (type, ...[detail]) => {
      events.push({ type, detail });
    },
  });
  return { store, material, coordinator, events };
}

function recordScenario(name: string, assertions: number): void {
  evidence.scenarios[name] = { passed: true, assertions };
}

function countAction(action: LiveCoordinationAction): void {
  evidence.action_counts[action] += 1;
  evidence.metrics.coordinated_segments += 1;
  if (action === 'append' || action === 'append_and_observe') evidence.metrics.append_actions += 1;
  if (action === 'respond' || action === 'interrupt_and_respond') evidence.metrics.direct_responses += 1;
  if (action === 'interrupt_and_respond') evidence.metrics.interruptions += 1;
  if (action === 'stop_output') evidence.metrics.stop_controls += 1;
}

function observation(
  index: number,
  taskContract: LiveTaskContract,
  priority: LiveObservationPriority = 'normal',
  estimatedSpeechMs = 900,
): LiveObservation {
  const segmentId = `segment-${index}`;
  return {
    observationId: `observation-${index}`,
    basedOnSequence: index,
    contextVersion: index + 1,
    taskContractId: taskContract.taskContractId,
    taskContractVersion: taskContract.version,
    anchors: [createObservationAnchor(segmentId, `fixture-${index}`)],
    priority,
    status: 'candidate',
    createdAtMs: index * 10,
    estimatedSpeechMs,
    equivalenceKey: `${taskContract.taskContractId}:${index}`,
  };
}

describe('deterministic full-duplex Live acceptance', () => {
  it('keeps translation source material separate from conversation and control', async () => {
    const harness = createCoordinatorHarness();
    const contract = harness.coordinator.setTaskInstruction('Translate Japanese speech into English as I listen.');
    expect(contract.taskContractId).toBe('translation');
    expect(contract.inputRole).toBe('ongoing_material');

    const material = await harness.coordinator.coordinate({
      text: 'これは継続中の素材です。',
      segmentId: 'translation-0',
      sourceSequence: 0,
      assistantSpeaking: false,
    });
    countAction(material.coordination.action);
    expect(material.coordination.action).toBe('append_and_observe');
    expect(material.submitted).toBe(false);
    expect(harness.events.some((event) => event.type === LIVE_OBSERVATION_CANDIDATE_EVENT)).toBe(true);

    const quotedQuestion = await harness.coordinator.coordinate({
      text: '“Why are you doing this?”',
      segmentId: 'translation-1',
      sourceSequence: 1,
      assistantSpeaking: true,
    });
    countAction(quotedQuestion.coordination.action);
    expect(quotedQuestion.coordination.action).toBe('append_and_observe');
    expect(quotedQuestion.submitted).toBe(false);

    const quotedStop = await harness.coordinator.coordinate({
      text: 'Stop by the store after work.',
      segmentId: 'translation-2',
      sourceSequence: 2,
      assistantSpeaking: true,
    });
    countAction(quotedStop.coordination.action);
    expect(quotedStop.coordination.action).toBe('append_and_observe');

    const directQuestion = await harness.coordinator.coordinate({
      text: 'Maya, what does that phrase mean?',
      segmentId: 'translation-3',
      sourceSequence: 3,
      assistantSpeaking: true,
    });
    countAction(directQuestion.coordination.action);
    expect(directQuestion.coordination.action).toBe('interrupt_and_respond');
    expect(harness.events.some((event) => event.type === 'live-chat-gateway-submit')).toBe(true);
    expect(harness.events.some((event) => event.type === ASSISTANT_VOICE_INTERRUPT_EVENT)).toBe(true);

    const stop = await harness.coordinator.coordinate({
      text: 'Maya, stop.',
      segmentId: 'translation-4',
      sourceSequence: 4,
      assistantSpeaking: true,
    });
    countAction(stop.coordination.action);
    expect(stop.coordination.action).toBe('stop_output');
    expect(harness.material.requests).toHaveLength(3);

    evidence.metrics.observation_candidates += harness.events.filter(
      (event) => event.type === LIVE_OBSERVATION_CANDIDATE_EVENT,
    ).length;
    recordScenario('translation_source_command_separation', 14);
  });

  it('cancels editing feedback on self-correction and task changes', async () => {
    const harness = createCoordinatorHarness();
    harness.coordinator.setTaskInstruction('Correct important grammar mistakes while I read this email.');
    const first = await harness.coordinator.coordinate({
      text: 'There is less mistakes in the draft.',
      segmentId: 'editing-0',
      sourceSequence: 0,
      assistantSpeaking: false,
    });
    countAction(first.coordination.action);
    expect(first.observation).toBeDefined();
    const firstAnchor = first.observation!.anchors[0].anchorId;
    const selfCorrected = harness.coordinator.markSelfCorrected(firstAnchor);
    expect(selfCorrected).toEqual([first.observation!.observationId]);

    const second = await harness.coordinator.coordinate({
      text: 'The recipients is listed below.',
      segmentId: 'editing-1',
      sourceSequence: 1,
      assistantSpeaking: false,
    });
    countAction(second.coordination.action);
    expect(second.observation).toBeDefined();
    harness.coordinator.setTaskInstruction('Translate the remaining material into English.');

    const supersededEvents = harness.events.filter((event) => event.type === LIVE_OBSERVATION_SUPERSEDED_EVENT);
    expect(supersededEvents).toHaveLength(2);
    const supersededCount = supersededEvents.reduce((total, event) => {
      const detail = event.detail as { observationIds?: string[] };
      return total + (detail.observationIds?.length ?? 0);
    }, 0);
    expect(supersededCount).toBe(2);
    evidence.metrics.superseded_observations += supersededCount;
    evidence.metrics.observation_candidates += 2;
    recordScenario('editing_self_correction_and_task_change', 5);
  });

  it('enforces observation and output backpressure with targeted cancellation', () => {
    const taskContract = inferLiveTaskContract('Correct grammar while I read.', 4);
    const observations = new LiveObservationQueue({ maxItems: 3, maxQueuedSpeechMs: 2_000 });
    const context = (queuedSpeechMs: number) => ({
      nowMs: 100,
      contextVersion: 10,
      taskContract,
      deliveredEquivalenceKeys: new Set<string>(),
      queuedSpeechMs,
    });
    expect(observations.admitCandidate(observation(0, taskContract), context(0)).admitted).toBe(true);
    expect(observations.admitCandidate(observation(1, taskContract), context(observations.queuedSpeechMs)).admitted).toBe(true);
    const speechRejected = observations.admitCandidate(
      observation(2, taskContract, 'normal', 500),
      context(observations.queuedSpeechMs),
    );
    expect(speechRejected.reason).toBe('speech_backpressure');
    const critical = observations.admitCandidate(
      observation(3, taskContract, 'critical', 500),
      context(observations.queuedSpeechMs),
    );
    expect(critical.admitted).toBe(true);
    const itemRejected = observations.admitCandidate(
      observation(4, taskContract, 'critical', 100),
      context(observations.queuedSpeechMs),
    );
    expect(itemRejected.reason).toBe('observation_item_limit');

    const outputs = new LiveOutputQueue({ maxItems: 3, maxSpeechMs: 2_000 });
    const first = outputs.enqueue(outputInput('output-a', taskContract, 900));
    const second = outputs.enqueue(outputInput('output-b', taskContract, 900));
    expect(() => outputs.enqueue(outputInput('output-c', taskContract, 500))).toThrow('output_speech_backpressure');
    outputs.enqueue(outputInput('output-critical', taskContract, 500, 'critical'));
    expect(() => outputs.enqueue(outputInput('output-overflow', taskContract, 100, 'critical'))).toThrow('output_item_limit');
    expect(outputs.cancel(first.outputId, first.generationEpoch, 'superseded')).not.toBeNull();
    expect(outputs.acceptsFrame(second.outputId, second.generationEpoch)).toBe(true);
    const replacement = outputs.enqueue(outputInput(first.outputId, taskContract, 100));
    expect(replacement.generationEpoch).toBeGreaterThan(first.generationEpoch);
    expect(outputs.acceptsFrame(first.outputId, first.generationEpoch)).toBe(false);
    expect(() => outputs.updateDelivery(replacement.outputId, replacement.generationEpoch, {
      contextDeliveredTextEnd: 1,
    })).toThrow('context_delivery_exceeds_user_delivery');

    evidence.metrics.backpressure_rejections += 4;
    evidence.metrics.stale_epoch_rejections += 1;
    evidence.metrics.max_observation_queue_depth = Math.max(
      evidence.metrics.max_observation_queue_depth,
      observations.snapshot.length,
    );
    evidence.metrics.max_output_queue_depth = Math.max(
      evidence.metrics.max_output_queue_depth,
      outputs.snapshot.length,
    );
    evidence.metrics.max_queued_speech_ms = Math.max(
      evidence.metrics.max_queued_speech_ms,
      observations.queuedSpeechMs,
      outputs.activeSpeechMs,
    );
    recordScenario('bounded_backpressure_and_targeted_cancellation', 12);
  });

  it('continues material sequence after a reconnect snapshot', async () => {
    const taskContract = inferLiveTaskContract('Correct grammar while I read.', 3);
    const harness = createCoordinatorHarness({
      acceptedSequence: 7,
      contextVersion: 8,
      taskContract,
    });
    const result = await harness.coordinator.coordinate({
      text: 'A continued source segment.',
      segmentId: 'reconnect-8',
      sourceSequence: 8,
      assistantSpeaking: false,
    });
    countAction(result.coordination.action);
    expect(result.coordination.action).toBe('append_and_observe');
    expect(harness.material.requests[0].request.sequence).toBe(8);
    expect(result.materialAcknowledgement?.accepted_sequence).toBe(8);
    expect(result.materialAcknowledgement?.context_version).toBe(9);
    evidence.metrics.reconnects += 1;
    recordScenario('reconnect_sequence_continuity', 4);
  });

  it('runs a bounded deterministic 1000-event coordination soak', () => {
    const taskContract = inferLiveTaskContract('Correct grammar while I read.', 5);
    const queue = new LiveObservationQueue({
      maxItems: 12,
      maxQueuedSpeechMs: 15_000,
      maxAgeMs: 20_000,
    });
    let rejected = 0;
    let maximumDepth = 0;
    let maximumSpeech = 0;
    const localCounts: Record<LiveCoordinationAction, number> = {
      append: 0,
      append_and_observe: 0,
      respond: 0,
      interrupt_and_respond: 0,
      stop_output: 0,
      ignore: 0,
    };

    for (let index = 0; index < 1_000; index += 1) {
      const text = index % 100 === 0
        ? 'Maya, what does this mean?'
        : index % 37 === 0
          ? 'Maya, stop.'
          : `fixture-${index}`;
      const decision = classifyLiveInput(text, {
        taskContract,
        assistantSpeaking: index % 2 === 0,
        acousticClass: index % 211 === 0 ? 'assistant_echo' : 'speech',
        echoConfidence: index % 211 === 0 ? 0.99 : 0,
      });
      localCounts[decision.action] += 1;
      evidence.action_counts[decision.action] += 1;
      if (decision.action === 'append_and_observe') {
        const candidate = observation(index + 100, taskContract, 'normal', 400);
        const admission = queue.admitCandidate(candidate, {
          nowMs: index * 10,
          contextVersion: index + 101,
          taskContract,
          deliveredEquivalenceKeys: new Set<string>(),
          queuedSpeechMs: queue.queuedSpeechMs,
        });
        if (!admission.admitted) rejected += 1;
      }
      if (index % 4 === 0) {
        const active = queue.snapshot.find((item) => item.status === 'candidate');
        const anchor = active?.anchors[0];
        if (anchor) queue.markAnchorState(anchor.anchorId, 'superseded');
        queue.prune(index * 10);
      }
      maximumDepth = Math.max(maximumDepth, queue.snapshot.length);
      maximumSpeech = Math.max(maximumSpeech, queue.queuedSpeechMs);
      expect(queue.snapshot.length).toBeLessThanOrEqual(12);
      expect(queue.queuedSpeechMs).toBeLessThanOrEqual(15_000);
    }

    expect(Object.values(localCounts).reduce((total, count) => total + count, 0)).toBe(1_000);
    expect(localCounts.append_and_observe).toBeGreaterThan(900);
    expect(localCounts.interrupt_and_respond).toBeGreaterThan(0);
    expect(localCounts.stop_output).toBeGreaterThan(0);
    expect(localCounts.ignore).toBeGreaterThan(0);
    evidence.metrics.soak_events += 1_000;
    evidence.metrics.backpressure_rejections += rejected;
    evidence.metrics.max_observation_queue_depth = Math.max(
      evidence.metrics.max_observation_queue_depth,
      maximumDepth,
    );
    evidence.metrics.max_queued_speech_ms = Math.max(
      evidence.metrics.max_queued_speech_ms,
      maximumSpeech,
    );
    recordScenario('deterministic_1000_event_soak', 2_005);
  });
});

function outputInput(
  outputId: string,
  taskContract: LiveTaskContract,
  estimatedSpeechMs: number,
  priority: 'normal' | 'critical' = 'normal',
) {
  return {
    outputId,
    taskContractId: taskContract.taskContractId,
    taskContractVersion: taskContract.version,
    contextVersion: 1,
    anchorIds: [`anchor-${outputId}`],
    priority,
    estimatedSpeechMs,
  };
}

afterAll(() => {
  const digestInput = JSON.stringify({
    scenarios: evidence.scenarios,
    metrics: evidence.metrics,
    action_counts: evidence.action_counts,
  });
  evidence.deterministic_digest = fingerprint(digestInput);
  console.log(`LIVE_FULL_DUPLEX_EVIDENCE=${JSON.stringify(evidence)}`);
});
