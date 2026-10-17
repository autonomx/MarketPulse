import { readLiveConversationEvaluationSnapshot } from './live-conversation-evaluation-controller';
import { liveConversationStore } from './live-conversation-store';
import {
  liveChatEvaluationClient,
  type VoiceSessionEvaluationCreate,
} from './live-chat-evaluation-client';
import { type LiveVoiceLatencyMetric, type LiveVoiceQualityMetric, type LiveVoiceReleaseObservation } from './live-voice-release-observer';
import { ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent, LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT, LIVE_VOICE_RELEASE_OBSERVATION_EVENT } from '../../../events/bus';

let liveDurableEvaluationInstalled = false;

const RELEASE_SCENARIO_KEY = 'omnix.liveCall.releaseScenario';

type ActiveCall = {
  callId: string;
  startedAt: string;
  eosTerminationCounts: Record<string, number>;
  releaseLatencies: Partial<Record<LiveVoiceLatencyMetric, number[]>>;
  releaseQuality: Record<LiveVoiceQualityMetric, boolean[]>;
  duckToCancelMs: number[];
  rejectedCandidateRestoreMs: number[];
};

let activeCall: ActiveCall | null = null;

export function initializeLiveConversationDurableEvaluationController(): () => void {
  if (typeof window === 'undefined') return () => undefined;
  if (liveDurableEvaluationInstalled) return () => undefined;
  liveDurableEvaluationInstalled = true;

  const handleStart = () => {
    activeCall = createActiveCall();
  };
  const handleObservation = (event: Event) => {
    if (!activeCall) return;
    const observation = (event as CustomEvent<LiveVoiceReleaseObservation>).detail;
    if (!observation) return;
    if (observation.kind === 'latency') {
      const values = activeCall.releaseLatencies[observation.metricName] ?? [];
      values.push(observation.valueMs);
      activeCall.releaseLatencies[observation.metricName] = values;
    } else {
      activeCall.releaseQuality[observation.qualityName].push(observation.occurred);
    }
  };
  const handlePerf = (event: Event) => {
    if (!activeCall) return;
    const detail = (event as CustomEvent<Record<string, unknown>>).detail ?? {};
    const reason = terminationReason(detail);
    if (reason && Object.hasOwn(activeCall.eosTerminationCounts, reason)) {
      activeCall.eosTerminationCounts[reason] += 1;
    }
    const stage = typeof detail.stage === 'string' ? detail.stage : '';
    if (stage === 'barge_in_confirmed') {
      pushFinite(activeCall.duckToCancelMs, detail.duck_to_cancel_ms);
    }
    if (stage === 'barge_in_restored') {
      pushFinite(activeCall.rejectedCandidateRestoreMs, detail.elapsed_ms);
    }
  };
  const handleStop = () => {
    const call = activeCall;
    activeCall = null;
    if (!call) return;
    void persistCallEvaluation(call);
  };

  window.addEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleStart);
  window.addEventListener(LIVE_VOICE_RELEASE_OBSERVATION_EVENT, handleObservation);
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);

  return () => {
    window.removeEventListener(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, handleStart);
    window.removeEventListener(LIVE_VOICE_RELEASE_OBSERVATION_EVENT, handleObservation);
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);
    activeCall = null;
    liveDurableEvaluationInstalled = false;
  };
}

export function buildDurableEvaluationPayload(
  call: ActiveCall,
  endedAt = new Date().toISOString(),
): VoiceSessionEvaluationCreate {
  const runtime = liveConversationStore.getState();
  const report = readLiveConversationEvaluationSnapshot().report;
  const calibration = runtime.duplex.calibration;
  return {
    call_id: call.callId,
    session_id: boundedOptionalString(runtime.sessionId, 160),
    started_at: call.startedAt,
    ended_at: endedAt,
    exact_commit_sha: currentCommitSha(),
    app_version: currentAppVersion(),
    browser_version: currentBrowserVersion(),
    os_version: currentOsVersion(),
    character_id: boundedString(runtime.identity.characterId, 'system-assistant', 160),
    // The bridge can briefly observe legacy/runtime metadata while a call is
    // starting. Keep optional versions and enum values inside the API contract
    // instead of allowing a stale `0` or unknown local-storage value to make
    // the whole aggregate POST fail validation.
    profile_version: positiveIntegerOrNull(runtime.identity.profileVersion),
    presence_preset: normalizedPresencePreset(runtime.profile?.presence_preset),
    conversation_stance: normalizedConversationStance(runtime.profile?.conversation_stance),
    configured_duplex_mode: normalizedConfiguredDuplexMode(runtime.duplex.configuredMode),
    resolved_duplex_mode: normalizedResolvedDuplexMode(runtime.duplex.resolvedMode),
    calibration_version: calibration?.version ?? null,
    input_device_hash: calibration?.deviceKey ?? null,
    output_device_hash: calibration?.deviceKey ?? null,
    environment_hash: calibration ? environmentHash(calibration) : null,
    latency_summary: {
      first_audio_average_ms: report.firstAudioLatencyMs.average,
      first_audio_p95_ms: report.firstAudioLatencyMs.p95,
      cancellation_average_ms: report.cancellationLatencyMs.average,
      cancellation_p95_ms: p95(call.duckToCancelMs) ?? report.cancellationLatencyMs.p95,
      rejected_candidate_restore_p95_ms: p95(call.rejectedCandidateRestoreMs),
      turn_duration_median_ms: report.turnDurationMs.median,
      turn_duration_p95_ms: report.turnDurationMs.p95,
      stt_finalize_p95_ms: latencyP95(call, 'stt_finalize_ms'),
      final_to_response_open_p95_ms: latencyP95(call, 'final_to_response_open_ms'),
      response_open_to_first_token_p95_ms: latencyP95(call, 'response_open_to_first_token_ms'),
      final_to_first_token_p95_ms: latencyP95(call, 'final_to_first_token_ms'),
      first_token_to_first_audio_p95_ms: latencyP95(call, 'first_token_to_first_audio_ms'),
      final_to_first_audio_p95_ms: latencyP95(call, 'final_to_first_audio_ms'),
      stt_request_to_first_audio_p95_ms: latencyP95(call, 'stt_request_to_first_audio_ms'),
      first_pcm_to_first_playback_p95_ms: latencyP95(call, 'first_pcm_to_first_playback_ms'),
      first_token_to_first_playback_p95_ms: latencyP95(call, 'first_token_to_first_playback_ms'),
      final_to_first_playback_p95_ms: latencyP95(call, 'final_to_first_playback_ms'),
      stt_request_to_first_playback_p95_ms: latencyP95(call, 'stt_request_to_first_playback_ms'),
      interruption_to_silence_p95_ms: latencyP95(call, 'interruption_to_silence_ms'),
    },
    quality_metrics: {
      event_count: report.eventCount,
      false_endpoint_rate: report.falseEndpointRate,
      talk_over_duration_ms: report.talkOverDurationMs,
      interruption_success_rate: report.interruptionSuccessRate,
      false_barge_in_rate: booleanRate(call.releaseQuality.false_interruption),
      missed_barge_in_rate: booleanRate(call.releaseQuality.missed_interruption),
      playback_echo_submission_rate: booleanRate(call.releaseQuality.playback_echo_submission),
      silence_fill_regret_rate: report.silenceFillRegretRate,
      proactive_acceptance_rate: report.proactiveAcceptanceRate,
      backchannel_collision_rate: report.backchannelCollisionRate,
      question_density: report.questionDensity,
      assistant_user_speaking_ratio: report.assistantUserSpeakingRatio,
      repair_success_rate: report.repairSuccessRate,
      repeated_topic_rate: report.repeatedTopicRate,
      unanswered_obligation_rate: report.unansweredObligationRate,
      perceived_listening_score: report.perceivedListeningScore,
      perceived_pressure_score: report.perceivedPressureScore,
    },
    eos_termination_counts: { ...call.eosTerminationCounts },
    scenario_labels: currentScenarioLabels(),
    release_gate_status: 'insufficient',
    listening_score: report.perceivedListeningScore,
    pressure_score: report.perceivedPressureScore,
  };
}

function normalizedPresencePreset(value: unknown): VoiceSessionEvaluationCreate['presence_preset'] {
  return value === 'quiet' || value === 'natural' || value === 'engaged' || value === 'listener'
    ? value
    : 'natural';
}

function normalizedConversationStance(value: unknown): VoiceSessionEvaluationCreate['conversation_stance'] {
  return value === 'automatic'
    || value === 'listen'
    || value === 'discuss'
    || value === 'advise'
    || value === 'brainstorm'
    || value === 'teach'
    ? value
    : 'automatic';
}

function normalizedConfiguredDuplexMode(
  value: unknown,
): VoiceSessionEvaluationCreate['configured_duplex_mode'] {
  return value === 'automatic' || value === 'half_duplex' || value === 'echo_aware'
    ? value
    : 'automatic';
}

function normalizedResolvedDuplexMode(
  value: unknown,
): VoiceSessionEvaluationCreate['resolved_duplex_mode'] {
  return value === 'echo_aware' ? 'echo_aware' : 'half_duplex';
}

function boundedString(value: unknown, fallback: string, maximum: number): string {
  const normalized = typeof value === 'string' ? value.trim().slice(0, maximum) : '';
  return normalized || fallback;
}

function boundedOptionalString(value: unknown, maximum: number): string | null {
  const normalized = typeof value === 'string' ? value.trim().slice(0, maximum) : '';
  return normalized || null;
}

function positiveIntegerOrNull(value: number | null): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : null;
}

async function persistCallEvaluation(call: ActiveCall): Promise<void> {
  try {
    const record = await liveChatEvaluationClient.upsert(buildDurableEvaluationPayload(call));
    const gate = await liveChatEvaluationClient.releaseGate({ persistStatus: true });
    emitOmnixEvent(LIVE_CONVERSATION_DURABLE_EVALUATION_SAVED_EVENT, { record: { ...record, release_gate_status: gate.status }, gate });
  } catch (error) {
    emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, {
        stage: 'durable_evaluation_save_failed',
        error_name: error instanceof Error ? error.name : 'unknown',
        timestamp: new Date().toISOString(),
      });
  }
}

function createActiveCall(): ActiveCall {
  return {
    callId: createCallId(),
    startedAt: new Date().toISOString(),
    eosTerminationCounts: {
      natural_eos: 0,
      forced_eos: 0,
      token_limit: 0,
      sequence_limit: 0,
      model_stopped: 0,
    },
    releaseLatencies: {
      stt_finalize_ms: [],
      final_to_response_open_ms: [],
      response_open_to_first_token_ms: [],
      final_to_first_token_ms: [],
      first_token_to_first_audio_ms: [],
      final_to_first_audio_ms: [],
      stt_request_to_first_audio_ms: [],
      first_pcm_to_first_playback_ms: [],
      first_token_to_first_playback_ms: [],
      final_to_first_playback_ms: [],
      stt_request_to_first_playback_ms: [],
      interruption_to_silence_ms: [],
    },
    releaseQuality: {
      false_interruption: [],
      missed_interruption: [],
      backchannel_false_positive: [],
      playback_echo_submission: [],
    },
    duckToCancelMs: [],
    rejectedCandidateRestoreMs: [],
  };
}

function terminationReason(detail: Record<string, unknown>): string {
  const direct = detail.termination_reason;
  if (typeof direct === 'string') return direct;
  const timing = detail.provider_timing;
  if (
    timing
    && typeof timing === 'object'
    && typeof (timing as Record<string, unknown>).termination_reason === 'string'
  ) {
    return String((timing as Record<string, unknown>).termination_reason);
  }
  return '';
}

function createCallId(): string {
  const suffix = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `live-call:${suffix}`;
}

function currentCommitSha(): string {
  const value = document.querySelector<HTMLMetaElement>('meta[name="omnix-commit-sha"]')?.content
    || document.documentElement.dataset.commitSha
    || 'unknown0';
  const normalized = value.trim().slice(0, 64);
  return normalized.length >= 7 ? normalized : 'unknown0';
}

function currentAppVersion(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="omnix-app-version"]')?.content
    || document.documentElement.dataset.appVersion
    || 'unknown';
}

function currentBrowserVersion(): string {
  const userAgentData = (navigator as Navigator & {
    userAgentData?: { brands?: Array<{ brand: string; version: string }> };
  }).userAgentData;
  const brands = userAgentData?.brands
    ?.map((entry) => `${entry.brand} ${entry.version}`)
    .join(', ')
    .trim();
  return (brands || navigator.userAgent || 'unknown').slice(0, 240);
}

function currentOsVersion(): string {
  const userAgentData = (
    navigator as Navigator & { userAgentData?: { platform?: string } }
  ).userAgentData;
  const platform = userAgentData?.platform || navigator.platform || 'unknown';
  const userAgent = navigator.userAgent || '';
  const windows = userAgent.match(/Windows NT [0-9.]+/i)?.[0];
  const mac = userAgent.match(/Mac OS X [0-9_]+/i)?.[0]?.replaceAll('_', '.');
  const android = userAgent.match(/Android [0-9.]+/i)?.[0];
  return `${platform}${windows || mac || android ? ` · ${windows || mac || android}` : ''}`.slice(0, 160);
}

function currentScenarioLabels(): string[] {
  const raw = window.localStorage.getItem(RELEASE_SCENARIO_KEY) ?? '';
  return raw.split(',')
    .map((value) => value.trim().toLocaleLowerCase())
    .filter((value) => /^[a-z0-9_.:-]{1,160}$/.test(value))
    .slice(0, 64);
}

function latencyP95(call: ActiveCall, metric: LiveVoiceLatencyMetric): number | null {
  return p95(call.releaseLatencies[metric] ?? []);
}

function p95(values: number[]): number | null {
  if (!values.length) return null;
  const ordered = [...values]
    .filter(Number.isFinite)
    .sort((left, right) => left - right);
  if (!ordered.length) return null;
  return Number(
    ordered[Math.max(0, Math.ceil(ordered.length * 0.95) - 1)].toFixed(3),
  );
}

function booleanRate(values: boolean[]): number | null {
  return values.length
    ? Number((values.filter(Boolean).length / values.length).toFixed(3))
    : null;
}

function pushFinite(target: number[], value: unknown): void {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    target.push(value);
  }
}

function environmentHash(calibration: {
  deviceKey: string;
  confidence: number;
  delayMs: number;
  noiseFloorRms: number;
}): string {
  const source = `${calibration.deviceKey}|${calibration.confidence.toFixed(3)}|${calibration.delayMs.toFixed(1)}|${calibration.noiseFloorRms.toFixed(5)}`;
  let hash = 2166136261;
  for (const character of source) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `environment-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}
