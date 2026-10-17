import type { DesktopCompanionRolloutStage } from '../../settings';
import {
  DesktopCompanionEvaluationAccumulator,
  type DesktopCompanionEvaluationPayload,
} from './desktop-companion-evaluation';
import {
  hashDesktopCompanionModelId,
  loadDesktopCompanionBuildIdentity,
} from './desktop-companion-build-identity';
import { type DesktopCompanionEvaluationEvent } from './desktop-companion-watch-controller';
import { DESKTOP_COMPANION_DELIVERY_EVENT, DESKTOP_COMPANION_EVALUATION_EVENT } from '../../../events/bus';
import { startTicker } from '../../../shared/timers';
import { api } from '../api/gateway';

let desktopCompanionShadowEvaluationInstalled = false;

const FLUSH_INTERVAL_MS = 60_000;

type EvaluationIdentity = {
  sessionId: string | null;
  characterId: string;
  modelId: string | null;
  remoteProvider: boolean;
  rolloutStage: DesktopCompanionRolloutStage;
  exactCommitSha: string;
  appVersion: string;
  visionModelHash: string | null;
};

export type DesktopCompanionDeliveryEvidence = {
  sessionId: string;
  status: string;
  presentation: 'text' | 'speech';
  reason: string | null;
};

let accumulator: DesktopCompanionEvaluationAccumulator | null = null;
let identity: EvaluationIdentity | null = null;
let recordedEvents = 0;
let startedAt = new Date();
let stopFlushTicker: (() => void) | null = null;
let eventQueue: Promise<void> = Promise.resolve();

export function initializeDesktopCompanionShadowEvaluationController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (desktopCompanionShadowEvaluationInstalled) return () => undefined;
  desktopCompanionShadowEvaluationInstalled = true;
  const handleEvent = (event: Event) => {
    const detail = normalizeEvaluationEvent((event as CustomEvent<unknown>).detail);
    if (!detail) return;
    eventQueue = eventQueue.then(() => handleEvaluationEvent(detail)).catch(() => undefined);
  };
  const handleDelivery = (event: Event) => {
    const detail = normalizeDeliveryEvaluationEvent((event as CustomEvent<unknown>).detail);
    if (!detail) return;
    eventQueue = eventQueue.then(() => handleDeliveryEvidence(detail)).catch(() => undefined);
  };
  const handleUnload = () => {
    const payload = finalizeCurrent();
    if (payload) submitEvaluationKeepalive(payload);
  };
  window.addEventListener(DESKTOP_COMPANION_EVALUATION_EVENT, handleEvent);
  window.addEventListener(DESKTOP_COMPANION_DELIVERY_EVENT, handleDelivery);
  window.addEventListener('beforeunload', handleUnload);
  stopFlushTicker = startTicker(() => {
    eventQueue = eventQueue.then(() => flushAndRestart('interval')).catch(() => undefined);
  }, FLUSH_INTERVAL_MS);
  return () => {
    window.removeEventListener(DESKTOP_COMPANION_EVALUATION_EVENT, handleEvent);
    window.removeEventListener(DESKTOP_COMPANION_DELIVERY_EVENT, handleDelivery);
    window.removeEventListener('beforeunload', handleUnload);
    stopFlushTicker?.();
    stopFlushTicker = null;
    const payload = finalizeCurrent();
    if (payload) void submitEvaluation(payload);
    desktopCompanionShadowEvaluationInstalled = false;
  };
}

export function normalizeEvaluationEvent(value: unknown): DesktopCompanionEvaluationEvent | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  const kind = input.kind;
  if (!['watch_started', 'capture', 'vision_result', 'watch_stopped'].includes(String(kind))) return null;
  const sessionId = typeof input.sessionId === 'string' && input.sessionId.trim() ? input.sessionId.trim() : null;
  const rolloutStage = ['shadow', 'text', 'speech'].includes(String(input.rolloutStage))
    ? input.rolloutStage as DesktopCompanionRolloutStage
    : undefined;
  return {
    kind: kind as DesktopCompanionEvaluationEvent['kind'],
    sessionId,
    characterId: typeof input.characterId === 'string' ? input.characterId.trim() || null : null,
    modelId: typeof input.modelId === 'string' ? input.modelId.trim() || null : null,
    remoteProvider: input.remoteProvider === true,
    rolloutStage,
    scenario: typeof input.scenario === 'string' ? input.scenario.trim() || null : null,
    meaningful: input.meaningful === true,
    latencyMs: finiteNumber(input.latencyMs),
    callsThisMinute: finiteNumber(input.callsThisMinute),
    providerError: input.providerError === true,
    stale: input.stale === true,
    observed: input.observed === true,
    reason: typeof input.reason === 'string' ? input.reason.slice(0, 120) : undefined,
  };
}

export function normalizeDeliveryEvaluationEvent(value: unknown): DesktopCompanionDeliveryEvidence | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  const sessionId = typeof input.sessionId === 'string' ? input.sessionId.trim() : '';
  const status = typeof input.status === 'string' ? input.status.trim().slice(0, 80) : '';
  if (!sessionId || !status) return null;
  return {
    sessionId,
    status,
    presentation: input.presentation === 'speech' ? 'speech' : 'text',
    reason: typeof input.reason === 'string' ? input.reason.trim().slice(0, 120) || null : null,
  };
}

export function scenarioForDeliveryEvidence(event: DesktopCompanionDeliveryEvidence): string | null {
  if (event.status === 'interrupted') return 'interruption';
  if (
    event.status === 'discarded'
    && (event.reason === 'user_speech' || event.reason === 'interrupted')
  ) return 'interruption';
  if (event.presentation !== 'speech') return null;
  if (event.status === 'completed') return 'speech-completed';
  if (event.status === 'suppress' && event.reason === 'candidate_stale') return 'speech-stale';
  return null;
}

async function handleEvaluationEvent(event: DesktopCompanionEvaluationEvent): Promise<void> {
  if (event.kind === 'watch_started') {
    if (identity?.sessionId !== event.sessionId || accumulator) await flushAndRestart('rebind', false);
    await startAccumulator(event);
    return;
  }
  if (event.kind === 'watch_stopped') {
    await flushAndRestart(event.reason ?? 'watch_stopped', false);
    return;
  }
  if (!accumulator || !identity || identity.sessionId !== event.sessionId) return;
  recordedEvents += 1;
  if (event.scenario) accumulator.addScenario(event.scenario);
  if (event.kind === 'capture') {
    accumulator.recordCapture();
    if (event.meaningful) accumulator.recordMeaningfulChange();
    return;
  }
  accumulator.recordVisionRequest({
    latencyMs: event.latencyMs,
    callsThisMinute: event.callsThisMinute,
    providerError: event.providerError,
    stale: event.stale,
  });
  if (event.observed) accumulator.recordObservation();
}

async function handleDeliveryEvidence(event: DesktopCompanionDeliveryEvidence): Promise<void> {
  if (!accumulator || !identity || identity.sessionId !== event.sessionId) return;
  recordedEvents += 1;
  const scenario = scenarioForDeliveryEvidence(event);
  if (scenario) accumulator.addScenario(scenario);
  if (event.status === 'generated') {
    accumulator.recordCommentary({ skipped: false });
    return;
  }
  if (event.status === 'suppress') {
    accumulator.recordCommentary({ skipped: true });
    return;
  }
  if (event.status === 'completed') {
    accumulator.recordDelivery({ collision: false, interrupted: false });
    return;
  }
  if (event.status === 'interrupted') {
    accumulator.recordDelivery({ collision: false, interrupted: true });
    return;
  }
  if (event.status === 'discarded') {
    accumulator.recordCommentary({ skipped: true });
    const collision = event.reason === 'user_speech' || event.reason === 'interrupted';
    if (collision) accumulator.recordDelivery({ collision: true, interrupted: true });
    return;
  }
  if (event.status === 'error') accumulator.recordCommentary({ skipped: true });
}

async function startAccumulator(event: DesktopCompanionEvaluationEvent): Promise<void> {
  if (!event.sessionId) return;
  const build = await loadDesktopCompanionBuildIdentity();
  const modelHash = event.modelId ? await hashDesktopCompanionModelId(event.modelId) : null;
  identity = {
    sessionId: event.sessionId,
    characterId: event.characterId || 'system-assistant',
    modelId: event.modelId ?? null,
    remoteProvider: event.remoteProvider === true,
    rolloutStage: event.rolloutStage ?? 'shadow',
    exactCommitSha: build.exact_commit_sha,
    appVersion: build.app_version,
    visionModelHash: modelHash,
  };
  startedAt = new Date();
  recordedEvents = 0;
  accumulator = createAccumulator(identity, startedAt);
}

async function flushAndRestart(_reason: string, restart = true): Promise<void> {
  const previousIdentity = identity;
  const payload = finalizeCurrent();
  if (payload) await submitEvaluation(payload);
  if (restart && previousIdentity) {
    identity = previousIdentity;
    startedAt = new Date();
    recordedEvents = 0;
    accumulator = createAccumulator(previousIdentity, startedAt);
  }
}

function finalizeCurrent(): DesktopCompanionEvaluationPayload | null {
  if (!accumulator || !identity || recordedEvents === 0) {
    accumulator = null;
    identity = null;
    recordedEvents = 0;
    return null;
  }
  const payload = accumulator.finalize(new Date());
  accumulator = null;
  identity = null;
  recordedEvents = 0;
  return payload;
}

function createAccumulator(value: EvaluationIdentity, start: Date): DesktopCompanionEvaluationAccumulator {
  return new DesktopCompanionEvaluationAccumulator({
    runId: `desktop-${value.rolloutStage}:${crypto.randomUUID()}`,
    sessionId: value.sessionId,
    exactCommitSha: value.exactCommitSha,
    appVersion: value.appVersion,
    characterId: value.characterId,
    profileVersion: null,
    observationSchemaVersion: 1,
    attentionPolicyVersion: 1,
    rolloutStage: value.rolloutStage,
    visionProvider: value.remoteProvider ? 'openai-compatible-remote' : 'openai-compatible-local',
    visionModelHash: value.visionModelHash,
    remoteProvider: value.remoteProvider,
    startedAt: start,
  });
}

async function submitEvaluation(payload: DesktopCompanionEvaluationPayload): Promise<void> {
  try {
    await api.POST('/api/desktop-companion/evaluations', { body: payload });
  } catch {
    // Evidence submission must never interrupt Desktop Companion observation.
  }
}

function submitEvaluationKeepalive(payload: DesktopCompanionEvaluationPayload): void {
  try {
    void api.POST('/api/desktop-companion/evaluations', { body: payload, keepalive: true });
  } catch {
    // Browser teardown remains best effort.
  }
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
