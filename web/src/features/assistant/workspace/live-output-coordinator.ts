import {
  createLiveCallDiagnosticsReporter,
  createLiveCallTraceId,
  type LiveCallDiagnosticsReporter,
} from './live-call-diagnostics-client';
import { liveConversationStore, type LiveConversationStore } from './live-conversation-store';
import type { LiveObservation } from './live-observation-coordinator';
import { LiveOutputQueue, type LiveOutputItem } from './live-output-queue';
import {
  createLiveVoicePcmSession,
  type LiveVoicePcmSession,
  type LiveVoicePcmSessionOptions,
} from './live-voice-pcm-session';
import { liveStreamFetch } from '../../../api/transport';
import type { components } from '../api/generated';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent, LIVE_OBSERVATION_CANDIDATE_EVENT, LIVE_OBSERVATION_SUPERSEDED_EVENT } from '../../../events/bus';


const LIVE_OBSERVATION_GENERATION_PATH = (sessionId: string): string =>
  `/api/chat/sessions/${encodeURIComponent(sessionId)}/live/observations/generate`;

export type LiveObservationCandidateDetail = {
  observation: LiveObservation;
  sourceText?: string;
};

export type LiveObservationSupersededDetail = {
  observationIds: string[];
  reason: string;
};

type ObservationGenerationResponse = components['schemas']['LiveObservationGenerationResponse'];

type PcmFactory = (
  traceId: string,
  voiceId: string | null,
  reporter: LiveCallDiagnosticsReporter,
  options?: LiveVoicePcmSessionOptions,
) => Promise<LiveVoicePcmSession>;

type LiveOutputCoordinatorDependencies = {
  store: LiveConversationStore;
  fetcher: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  createPcmSession: PcmFactory;
  createReporter: typeof createLiveCallDiagnosticsReporter;
  createTraceId: typeof createLiveCallTraceId;
};

export class LiveOutputCoordinator {
  private readonly queue = new LiveOutputQueue({ maxItems: 24, maxSpeechMs: 30_000 });
  private readonly observationOutputs = new Map<string, string>();
  private sessionId: string | null = null;
  private sessionPromise: Promise<LiveVoicePcmSession> | null = null;
  private session: LiveVoicePcmSession | null = null;
  private reporter: LiveCallDiagnosticsReporter | null = null;
  private phraseIndex = 0;

  constructor(private readonly dependencies: LiveOutputCoordinatorDependencies) {}

  get snapshot(): readonly LiveOutputItem[] {
    return this.queue.snapshot;
  }

  async handleObservationCandidate(detail: LiveObservationCandidateDetail): Promise<void> {
    const state = this.dependencies.store.getState();
    const sessionId = state.sessionId;
    if (!sessionId) return;
    const observation = detail.observation;
    const outputId = this.outputId(sessionId, observation.observationId);
    let item: LiveOutputItem;
    try {
      item = this.queue.enqueue({
        outputId,
        observationId: observation.observationId,
        taskContractId: observation.taskContractId,
        taskContractVersion: observation.taskContractVersion,
        contextVersion: observation.contextVersion,
        anchorIds: observation.anchors.map((anchor) => anchor.anchorId),
        priority: observation.priority,
        estimatedSpeechMs: observation.estimatedSpeechMs,
      });
    } catch {
      return;
    }
    this.observationOutputs.set(observation.observationId, outputId);
    await this.generateAndQueue(sessionId, observation, item);
  }

  async cancelObservationIds(observationIds: readonly string[], reason: string): Promise<void> {
    await Promise.all(observationIds.map(async (observationId) => {
      const outputId = this.observationOutputs.get(observationId);
      if (!outputId) return;
      const item = this.queue.snapshot.find((candidate) => candidate.outputId === outputId);
      if (!item || !this.queue.cancel(outputId, item.generationEpoch, reason)) return;
      const session = this.session;
      if (session && !session.isClosed()) {
        await session.cancelOutputItem(outputId, item.generationEpoch, reason);
      }
    }));
  }

  async interrupt(reason = 'live_interrupt'): Promise<void> {
    const active = this.queue.snapshot.filter((item) => !isTerminal(item));
    await Promise.all(active.map(async (item) => {
      this.queue.cancel(item.outputId, item.generationEpoch, reason);
      if (this.session && !this.session.isClosed()) {
        await this.session.cancelOutputItem(item.outputId, item.generationEpoch, reason);
      }
    }));
  }

  async stop(reason = 'live_output_coordinator_stopped'): Promise<void> {
    const session = this.session ?? await this.sessionPromise?.catch(() => null) ?? null;
    this.session = null;
    this.sessionPromise = null;
    this.sessionId = null;
    if (session && !session.isClosed()) await session.stop(reason);
    const reporter = this.reporter;
    this.reporter = null;
    if (reporter) await reporter.close('live_output_session_closed', { reason });
  }

  handleWorkletEvent(event: Record<string, unknown>): void {
    const outputId = typeof event.output_id === 'string' ? event.output_id : '';
    const generationEpoch = typeof event.generation_epoch === 'number' ? event.generation_epoch : -1;
    if (!outputId || generationEpoch < 0 || !this.queue.acceptsFrame(outputId, generationEpoch)) return;
    const item = this.queue.snapshot.find((candidate) => candidate.outputId === outputId);
    if (!item) return;
    const eventType = typeof event.type === 'string' ? event.type : '';
    try {
      if (eventType === 'buffered' && item.status === 'generating') {
        this.queue.transition(outputId, generationEpoch, 'buffered');
      } else if (eventType === 'segment_started' && item.status !== 'playing') {
        this.queue.transition(outputId, generationEpoch, 'playing');
      } else if (eventType === 'segment_completed') {
        const delivered = item.delivery.generatedTextEnd;
        this.queue.updateDelivery(outputId, generationEpoch, {
          audioBufferedTextEnd: delivered,
          audioDeliveredTextEnd: delivered,
          contextDeliveredTextEnd: delivered,
        });
        this.queue.transition(outputId, generationEpoch, 'completed');
      } else if (eventType === 'segment_cancelled' || eventType === 'segment_interrupted') {
        this.queue.cancel(outputId, generationEpoch, String(event.reason ?? eventType));
      }
    } catch {
      // Late worklet events are ignored after a newer epoch or terminal transition wins.
    }
  }

  private async generateAndQueue(
    sessionId: string,
    observation: LiveObservation,
    item: LiveOutputItem,
  ): Promise<void> {
    try {
      this.queue.transition(item.outputId, item.generationEpoch, 'generating');
      const taskContract = this.dependencies.store.getState().coordination.taskContract;
      const response = await this.dependencies.fetcher(LIVE_OBSERVATION_GENERATION_PATH(sessionId), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          observation_id: observation.observationId,
          output_id: item.outputId,
          context_version: observation.contextVersion,
          task_contract_id: observation.taskContractId,
          task_contract_version: observation.taskContractVersion,
          task_instruction: taskContract.instruction,
          priority: observation.priority,
          anchor_ids: observation.anchors.map((anchor) => anchor.anchorId),
          preferred_maximum_speech_ms: taskContract.observationPolicy.preferredMaximumSpeechMs,
        }),
      });
      if (!response.ok) throw new Error(`live_observation_generation_${response.status}`);
      const generated = await response.json() as ObservationGenerationResponse;
      if (!this.matchesCurrentContract(sessionId, observation, generated)) {
        await this.cancelObservationIds([observation.observationId], 'generation_contract_stale');
        return;
      }
      const text = generated.text.trim();
      if (!text) {
        this.queue.transition(item.outputId, item.generationEpoch, 'completed');
        return;
      }
      this.queue.updateDelivery(item.outputId, item.generationEpoch, {
        generatedTextEnd: text.length,
        visualDeliveredTextEnd: text.length,
      });
      emitOmnixEvent('omnix:live-observation-text', {
          observationId: observation.observationId,
          outputId: item.outputId,
          text,
          priority: observation.priority,
        });
      const session = await this.ensureSession(sessionId);
      if (!this.queue.acceptsFrame(item.outputId, item.generationEpoch)) return;
      await session.enqueueOutputPhrase(text, this.phraseIndex++, {
        outputId: item.outputId,
        generationEpoch: item.generationEpoch,
        outputOrder: item.outputOrder,
      });
      const current = this.queue.snapshot.find((candidate) => candidate.outputId === item.outputId);
      if (current?.status === 'generating') {
        this.queue.updateDelivery(item.outputId, item.generationEpoch, { audioBufferedTextEnd: text.length });
        this.queue.transition(item.outputId, item.generationEpoch, 'buffered');
      }
    } catch (error) {
      const current = this.queue.snapshot.find((candidate) => candidate.outputId === item.outputId);
      if (!current || isTerminal(current)) return;
      try {
        this.queue.transition(item.outputId, item.generationEpoch, 'failed');
      } catch {
        // A concurrent cancellation is authoritative.
      }
      this.reporter?.record('live_output_failed', {
        output_id: item.outputId,
        generation_epoch: item.generationEpoch,
        error: error instanceof Error ? error.message : String(error),
      }, 'live_output_coordinator');
    }
  }

  private matchesCurrentContract(
    sessionId: string,
    observation: LiveObservation,
    generated: ObservationGenerationResponse,
  ): boolean {
    const state = this.dependencies.store.getState();
    return state.sessionId === sessionId
      && generated.observation_id === observation.observationId
      && generated.context_version === observation.contextVersion
      && generated.task_contract_id === state.coordination.taskContract.taskContractId
      && generated.task_contract_version === state.coordination.taskContract.version;
  }

  private async ensureSession(sessionId: string): Promise<LiveVoicePcmSession> {
    if (this.sessionId !== sessionId) await this.stop('live_session_changed');
    if (this.session && !this.session.isClosed()) return this.session;
    if (this.sessionPromise) return this.sessionPromise;
    this.sessionId = sessionId;
    const reporter = this.dependencies.createReporter(this.dependencies.createTraceId(sessionId));
    this.reporter = reporter;
    const state = this.dependencies.store.getState();
    this.sessionPromise = this.dependencies.createPcmSession(
      reporter.traceId,
      state.identity.voiceId,
      reporter,
      {
        sessionScoped: true,
        affinityKey: sessionId,
        onWorkletEvent: (event) => this.handleWorkletEvent(event),
      },
    ).then((session) => {
      this.session = session;
      this.sessionPromise = null;
      return session;
    }).catch((error) => {
      this.sessionPromise = null;
      this.sessionId = null;
      throw error;
    });
    return this.sessionPromise;
  }

  private outputId(sessionId: string, observationId: string): string {
    const safeSession = sessionId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-48);
    const safeObservation = observationId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-72);
    return `live-${safeSession}-${safeObservation}`.slice(0, 160);
  }
}

function isTerminal(item: LiveOutputItem): boolean {
  return item.status === 'completed' || item.status === 'cancelled' || item.status === 'failed';
}

export const liveOutputCoordinator = new LiveOutputCoordinator({
  store: liveConversationStore,
  fetcher: liveStreamFetch,
  createPcmSession: createLiveVoicePcmSession,
  createReporter: createLiveCallDiagnosticsReporter,
  createTraceId: createLiveCallTraceId,
});

let initialized = false;

export function initializeLiveOutputCoordinator(): () => void {
  if (initialized || typeof window === 'undefined') return () => undefined;
  initialized = true;
  const handleObservation = (event: Event) => {
    const detail = (event as CustomEvent<LiveObservationCandidateDetail>).detail;
    if (detail?.observation) void liveOutputCoordinator.handleObservationCandidate(detail);
  };
  const handleSuperseded = (event: Event) => {
    const detail = (event as CustomEvent<LiveObservationSupersededDetail>).detail;
    if (detail?.observationIds?.length) {
      void liveOutputCoordinator.cancelObservationIds(detail.observationIds, detail.reason || 'observation_superseded');
    }
  };
  const handleInterrupt = (event: Event) => {
    const detail = (event as CustomEvent<{ intent?: string }>).detail;
    void liveOutputCoordinator.interrupt(detail?.intent || 'live_interrupt');
  };
  const handleUnload = () => {
    void liveOutputCoordinator.stop('page_unload');
  };
  let observedSessionId = liveConversationStore.getState().sessionId;
  const unsubscribe = liveConversationStore.subscribe(() => {
    const nextSessionId = liveConversationStore.getState().sessionId;
    if (nextSessionId === observedSessionId) return;
    observedSessionId = nextSessionId;
    void liveOutputCoordinator.stop('live_session_changed');
  });
  window.addEventListener(LIVE_OBSERVATION_CANDIDATE_EVENT, handleObservation);
  window.addEventListener(LIVE_OBSERVATION_SUPERSEDED_EVENT, handleSuperseded);
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterrupt);
  window.addEventListener('beforeunload', handleUnload);
  return () => {
    window.removeEventListener(LIVE_OBSERVATION_CANDIDATE_EVENT, handleObservation);
    window.removeEventListener(LIVE_OBSERVATION_SUPERSEDED_EVENT, handleSuperseded);
    window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterrupt);
    window.removeEventListener('beforeunload', handleUnload);
    unsubscribe();
    void liveOutputCoordinator.stop('runtime_disposed');
    initialized = false;
  };
}
