import type { AcceptedVoiceFinal, LiveFinalRoutingResult } from './live-accepted-final';
import { liveChatSubmissionGateway, type LiveChatSubmissionGateway } from './live-chat-submission-gateway';
import { liveConversationStore, type LiveConversationStore } from './live-conversation-store';
import {
  classifyLiveInput,
  type LiveAcousticClass,
  type LiveInputCoordination,
} from './live-input-coordination';
import {
  liveMaterialClient,
  type LiveMaterialAcknowledgement,
  type LiveMaterialClient,
} from './live-material-client';
import {
  LiveObservationQueue,
  createObservationAnchor,
  fingerprint,
  type LiveObservation,
} from './live-observation-coordinator';
import {
  decideLiveTaskTransition,
  inferLiveTaskContract,
  type LiveTaskContract,
} from './live-task-contract';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent, LIVE_COORDINATION_TERMINAL_EVENT, LIVE_OBSERVATION_CANDIDATE_EVENT, LIVE_OBSERVATION_SUPERSEDED_EVENT, LIVE_TASK_CONTRACT_EVENT } from '../../../events/bus';



export type CoordinateLiveTranscriptInput = {
  text: string;
  segmentId: string;
  sourceSequence: number;
  startSample?: number;
  endSample?: number;
  assistantSpeaking: boolean;
  acousticClass?: LiveAcousticClass;
  echoConfidence?: number;
};

export type CoordinateLiveTranscriptResult = {
  coordination: LiveInputCoordination;
  taskContract: LiveTaskContract;
  materialAcknowledgement?: LiveMaterialAcknowledgement;
  observation?: LiveObservation;
  submitted: boolean;
};

type CoordinatorDependencies = {
  store: LiveConversationStore;
  materialClient: Pick<LiveMaterialClient, 'append' | 'acknowledgeTaskContract'>;
  chatGateway: Pick<LiveChatSubmissionGateway, 'submit'>;
  now: () => number;
  emit: typeof emitOmnixEvent;
};

const TASK_INSTRUCTION_PATTERN = /\b(?:translate|translation|interpret|correct my|correct the|grammar|proofread|coach my|pronunciation|just listen|stop correcting|summarize it)\b/i;

export class LiveSessionCoordinator {
  private readonly observations = new LiveObservationQueue();
  private readonly materialSequences = new Map<string, number>();
  private readonly coordinationLanes = new Map<string, Promise<void>>();
  private observationCounter = 0;

  constructor(private readonly dependencies: CoordinatorDependencies) {}

  setTaskContract(contract: LiveTaskContract): void {
    const previous = this.dependencies.store.getState().coordination.taskContract;
    const transition = decideLiveTaskTransition(previous, contract);
    this.dependencies.store.dispatch({ type: 'task_contract', contract });
    const superseded = transition.invalidatePendingObservations
      ? this.observations.invalidateTaskContract(contract)
      : [];
    if (superseded.length) this.dispatchSuperseded(superseded, 'task_contract_changed');
    this.publishObservationQueue();
    if (transition.cancelSpeakingOutput) {
      this.dependencies.emit(ASSISTANT_VOICE_INTERRUPT_EVENT, { source: 'task-contract-transition', intent: 'hard_stop', confidence: 1 });
    }
  }

  setTaskInstruction(instruction: string): LiveTaskContract {
    const current = this.dependencies.store.getState().coordination.taskContract;
    const contract = inferLiveTaskContract(instruction, current.version + 1);
    this.setTaskContract(contract);
    return contract;
  }

  async prepareTaskContract(sessionId: string, instruction?: string): Promise<LiveTaskContract> {
    const normalizedInstruction = instruction?.replace(/\s+/g, ' ').trim() ?? '';
    const current = this.dependencies.store.getState().coordination.taskContract;
    const contract = normalizedInstruction
      ? inferLiveTaskContract(normalizedInstruction, current.version + 1)
      : current;
    const acknowledgement = await this.dependencies.materialClient.acknowledgeTaskContract(sessionId, {
      task_contract_id: contract.taskContractId,
      task_contract_version: contract.version,
    });
    this.dependencies.store.dispatch({ type: 'session', sessionId });
    this.setTaskContract(contract);
    this.dependencies.store.dispatch({
      type: 'task_contract_ack',
      contextVersion: acknowledgement.context_version,
      taskContractId: acknowledgement.task_contract_id,
      taskContractVersion: acknowledgement.task_contract_version,
    });
    return contract;
  }

  routeAcceptedFinal(final: AcceptedVoiceFinal): Promise<LiveFinalRoutingResult> {
    return new Promise((resolve) => {
      const previous = this.coordinationLanes.get(final.chatSessionId) ?? Promise.resolve();
      const next = previous
        .catch(() => undefined)
        .then(async () => {
          const result = await this.routeAcceptedFinalNow(final);
          this.emitTerminal(final, result);
          resolve(result);
        });
      this.coordinationLanes.set(final.chatSessionId, next.then(() => undefined, () => undefined));
    });
  }

  async coordinate(input: CoordinateLiveTranscriptInput): Promise<CoordinateLiveTranscriptResult> {
    const text = input.text.replace(/\s+/g, ' ').trim();
    let state = this.dependencies.store.getState();
    let taskContract = state.coordination.taskContract;
    const isTaskInstruction = taskContract.taskContractId === 'conversation' && TASK_INSTRUCTION_PATTERN.test(text);
    if (isTaskInstruction) {
      taskContract = this.setTaskInstruction(text);
      state = this.dependencies.store.getState();
    }
    const coordination = isTaskInstruction
      ? {
          acousticClass: input.acousticClass ?? 'speech',
          inputRole: 'conversation' as const,
          action: input.assistantSpeaking ? 'interrupt_and_respond' as const : 'respond' as const,
          confidence: 0.96,
          reason: 'task_instruction',
        }
      : classifyLiveInput(text, {
          taskContract,
          assistantSpeaking: input.assistantSpeaking,
          acousticClass: input.acousticClass,
          echoConfidence: input.echoConfidence,
        });
    this.dependencies.store.dispatch({ type: 'coordination_action', action: coordination.action });
    this.dependencies.store.dispatch({ type: 'transcript_final', text });

    switch (coordination.action) {
      case 'ignore':
        return { coordination, taskContract, submitted: false };
      case 'stop_output':
        this.dispatchInterrupt('hard_stop', coordination.confidence);
        return { coordination, taskContract, submitted: false };
      case 'respond':
        await this.submitConversation(input, text, false);
        return { coordination, taskContract, submitted: true };
      case 'interrupt_and_respond':
        this.dispatchInterrupt('interrupt', coordination.confidence);
        await this.submitConversation(input, text, true);
        return { coordination, taskContract, submitted: true };
      case 'append':
      case 'append_and_observe': {
        const sessionId = state.sessionId;
        if (!sessionId) throw new Error('live_material_session_missing');
        const sequence = this.nextMaterialSequence(sessionId, state.coordination.acceptedSequence);
        const acknowledgement = await this.dependencies.materialClient.append(sessionId, {
          segment_id: input.segmentId,
          sequence,
          text,
          start_sample: input.startSample ?? 0,
          end_sample: input.endSample ?? 0,
          response_policy: coordination.action === 'append_and_observe' ? 'observe' : 'none',
          retention: taskContract.defaultRetention,
          task_contract_id: taskContract.taskContractId,
          task_contract_version: taskContract.version,
        });
        this.materialSequences.set(sessionId, acknowledgement.accepted_sequence + 1);
        this.dependencies.store.dispatch({
          type: 'material_ack',
          acceptedSequence: acknowledgement.accepted_sequence,
          contextVersion: acknowledgement.context_version,
        });
        if (coordination.action === 'append') {
          return { coordination, taskContract, materialAcknowledgement: acknowledgement, submitted: false };
        }
        const observation = this.createObservation(input, acknowledgement, taskContract);
        const admission = this.observations.admitCandidate(observation, {
          nowMs: this.dependencies.now(),
          contextVersion: acknowledgement.context_version,
          taskContract,
          deliveredEquivalenceKeys: new Set(),
          queuedSpeechMs: this.observations.queuedSpeechMs,
        });
        if (admission.admitted) {
          this.dependencies.emit(LIVE_OBSERVATION_CANDIDATE_EVENT, { observation, sourceText: text });
        }
        this.publishObservationQueue();
        return {
          coordination,
          taskContract,
          materialAcknowledgement: acknowledgement,
          observation: admission.admitted ? observation : undefined,
          submitted: false,
        };
      }
      default:
        return assertNever(coordination.action);
    }
  }

  markSelfCorrected(anchorId: string): string[] {
    const superseded = this.observations.markAnchorState(anchorId, 'self_corrected');
    if (superseded.length) this.dispatchSuperseded(superseded, 'source_self_corrected');
    this.publishObservationQueue();
    return superseded;
  }

  private async routeAcceptedFinalNow(final: AcceptedVoiceFinal): Promise<LiveFinalRoutingResult> {
    const state = this.dependencies.store.getState();
    const task = state.coordination.taskContract;
    try {
      if (state.sessionId !== final.chatSessionId) throw new Error('live_session_mismatch');
      const result = await this.coordinate({
        text: final.text,
        segmentId: final.segmentId,
        sourceSequence: final.sourceSequence,
        startSample: final.startSample,
        endSample: final.endSample,
        assistantSpeaking: state.conversation.assistantTurn === 'speaking',
        acousticClass: 'speech',
      });
      const outcome = result.coordination.action === 'ignore'
        ? 'ignored'
        : result.coordination.action === 'stop_output'
          ? 'control_executed'
          : result.submitted
            ? 'conversation_submitted'
            : result.observation
              ? 'observation_queued'
              : 'material_acked';
      return {
        outcome,
        segmentId: final.segmentId,
        sourceSequence: final.sourceSequence,
        taskContractId: result.taskContract.taskContractId,
        taskContractVersion: result.taskContract.version,
        contextVersion: result.materialAcknowledgement?.context_version,
      };
    } catch (error) {
      this.dependencies.store.dispatch({ type: 'capture_activity', activity: 'degraded' });
      return {
        outcome: 'failed',
        segmentId: final.segmentId,
        sourceSequence: final.sourceSequence,
        taskContractId: task.taskContractId,
        taskContractVersion: task.version,
        errorCode: error instanceof Error ? error.message : 'live_coordination_failed',
      };
    }
  }

  private nextMaterialSequence(sessionId: string, acceptedSequence: number): number {
    const known = this.materialSequences.get(sessionId);
    if (known !== undefined) return known;
    return acceptedSequence + 1;
  }

  private createObservation(
    input: CoordinateLiveTranscriptInput,
    acknowledgement: LiveMaterialAcknowledgement,
    taskContract: LiveTaskContract,
  ): LiveObservation {
    const anchor = createObservationAnchor(input.segmentId, input.text);
    const priority = /\b(?:danger|wrong recipient|password|credit card|social insurance|urgent)\b/i.test(input.text)
      ? 'critical'
      : taskContract.observationPolicy.minorFeedback === 'consolidate' ? 'deferred' : 'normal';
    return {
      observationId: `observation-${++this.observationCounter}`,
      basedOnSequence: input.sourceSequence,
      contextVersion: acknowledgement.context_version,
      taskContractId: taskContract.taskContractId,
      taskContractVersion: taskContract.version,
      anchors: [anchor],
      priority,
      status: 'candidate',
      createdAtMs: this.dependencies.now(),
      estimatedSpeechMs: Math.min(taskContract.observationPolicy.preferredMaximumSpeechMs, 3_000),
      equivalenceKey: `${taskContract.taskContractId}:${fingerprint(input.text)}`,
    };
  }

  private async submitConversation(input: CoordinateLiveTranscriptInput, text: string, interrupted: boolean): Promise<void> {
    const sessionId = this.dependencies.store.getState().sessionId;
    if (!sessionId) throw new Error('live_chat_session_missing');
    await this.dependencies.chatGateway.submit({
      sessionId,
      text,
      source: 'live_coordination',
      interrupted,
      segmentId: input.segmentId,
      sourceSequence: input.sourceSequence,
    });
  }

  private dispatchInterrupt(intent: string, confidence: number): void {
    this.dependencies.emit(ASSISTANT_VOICE_INTERRUPT_EVENT, { source: 'live-session-coordinator', intent, confidence });
  }

  private dispatchSuperseded(observationIds: string[], reason: string): void {
    this.dependencies.emit(LIVE_OBSERVATION_SUPERSEDED_EVENT, { observationIds, reason });
  }

  private emitTerminal(final: AcceptedVoiceFinal, result: LiveFinalRoutingResult): void {
    this.dependencies.emit(LIVE_COORDINATION_TERMINAL_EVENT, {
        captureEpoch: final.captureEpoch,
        segmentId: final.segmentId,
        resultId: final.resultId,
        sourceSequence: final.sourceSequence,
        outcome: result.outcome,
        contextVersion: result.contextVersion,
        errorCode: result.errorCode,
      });
  }

  private publishObservationQueue(): void {
    const active = this.observations.snapshot.filter((item) => item.status !== 'superseded' && item.status !== 'completed' && item.status !== 'failed');
    this.dependencies.store.dispatch({
      type: 'observation_queue',
      count: active.length,
      speechMs: this.observations.queuedSpeechMs,
    });
  }
}

export const liveSessionCoordinator = new LiveSessionCoordinator({
  store: liveConversationStore,
  materialClient: liveMaterialClient,
  chatGateway: liveChatSubmissionGateway,
  now: () => performance.now(),
  emit: emitOmnixEvent,
});

let initialized = false;

export function initializeLiveSessionCoordinator(): () => void {
  if (initialized || typeof window === 'undefined') return () => undefined;
  initialized = true;
  const handleTaskContract = (event: Event) => {
    const detail = (event as CustomEvent<{ instruction?: string; contract?: LiveTaskContract }>).detail;
    if (detail?.contract) liveSessionCoordinator.setTaskContract(detail.contract);
    else if (detail?.instruction?.trim()) liveSessionCoordinator.setTaskInstruction(detail.instruction);
  };
  window.addEventListener(LIVE_TASK_CONTRACT_EVENT, handleTaskContract);
  return () => {
    window.removeEventListener(LIVE_TASK_CONTRACT_EVENT, handleTaskContract);
    initialized = false;
  };
}

function assertNever(value: never): never {
  throw new Error(`Unsupported Live coordination action: ${String(value)}`);
}
