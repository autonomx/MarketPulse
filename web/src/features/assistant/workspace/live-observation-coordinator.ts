import type { LiveTaskContract } from './live-task-contract';

export type LiveObservationPriority = 'critical' | 'normal' | 'deferred';
export type LiveObservationStatus =
  | 'candidate'
  | 'generating'
  | 'ready'
  | 'superseded'
  | 'queued'
  | 'speaking'
  | 'completed'
  | 'failed';

export type LiveObservationAnchor = {
  anchorId: string;
  segmentIds: string[];
  startOffset?: number;
  endOffset?: number;
  sourceFingerprint: string;
  state: 'open' | 'self_corrected' | 'superseded';
};

export type LiveObservation = {
  observationId: string;
  basedOnSequence: number;
  contextVersion: number;
  taskContractId: string;
  taskContractVersion: number;
  anchors: LiveObservationAnchor[];
  priority: LiveObservationPriority;
  status: LiveObservationStatus;
  createdAtMs: number;
  estimatedSpeechMs: number;
  equivalenceKey: string;
};

export type ObservationAdmissionStage = 'candidate' | 'generation' | 'playback';
export type ObservationAdmissionContext = {
  nowMs: number;
  contextVersion: number;
  taskContract: LiveTaskContract;
  deliveredEquivalenceKeys: ReadonlySet<string>;
  queuedSpeechMs: number;
};

export type ObservationAdmissionDecision = {
  admitted: boolean;
  reason: string;
};

export type LiveObservationQueueOptions = {
  maxItems?: number;
  maxQueuedSpeechMs?: number;
  maxAgeMs?: number;
};

const DEFAULT_MAX_ITEMS = 12;
const DEFAULT_MAX_QUEUED_SPEECH_MS = 15_000;
const DEFAULT_MAX_AGE_MS = 20_000;

export class LiveObservationQueue {
  private readonly items: LiveObservation[] = [];
  private readonly maxItems: number;
  private readonly maxQueuedSpeechMs: number;
  private readonly maxAgeMs: number;

  constructor(options: LiveObservationQueueOptions = {}) {
    this.maxItems = Math.max(1, options.maxItems ?? DEFAULT_MAX_ITEMS);
    this.maxQueuedSpeechMs = Math.max(250, options.maxQueuedSpeechMs ?? DEFAULT_MAX_QUEUED_SPEECH_MS);
    this.maxAgeMs = Math.max(250, options.maxAgeMs ?? DEFAULT_MAX_AGE_MS);
  }

  get snapshot(): readonly LiveObservation[] {
    return [...this.items];
  }

  get queuedSpeechMs(): number {
    return this.items
      .filter((item) => item.status !== 'completed' && item.status !== 'superseded' && item.status !== 'failed')
      .reduce((total, item) => total + item.estimatedSpeechMs, 0);
  }

  admitCandidate(observation: LiveObservation, context: ObservationAdmissionContext): ObservationAdmissionDecision {
    const decision = evaluateObservationAdmission('candidate', observation, context, this.maxAgeMs, this.maxQueuedSpeechMs);
    if (!decision.admitted) return decision;
    const duplicate = this.items.find((item) => item.equivalenceKey === observation.equivalenceKey && !isTerminal(item.status));
    if (duplicate) return { admitted: false, reason: 'equivalent_observation_pending' };
    if (this.items.length >= this.maxItems) {
      const removable = this.items.findIndex((item) => item.priority === 'deferred' && item.status === 'candidate');
      if (removable < 0) return { admitted: false, reason: 'observation_item_limit' };
      this.items[removable] = { ...this.items[removable], status: 'superseded' };
    }
    this.items.push(observation);
    return { admitted: true, reason: 'candidate_admitted' };
  }

  transition(
    observationId: string,
    status: LiveObservationStatus,
    context: ObservationAdmissionContext,
  ): ObservationAdmissionDecision {
    const index = this.items.findIndex((item) => item.observationId === observationId);
    if (index < 0) return { admitted: false, reason: 'observation_missing' };
    const item = this.items[index];
    const stage: ObservationAdmissionStage = status === 'generating' ? 'generation' : status === 'queued' || status === 'speaking' ? 'playback' : 'candidate';
    const decision = evaluateObservationAdmission(stage, item, context, this.maxAgeMs, this.maxQueuedSpeechMs);
    if (!decision.admitted) {
      this.items[index] = { ...item, status: 'superseded' };
      return decision;
    }
    this.items[index] = { ...item, status };
    return { admitted: true, reason: `${status}_admitted` };
  }

  markAnchorState(anchorId: string, state: LiveObservationAnchor['state']): string[] {
    const superseded: string[] = [];
    for (let index = 0; index < this.items.length; index += 1) {
      const item = this.items[index];
      if (!item.anchors.some((anchor) => anchor.anchorId === anchorId)) continue;
      const anchors = item.anchors.map((anchor) => anchor.anchorId === anchorId ? { ...anchor, state } : anchor);
      const status = state === 'open' ? item.status : 'superseded';
      this.items[index] = { ...item, anchors, status };
      if (status === 'superseded') superseded.push(item.observationId);
    }
    return superseded;
  }

  invalidateTaskContract(contract: LiveTaskContract): string[] {
    const superseded: string[] = [];
    for (let index = 0; index < this.items.length; index += 1) {
      const item = this.items[index];
      if (item.taskContractId === contract.taskContractId && item.taskContractVersion === contract.version) continue;
      if (isTerminal(item.status)) continue;
      this.items[index] = { ...item, status: 'superseded' };
      superseded.push(item.observationId);
    }
    return superseded;
  }

  prune(nowMs: number): void {
    for (let index = this.items.length - 1; index >= 0; index -= 1) {
      const item = this.items[index];
      if (isTerminal(item.status) || nowMs - item.createdAtMs > this.maxAgeMs * 2) this.items.splice(index, 1);
    }
  }
}

export function evaluateObservationAdmission(
  stage: ObservationAdmissionStage,
  observation: LiveObservation,
  context: ObservationAdmissionContext,
  maxAgeMs = DEFAULT_MAX_AGE_MS,
  maxQueuedSpeechMs = DEFAULT_MAX_QUEUED_SPEECH_MS,
): ObservationAdmissionDecision {
  if (observation.taskContractId !== context.taskContract.taskContractId
    || observation.taskContractVersion !== context.taskContract.version) {
    return { admitted: false, reason: 'task_contract_changed' };
  }
  if (observation.contextVersion > context.contextVersion) {
    return { admitted: false, reason: 'context_version_ahead' };
  }
  if (observation.anchors.some((anchor) => anchor.state !== 'open')) {
    return { admitted: false, reason: 'anchor_invalid' };
  }
  if (context.deliveredEquivalenceKeys.has(observation.equivalenceKey)) {
    return { admitted: false, reason: 'equivalent_feedback_delivered' };
  }
  if (context.nowMs - observation.createdAtMs > maxAgeMs) {
    return { admitted: false, reason: 'observation_stale' };
  }
  if (stage !== 'candidate' && observation.contextVersion < context.contextVersion - 4) {
    return { admitted: false, reason: 'context_advanced' };
  }
  if (context.queuedSpeechMs + observation.estimatedSpeechMs > maxQueuedSpeechMs && observation.priority !== 'critical') {
    return { admitted: false, reason: 'speech_backpressure' };
  }
  return { admitted: true, reason: `${stage}_valid` };
}

export function createObservationAnchor(segmentId: string, text: string): LiveObservationAnchor {
  return {
    anchorId: `${segmentId}:${fingerprint(text)}`,
    segmentIds: [segmentId],
    sourceFingerprint: fingerprint(text),
    state: 'open',
  };
}

export function fingerprint(value: string): string {
  let hash = 2166136261;
  for (const character of value.trim().toLocaleLowerCase()) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `session-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

function isTerminal(status: LiveObservationStatus): boolean {
  return status === 'completed' || status === 'superseded' || status === 'failed';
}
