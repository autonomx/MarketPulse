import type { DesktopCompanionRolloutStage } from '../../settings';
import {
  classifyDesktopActivity,
  DesktopBehaviorTracker,
  type DesktopActivityReading,
  type DesktopBehaviorReading,
} from './desktop-companion-activity';
import { desktopCompanionRolloutEvidenceIdentity } from './desktop-companion-build-identity';
import { desktopCompanionControlStore } from './desktop-companion-control-store';
import {
  fetchDesktopCompanionRolloutStatus,
  type DesktopCompanionRolloutStatus,
} from './desktop-companion-rollout';
import { DesktopCompanionRuntime, type DesktopCompanionSnapshot } from './desktop-companion-runtime';
import { currentDesktopCompanionCapture } from './assistant-context-controller';
import { liveConversationStore } from './live-conversation-store';
import type { components } from '../api/generated';
import { unwrap } from '../../../api/http';
import { DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT, DESKTOP_COMPANION_EVALUATION_EVENT, DESKTOP_COMPANION_STATUS_EVENT, emitOmnixEvent } from '../../../events/bus';
import { startTicker } from '../../../shared/timers';
import { api } from '../api/gateway';

let desktopCompanionWatchInstalled = false;


export type ShadowWatchSettings = {
  enabled: boolean;
  requestedStage: DesktopCompanionRolloutStage;
  visionModelId: string;
  remoteVisionAllowed: boolean;
  backgroundCallsPerMinute: number;
  minimumObservationIntervalMs: number;
  observationTimeoutMs: number;
  observationTtlMs: number;
  commentaryCooldownMs: number;
  minimumChangeConfidence: number;
};

export type DesktopCompanionEvaluationEvent = {
  kind: 'watch_started' | 'capture' | 'vision_result' | 'watch_stopped';
  sessionId: string | null;
  characterId?: string | null;
  modelId?: string | null;
  remoteProvider?: boolean;
  rolloutStage?: DesktopCompanionRolloutStage;
  scenario?: string | null;
  meaningful?: boolean;
  latencyMs?: number;
  callsThisMinute?: number;
  providerError?: boolean;
  stale?: boolean;
  observed?: boolean;
  reason?: string;
};

type ObserveResponse = components['schemas']['DesktopCompanionObserveResponse'];

type PreflightResponse = components['schemas']['DesktopCompanionPreflightResult'];

const runtime = new DesktopCompanionRuntime();
const behaviorTracker = new DesktopBehaviorTracker();
let previousSample: Uint8Array | null = null;
let previousSampleAtMs = -1;
let requestController: AbortController | null = null;
let stopTicker: (() => void) | null = null;
let settings: ShadowWatchSettings = disabledSettings();
let settingsLoadedAtMs = 0;
let lastObservationStartedMs: number | null = null;
let lastBindingKey = '';
let resetBinding: { sessionId: string; captureGeneration: string } | null = null;
let preflightKey = '';
let preflight: PreflightResponse | null = null;
let rollout: DesktopCompanionRolloutStatus = disabledRollout();
let rolloutCheckedAtMs = 0;
let pauseInterruptionRecorded = false;

export function createDesktopCompanionTickScheduler(run: () => Promise<void>): () => Promise<void> {
  let inFlight: Promise<void> | null = null;
  let pending = false;
  return () => {
    if (inFlight) {
      pending = true;
      return inFlight;
    }
    inFlight = Promise.resolve()
      .then(async () => {
        await run();
        while (pending) {
          pending = false;
          await run();
        }
      })
      .finally(() => {
        inFlight = null;
      });
    return inFlight;
  };
}

export function shouldResumeDesktopCompanion(
  snapshot: Pick<DesktopCompanionSnapshot, 'phase' | 'watchEnabled'>,
): boolean {
  return !snapshot.watchEnabled || snapshot.phase === 'paused';
}

export function shouldRecordPausedAnalysisInterruption(
  snapshot: Pick<DesktopCompanionSnapshot, 'phase'>,
  requestInFlight: boolean,
  alreadyRecorded: boolean,
): boolean {
  return snapshot.phase === 'analyzing' && !requestInFlight && !alreadyRecorded;
}

export function abortDesktopCompanionObservationForPause(
  paused: boolean,
  controller: AbortController | null,
): boolean {
  if (!paused || !controller || controller.signal.aborted) return false;
  controller.abort('paused_by_user');
  return true;
}

const tick = createDesktopCompanionTickScheduler(tickOnce);

export function initializeDesktopCompanionWatchController(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  if (desktopCompanionWatchInstalled) return () => undefined;
  desktopCompanionWatchInstalled = true;
  stopTicker = startTicker(() => void tick(), 500);
  const handleVisibility = () => runtime.handleVisibility(document.visibilityState === 'visible');
  const handleShareChange = () => void tick();
  const unsubscribeControls = desktopCompanionControlStore.subscribe(() => {
    abortDesktopCompanionObservationForPause(
      desktopCompanionControlStore.getState().paused,
      requestController,
    );
    void tick();
  });
  document.addEventListener('visibilitychange', handleVisibility);
  window.addEventListener('omnix:desktop-share-changed', handleShareChange);
  void tick();
  return () => {
    stopTicker?.();
    stopTicker = null;
    requestController?.abort('controller_disposed');
    requestController = null;
    unsubscribeControls();
    document.removeEventListener('visibilitychange', handleVisibility);
    window.removeEventListener('omnix:desktop-share-changed', handleShareChange);
    void stopAndReset('controller_disposed');
    desktopCompanionWatchInstalled = false;
  };
}

export function parseShadowWatchSettings(payload: unknown): ShadowWatchSettings {
  const root = record(payload);
  const settingsRoot = record(root.settings);
  const profile = record(settingsRoot.settings_control_center);
  const assistant = record(profile.assistant);
  const configuredStage = stringValue(assistant.desktopCompanionRolloutStage, 'disabled');
  const requestedStage: DesktopCompanionRolloutStage = ['shadow', 'text', 'speech'].includes(configuredStage)
    ? configuredStage as DesktopCompanionRolloutStage
    : 'disabled';
  return {
    enabled: assistant.desktopCompanionEnabled === true && requestedStage !== 'disabled',
    requestedStage,
    visionModelId: stringValue(assistant.desktopCompanionVisionModelId, ''),
    remoteVisionAllowed: assistant.desktopCompanionRemoteVisionAllowed === true,
    backgroundCallsPerMinute: boundedInt(assistant.desktopCompanionBackgroundCallsPerMinute, 6, 1, 30),
    minimumObservationIntervalMs: boundedInt(assistant.desktopCompanionMinimumObservationIntervalMs, 8_000, 2_000, 120_000),
    observationTimeoutMs: boundedInt(assistant.desktopCompanionObservationTimeoutMs, 10_000, 1_000, 60_000),
    observationTtlMs: boundedInt(assistant.desktopCompanionObservationTtlMs, 12_000, 2_000, 120_000),
    commentaryCooldownMs: boundedInt(assistant.desktopCompanionCommentaryCooldownMs, 25_000, 5_000, 300_000),
    minimumChangeConfidence: boundedNumber(assistant.desktopCompanionMinimumChangeConfidence, 0.55, 0, 1),
  };
}

export function activityPayload(signal: DesktopActivityReading, sourceWidth: number, sourceHeight: number) {
  return {
    activity: signal.activity,
    hypothesis: signal.hypothesis,
    confidence: signal.confidence,
    changed_ratio: signal.changedRatio,
    mean_difference: signal.meanDifference,
    horizontal_shift: signal.horizontalShift,
    vertical_shift: signal.verticalShift,
    focus: signal.focus,
    source_width: Math.max(1, Math.round(sourceWidth)),
    source_height: Math.max(1, Math.round(sourceHeight)),
    details: {},
  };
}

export function scenarioForActivity(
  activity: DesktopActivityReading,
  behavior: DesktopBehaviorReading,
): string | null {
  if (behavior.likelyTyping || activity.hypothesis === 'likely_typing') return 'typing';
  if (behavior.rapidBrowsing) return 'rapid-browsing';
  if (activity.activity === 'full_scene_change' || activity.hypothesis === 'likely_app_switch') return 'scene-change';
  if (activity.activity === 'static' || activity.activity === 'micro_change') return 'static-screen';
  return null;
}

export function scenarioForObservationOutcome(
  activityScenario: string | null,
  evaluationScenario: ObserveResponse['evaluation_scenario'],
  interrupted: boolean,
): string | null {
  if (interrupted) return 'interruption';
  if (evaluationScenario === 'screen-prompt-injection') return evaluationScenario;
  return activityScenario;
}

async function tickOnce(): Promise<void> {
  const nowMs = Date.now();
  if (nowMs - settingsLoadedAtMs >= 10_000) await refreshSettings(nowMs);
  const controls = desktopCompanionControlStore.getState();
  const capture = currentDesktopCompanionCapture();
  if (!settings.enabled || !capture?.sessionId || !controls.requested) {
    if (runtime.getSnapshot().binding) {
      await stopAndReset(!settings.enabled ? 'desktop_companion_disabled' : !capture?.sessionId ? 'session_unbound' : 'stopped_by_user');
    }
    return;
  }

  if (controls.paused) {
    const snapshot = runtime.getSnapshot();
    if (shouldRecordPausedAnalysisInterruption(
      snapshot,
      requestController !== null,
      pauseInterruptionRecorded,
    )) {
      dispatchEvaluation({
        kind: 'vision_result',
        sessionId: capture.sessionId,
        scenario: 'interruption',
        providerError: false,
        stale: false,
        observed: false,
        reason: 'paused_during_analysis',
      });
    }
    if (snapshot.phase === 'analyzing') pauseInterruptionRecorded = true;
    requestController?.abort('paused_by_user');
    requestController = null;
    runtime.pause('paused_by_user');
    publishStatus('paused', 'paused_by_user');
    return;
  }
  pauseInterruptionRecorded = false;

  const bindingKey = `${capture.sessionId}:${capture.sourceFingerprint}`;
  if (bindingKey !== lastBindingKey) {
    await stopAndReset('capture_rebound');
    const binding = runtime.beginSharing({
      sessionId: capture.sessionId,
      characterId: capture.characterId,
      sourceFingerprint: capture.sourceFingerprint,
    });
    resetBinding = { sessionId: binding.sessionId, captureGeneration: binding.captureGeneration };
    behaviorTracker.reset();
    previousSample = null;
    previousSampleAtMs = -1;
    lastObservationStartedMs = null;
    lastBindingKey = bindingKey;
  }

  const expectedPreflightKey = `${bindingKey}:${settings.visionModelId}:${settings.remoteVisionAllowed}`;
  if (preflightKey !== expectedPreflightKey) {
    publishStatus('sharing', 'preflight_running');
    preflight = await runPreflight();
    preflightKey = expectedPreflightKey;
    runtime.setPreflight({
      ready: preflight.ready,
      modelId: preflight.model_id,
      endpoint: preflight.endpoint,
      remote: preflight.remote,
      reason: preflight.reason,
    });
    if (!preflight.ready) {
      runtime.markPhase('error', preflight.reason);
      publishStatus('error', preflight.reason);
      desktopCompanionControlStore.dispatch('stop');
      return;
    }
    await refreshRollout(true);
    runtime.enableWatch({
      shadowMode: rollout.effective_stage === 'shadow',
      speechMuted: controls.muted,
    });
    dispatchEvaluation({
      kind: 'watch_started',
      sessionId: capture.sessionId,
      characterId: capture.characterId,
      modelId: preflight.model_id,
      remoteProvider: preflight.remote,
      rolloutStage: rollout.effective_stage,
      reason: rollout.reason,
    });
    publishStatus('watching_idle', rollout.reason);
  } else if (shouldResumeDesktopCompanion(runtime.getSnapshot())) {
    runtime.resume();
  }
  if (nowMs - rolloutCheckedAtMs >= 30_000) await refreshRollout(false);
  runtime.setSpeechMuted(controls.muted);
  if (requestController || !runtime.getSnapshot().watchEnabled) return;

  const sample = capture.capture.latestActivitySample();
  if (!sample || sample.capturedAtMs === previousSampleAtMs) return;
  const activity = classifyDesktopActivity(previousSample, sample.sample, performance.now());
  previousSample = sample.sample;
  previousSampleAtMs = sample.capturedAtMs;
  const behavior = behaviorTracker.record(activity);
  const scenario = scenarioForActivity(activity, behavior);
  const meaningful = !['unknown', 'static', 'micro_change'].includes(activity.activity)
    && activity.confidence >= settings.minimumChangeConfidence;
  dispatchEvaluation({
    kind: 'capture',
    sessionId: capture.sessionId,
    scenario,
    meaningful,
  });
  if (!meaningful) return;
  if (behavior.likelyTyping || behavior.rapidBrowsing) return;
  if (lastObservationStartedMs !== null && nowMs - lastObservationStartedMs < settings.minimumObservationIntervalMs) return;

  const sequence = runtime.nextSequence();
  const controller = new AbortController();
  requestController = controller;
  lastObservationStartedMs = nowMs;
  runtime.markPhase('analyzing');
  publishStatus('analyzing', 'meaningful_change_available');
  const timeoutId = window.setTimeout(() => controller.abort('observation_timeout'), settings.observationTimeoutMs);
  const observationStarted = performance.now();
  try {
    const payload = await capture.capture.buildPayload();
    const conversation = liveConversationStore.getState().conversation;
    const effectiveStage = rollout.effective_stage;
    // openapi-fetch reads tuple fields (possible_events[].between) as arrays.
    const result = await unwrap(api.POST('/api/desktop-companion/observe', {
      signal: controller.signal,
      body: {
        session_id: sequence.binding.sessionId,
        character_id: sequence.binding.characterId,
        capture_generation: sequence.binding.captureGeneration,
        source_fingerprint: sequence.binding.sourceFingerprint,
        client_sequence: sequence.clientSequence,
        captured_at: new Date().toISOString(),
        current_image_data_url: payload.currentImageDataUrl,
        history_image_data_url: payload.historyImageDataUrl,
        combined_image_data_url: payload.combinedImageDataUrl,
        history_timestamps: payload.historyTimestamps,
        capture_mode: payload.captureMode,
        vision_model_id: settings.visionModelId || null,
        activity: activityPayload(activity, sample.width, sample.height),
        behavior: {
          current_pattern: behavior.currentPattern,
          settled_seconds: behavior.settledSeconds,
          browsing_pace: behavior.browsingPace,
          rapid_browsing: behavior.rapidBrowsing,
          likely_typing: behavior.likelyTyping,
          likely_media: behavior.likelyMedia,
          transition: behavior.transition,
          sample_count: behavior.sampleCount,
        },
        policy: {
          enabled: effectiveStage !== 'disabled',
          shadow_mode: effectiveStage === 'shadow',
          speech_enabled: effectiveStage === 'speech' && !controls.muted,
          visible_comments: effectiveStage === 'text' || effectiveStage === 'speech',
          background_calls_per_minute: settings.backgroundCallsPerMinute,
          minimum_observation_interval_ms: settings.minimumObservationIntervalMs,
          observation_timeout_ms: settings.observationTimeoutMs,
          observation_ttl_ms: settings.observationTtlMs,
          commentary_cooldown_ms: settings.commentaryCooldownMs,
          minimum_change_confidence: settings.minimumChangeConfidence,
        },
        user_floor_active: conversation.floorOwner === 'user' || conversation.userTurn === 'speaking',
        assistant_busy: conversation.floorOwner === 'assistant' || conversation.assistantTurn !== 'idle',
        request_in_flight: false,
      },
    })) as ObserveResponse;
    const callsThisMinute = numberValue(result.coordinator?.background_calls_in_window);
    dispatchEvaluation({
      kind: 'vision_result',
      sessionId: capture.sessionId,
      scenario: scenarioForObservationOutcome(scenario, result.evaluation_scenario, false),
      latencyMs: Math.max(0, performance.now() - observationStarted),
      callsThisMinute,
      providerError: result.status === 'error',
      stale: result.reason.includes('stale') || result.reason.includes('expired'),
      observed: result.status === 'completed' && Boolean(result.observation?.observation_id),
      reason: result.reason,
    });
    if (!runtime.acceptsResult({
      captureGeneration: sequence.binding.captureGeneration,
      clientSequence: sequence.clientSequence,
    })) return;
    if (result.status === 'completed') {
      runtime.markPhase('observation_ready');
      publishStatus('observation_ready', result.reason, result);
      const observationId = result.observation?.observation_id;
      if (
        result.delivery_eligible
        && observationId
        && result.scene_summary
        && (effectiveStage === 'text' || effectiveStage === 'speech')
      ) {
        emitOmnixEvent(DESKTOP_COMPANION_DELIVERY_REQUEST_EVENT, {
            sessionId: capture.sessionId,
            observationId,
            groundingIds: [observationId],
            stateSummary: result.scene_summary,
            priority: 'normal',
            presentation: effectiveStage === 'speech' && !controls.muted ? 'speech' : 'text',
            expiresAtMs: Date.now() + settings.observationTtlMs,
          });
      }
    } else if (result.status === 'deferred') {
      runtime.markPhase('backing_off', result.reason);
      publishStatus('backing_off', result.reason, result);
    } else if (result.status === 'error') {
      runtime.markPhase('error', result.reason);
      publishStatus('error', result.reason, result);
    } else {
      runtime.markPhase('watching_idle');
      publishStatus('watching_idle', result.reason, result);
    }
  } catch (error) {
    const reason = controller.signal.aborted
      ? String(controller.signal.reason || 'observation_aborted')
      : error instanceof Error ? error.message : String(error);
    dispatchEvaluation({
      kind: 'vision_result',
      sessionId: capture.sessionId,
      scenario: scenarioForObservationOutcome(scenario, null, controller.signal.aborted),
      latencyMs: Math.max(0, performance.now() - observationStarted),
      providerError: !controller.signal.aborted,
      stale: controller.signal.reason === 'observation_timeout',
      observed: false,
      reason,
    });
    if (!controller.signal.aborted) {
      runtime.markPhase('backing_off', reason);
      publishStatus('backing_off', reason);
    }
  } finally {
    window.clearTimeout(timeoutId);
    if (requestController === controller) requestController = null;
    if (runtime.getSnapshot().watchEnabled && runtime.getSnapshot().phase !== 'error') {
      runtime.markPhase('watching_idle');
    }
  }
}

async function runPreflight(): Promise<PreflightResponse> {
  try {
    return await unwrap(api.POST('/api/desktop-companion/preflight', {
      body: {
        vision_model_id: settings.visionModelId || null,
        remote_vision_allowed: settings.remoteVisionAllowed,
      },
    }));
  } catch (error) {
    return {
      ready: false,
      model_id: settings.visionModelId || null,
      endpoint: null,
      remote: false,
      latency_ms: null,
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

async function refreshRollout(force: boolean): Promise<void> {
  if (!preflight?.ready) return;
  const nowMs = Date.now();
  if (!force && nowMs - rolloutCheckedAtMs < 30_000) return;
  rolloutCheckedAtMs = nowMs;
  try {
    const identity = await desktopCompanionRolloutEvidenceIdentity({
      modelId: preflight.model_id,
      remoteProvider: preflight.remote,
    });
    const next = await fetchDesktopCompanionRolloutStatus(settings.requestedStage, identity);
    const changed = next.effective_stage !== rollout.effective_stage;
    rollout = next;
    if (changed && runtime.getSnapshot().binding) {
      runtime.enableWatch({
        shadowMode: rollout.effective_stage === 'shadow',
        speechMuted: desktopCompanionControlStore.getState().muted,
      });
      publishStatus('watching_idle', rollout.reason);
    }
  } catch {
    rollout = {
      requested_stage: settings.requestedStage,
      effective_stage: settings.requestedStage === 'disabled' ? 'disabled' : 'shadow',
      enabled: settings.requestedStage !== 'disabled',
      reason: 'rollout_status_unavailable',
      release_gate_status: 'insufficient',
      evidence_evaluation_ids: [],
    };
  }
}

async function refreshSettings(nowMs: number): Promise<void> {
  settingsLoadedAtMs = nowMs;
  try {
    const next = parseShadowWatchSettings(await unwrap(api.GET('/api/settings')));
    if (
      next.visionModelId !== settings.visionModelId
      || next.remoteVisionAllowed !== settings.remoteVisionAllowed
      || next.requestedStage !== settings.requestedStage
    ) {
      preflightKey = '';
      rolloutCheckedAtMs = 0;
    }
    settings = next;
  } catch {
    // Existing chat and manual Desktop Ask remain available when settings cannot load.
  }
}

async function stopAndReset(reason: string): Promise<void> {
  requestController?.abort(reason);
  requestController = null;
  const previous = resetBinding;
  runtime.stopAndForget();
  behaviorTracker.reset();
  previousSample = null;
  previousSampleAtMs = -1;
  lastObservationStartedMs = null;
  lastBindingKey = '';
  resetBinding = null;
  preflightKey = '';
  preflight = null;
  rollout = disabledRollout();
  rolloutCheckedAtMs = 0;
  pauseInterruptionRecorded = false;
  if (previous) {
    dispatchEvaluation({
      kind: 'watch_stopped',
      sessionId: previous.sessionId,
      reason,
    });
    try {
      await api.POST('/api/desktop-companion/reset', {
        body: {
          session_id: previous.sessionId,
          capture_generation: previous.captureGeneration,
        },
      });
    } catch {
      // Reset is best effort; generation IDs still reject stale browser results.
    }
  }
  publishStatus('off', reason);
}

function publishStatus(phase: string, reason: string, result?: ObserveResponse): void {
  emitOmnixEvent(DESKTOP_COMPANION_STATUS_EVENT, {
      phase,
      reason,
      requestedStage: rollout.requested_stage,
      effectiveStage: rollout.effective_stage,
      releaseGateStatus: rollout.release_gate_status,
      observationId: result?.observation?.observation_id ?? null,
      reaction: result?.attention?.reaction ?? null,
      rationale: result?.attention?.rationale ?? null,
      shouldGenerate: result?.attention?.should_generate ?? false,
      coordinator: result?.coordinator ?? {},
      preflight: preflight ? {
        ready: preflight.ready,
        modelId: preflight.model_id,
        endpoint: preflight.endpoint,
        remote: preflight.remote,
        latencyMs: preflight.latency_ms,
      } : null,
    });
}

function dispatchEvaluation(detail: DesktopCompanionEvaluationEvent): void {
  emitOmnixEvent(DESKTOP_COMPANION_EVALUATION_EVENT, detail);
}

function disabledSettings(): ShadowWatchSettings {
  return {
    enabled: false,
    requestedStage: 'disabled',
    visionModelId: '',
    remoteVisionAllowed: false,
    backgroundCallsPerMinute: 6,
    minimumObservationIntervalMs: 8_000,
    observationTimeoutMs: 10_000,
    observationTtlMs: 12_000,
    commentaryCooldownMs: 25_000,
    minimumChangeConfidence: 0.55,
  };
}

function disabledRollout(): DesktopCompanionRolloutStatus {
  return {
    requested_stage: 'disabled',
    effective_stage: 'disabled',
    enabled: false,
    reason: 'disabled_by_setting',
    release_gate_status: 'insufficient',
    evidence_evaluation_ids: [],
  };
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value.trim() : fallback;
}

function boundedInt(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.max(minimum, Math.min(maximum, Math.round(numeric)));
}

function boundedNumber(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const numeric = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.max(minimum, Math.min(maximum, numeric));
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}
