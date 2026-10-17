import { createLiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { type LiveVoiceTurnTimelineDetail } from './live-voice-turn-coordinator';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, ASSISTANT_VOICE_PERF_EVENT, ASSISTANT_VOICE_RELEASE_QUALITY_EVENT, emitOmnixEvent, LIVE_CALL_DIAGNOSTIC_EVENT, LIVE_VOICE_RELEASE_OBSERVATION_EVENT, LIVE_VOICE_TURN_TIMELINE_EVENT } from '../../../events/bus';

const SCENARIO_KEY = 'omnix.liveCall.releaseScenario';

export type LiveVoiceLatencyMetric =
  | 'speech_end_to_first_playback_ms'
  | 'stt_finalize_ms'
  | 'final_to_response_open_ms'
  | 'response_open_to_first_token_ms'
  | 'final_to_first_token_ms'
  | 'first_token_to_first_audio_ms'
  | 'final_to_first_audio_ms'
  | 'stt_request_to_first_audio_ms'
  | 'first_pcm_to_first_playback_ms'
  | 'first_token_to_first_playback_ms'
  | 'final_to_first_playback_ms'
  | 'stt_request_to_first_playback_ms'
  | 'interruption_to_silence_ms';

export type LiveVoiceQualityMetric =
  | 'false_interruption'
  | 'missed_interruption'
  | 'backchannel_false_positive'
  | 'playback_echo_submission';

export type LiveVoiceReleaseObservation =
  | { kind: 'latency'; metricName: LiveVoiceLatencyMetric; valueMs: number; scenario: string }
  | { kind: 'quality'; qualityName: LiveVoiceQualityMetric; occurred: boolean; scenario: string };

type PerfDetail = {
  stage?: unknown;
  turnId?: unknown;
  sttFinalizeMs?: unknown;
  inputChars?: unknown;
  input_chars?: unknown;
  transcriptChars?: unknown;
  transcript_chars?: unknown;
  provider?: unknown;
  segmentId?: unknown;
  sourceSequence?: unknown;
  providerMetrics?: unknown;
};

type DiagnosticDetail = {
  traceId?: unknown;
  source?: unknown;
  event?: unknown;
  details?: Record<string, unknown>;
};

type QualityDetail = {
  qualityName?: unknown;
  occurred?: unknown;
  scenario?: unknown;
};

type ReleaseState = {
  speechEndedAt: number | null;
  sttRequestedAt: number | null;
  sttFinalAt: number | null;
  responseOpenedAt: number | null;
  firstTokenAt: number | null;
  firstPcmAt: number | null;
  firstPcmOutputId: string | null;
  firstPcmSegmentId: string | null;
  firstPlaybackAt: number | null;
  interruptionAt: number | null;
  turnId: string | null;
  activeTraceId: string | null;
};

const reporter = typeof window === 'undefined'
  ? null
  : createLiveCallDiagnosticsReporter('live-call:release-observer');
let initialized = false;
let state: ReleaseState = emptyState();

export function initializeLiveVoiceReleaseObserver(): () => void {
  if (initialized || typeof window === 'undefined') return () => undefined;
  initialized = true;
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerfEvent);
  window.addEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnosticEvent);
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterruption);
  window.addEventListener(ASSISTANT_VOICE_RELEASE_QUALITY_EVENT, handleQualityEvent);
  window.addEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, handleTurnTimeline);
  return () => {
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerfEvent);
    window.removeEventListener(LIVE_CALL_DIAGNOSTIC_EVENT, handleDiagnosticEvent);
    window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleInterruption);
    window.removeEventListener(ASSISTANT_VOICE_RELEASE_QUALITY_EVENT, handleQualityEvent);
    window.removeEventListener(LIVE_VOICE_TURN_TIMELINE_EVENT, handleTurnTimeline);
    initialized = false;
  };
}

export function recordLiveVoiceReleaseQuality(
  qualityName: LiveVoiceQualityMetric,
  occurred: boolean,
  scenario = currentScenario(),
): void {
  reporter?.record('release_quality', {
    quality_name: qualityName,
    occurred,
    scenario,
  }, 'release_observer');
  dispatchObservation({ kind: 'quality', qualityName, occurred, scenario });
}

export function resetLiveVoiceReleaseObserver(): void {
  state = emptyState();
}

function handleTurnTimeline(event: Event): void {
  const detail = (event as CustomEvent<LiveVoiceTurnTimelineDetail>).detail;
  if (!detail?.turnId || detail.event !== 'speech_ended') return;
  if (state.turnId !== null && state.turnId !== detail.turnId) {
    state = { ...emptyState(), turnId: detail.turnId };
  } else {
    state.turnId = detail.turnId;
  }
  // Repeated speech-end events represent pause/resume cycles within the same
  // utterance. The latest pause is the one that should anchor release latency.
  state.speechEndedAt = detail.atMs;
}

function handlePerfEvent(event: Event): void {
  const detail = (event as CustomEvent<PerfDetail>).detail ?? {};
  const stage = typeof detail.stage === 'string' ? detail.stage : '';
  if (stage === 'stt_provider_final') {
    reporter?.record('stt_provider_final', {
      provider: normalizedString(detail.provider),
      segment_id: normalizedString(detail.segmentId),
      source_sequence: finiteNonnegative(detail.sourceSequence),
      transcript_chars: finiteNonnegative(detail.transcriptChars ?? detail.transcript_chars),
      ...finiteNumericRecord(detail.providerMetrics),
    }, 'release_observer');
    return;
  }
  const now = performance.now();
  const incomingTurnId = normalizedTurnId(detail.turnId);
  if (stage === 'stt_final_requested') {
    const preserveSpeechEnd = incomingTurnId !== null
      && state.turnId === incomingTurnId
      ? state.speechEndedAt
      : null;
    state = {
      ...emptyState(),
      speechEndedAt: preserveSpeechEnd,
      sttRequestedAt: now,
      turnId: incomingTurnId,
    };
    return;
  }
  if (stage === 'stt_final_received') {
    const transcriptChars = finiteNonnegative(
      detail.inputChars
      ?? detail.input_chars
      ?? detail.transcriptChars
      ?? detail.transcript_chars,
    );
    if (transcriptChars === 0) {
      reporter?.record('release_metric_skipped', {
        metric_name: 'turn_latency',
        reason: 'empty_final_transcript',
        incoming_turn_id: incomingTurnId,
      }, 'release_observer');
      state = emptyState();
      return;
    }

    const requestedAt = state.sttRequestedAt;
    const requestedTurnId = state.turnId;
    const matchingRequest = requestedAt !== null
      && (incomingTurnId === null || requestedTurnId === incomingTurnId);
    const explicitFinalizeMs = finiteNonnegative(detail.sttFinalizeMs);

    if (incomingTurnId !== null && incomingTurnId !== requestedTurnId) {
      state = { ...emptyState(), turnId: incomingTurnId };
    } else if (incomingTurnId !== null) {
      state.turnId = incomingTurnId;
    }

    const observed = explicitFinalizeMs
      ?? (matchingRequest ? elapsed(requestedAt, now) : null);
    if (observed === null) {
      reporter?.record('release_metric_skipped', {
        metric_name: 'stt_finalize_ms',
        reason: 'missing_matching_stt_final_requested',
        incoming_turn_id: incomingTurnId,
        requested_turn_id: requestedTurnId,
      }, 'release_observer');
      return;
    }

    recordLatency('stt_finalize_ms', observed);
    state.sttFinalAt = now;
  }
}

function handleDiagnosticEvent(event: Event): void {
  const detail = (event as CustomEvent<DiagnosticDetail>).detail ?? {};
  const diagnosticEvent = typeof detail.event === 'string' ? detail.event : '';
  const traceId = typeof detail.traceId === 'string' ? detail.traceId : null;
  if (!traceId || traceId === 'live-call:release-observer') return;
  const now = performance.now();

  if (diagnosticEvent === 'turn_intercepted') {
    if (!traceMatchesCurrentVoiceTurn(traceId)) return;
    state.activeTraceId = traceId;
    return;
  }
  if (!diagnosticBelongsToActiveTurn(detail, diagnosticEvent, traceId)) return;
  if (diagnosticEvent === 'turn_finished') {
    state = emptyState();
    return;
  }
  if (diagnosticEvent === 'chat_response_opened' && state.responseOpenedAt === null) {
    state.responseOpenedAt = now;
    recordLatency('final_to_response_open_ms', elapsed(state.sttFinalAt, now));
    return;
  }
  if (diagnosticEvent === 'llm_text_chunk_received' && state.firstTokenAt === null) {
    state.firstTokenAt = now;
    recordLatency('response_open_to_first_token_ms', elapsed(state.responseOpenedAt, now));
    recordLatency('final_to_first_token_ms', elapsed(state.sttFinalAt, now));
    return;
  }
  if (
    diagnosticEvent === 'phrase_first_frame_received'
    && state.firstPcmAt === null
    && state.activeTraceId !== null
    && state.sttFinalAt !== null
    && state.firstTokenAt !== null
  ) {
    state.firstPcmAt = now;
    state.firstPcmOutputId = diagnosticString(detail, 'output_id');
    state.firstPcmSegmentId = diagnosticString(detail, 'segment_id');
    recordLatency('first_token_to_first_audio_ms', elapsed(state.firstTokenAt, now));
    recordLatency('final_to_first_audio_ms', elapsed(state.sttFinalAt, now));
    recordLatency('stt_request_to_first_audio_ms', elapsed(state.sttRequestedAt, now));
    return;
  }
  if (
    diagnosticEvent === 'worklet_segment_started'
    && state.firstPlaybackAt === null
    && state.activeTraceId !== null
    && state.sttFinalAt !== null
    && state.firstTokenAt !== null
    && state.firstPcmAt !== null
    && isSpeechPlayback(detail)
    && playbackMatchesFirstPcm(detail)
  ) {
    const renderedAt = finiteNonnegative(
      detail.details?.playback_performance_time_ms,
    );
    const playbackAt = Math.max(
      state.firstPcmAt,
      Math.min(now, renderedAt ?? now),
    );
    state.firstPlaybackAt = playbackAt;
    recordLatency('speech_end_to_first_playback_ms', elapsed(state.speechEndedAt, playbackAt));
    recordLatency('first_pcm_to_first_playback_ms', elapsed(state.firstPcmAt, playbackAt));
    recordLatency('first_token_to_first_playback_ms', elapsed(state.firstTokenAt, playbackAt));
    recordLatency('final_to_first_playback_ms', elapsed(state.sttFinalAt, playbackAt));
    recordLatency('stt_request_to_first_playback_ms', elapsed(state.sttRequestedAt, playbackAt));
    return;
  }
  if (diagnosticEvent === 'turn_stopped' && state.interruptionAt !== null) {
    recordLatency('interruption_to_silence_ms', elapsed(state.interruptionAt, now));
    state.interruptionAt = null;
  }
}

function diagnosticBelongsToActiveTurn(
  detail: DiagnosticDetail,
  diagnosticEvent: string,
  traceId: string,
): boolean {
  if (isTurnBoundDiagnostic(diagnosticEvent) && !traceMatchesCurrentVoiceTurn(traceId)) {
    return false;
  }
  if (!state.activeTraceId || traceId === state.activeTraceId) return true;
  const crossTraceAudio = diagnosticEvent === 'phrase_first_frame_received'
    && detail.source === 'pcm_session'
    && state.firstTokenAt !== null
    && state.firstPcmAt === null;
  const crossTracePlayback = diagnosticEvent === 'worklet_segment_started'
    && detail.source === 'audio_worklet'
    && state.firstTokenAt !== null
    && state.firstPcmAt !== null
    && state.firstPlaybackAt === null
    && isSpeechPlayback(detail)
    && playbackMatchesFirstPcm(detail);
  return crossTraceAudio || crossTracePlayback;
}

function isTurnBoundDiagnostic(event: string): boolean {
  return event === 'chat_response_opened'
    || event === 'llm_text_chunk_received'
    || event === 'turn_finished'
    || event === 'turn_stopped';
}

function traceMatchesCurrentVoiceTurn(traceId: string): boolean {
  return state.turnId === null || traceId === `live-call:${state.turnId}`;
}

function diagnosticString(detail: DiagnosticDetail, key: string): string | null {
  const value = detail.details?.[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function isSpeechPlayback(detail: DiagnosticDetail): boolean {
  const kind = diagnosticString(detail, 'segment_kind');
  return kind === null || kind === 'speech';
}

function playbackMatchesFirstPcm(detail: DiagnosticDetail): boolean {
  const outputId = diagnosticString(detail, 'output_id');
  const segmentId = diagnosticString(detail, 'segment_id');
  if (state.firstPcmOutputId !== null && outputId !== null) {
    return state.firstPcmOutputId === outputId;
  }
  if (state.firstPcmSegmentId !== null && segmentId !== null) {
    return state.firstPcmSegmentId === segmentId;
  }
  return state.firstPcmOutputId === null && state.firstPcmSegmentId === null;
}

function handleInterruption(): void {
  state.interruptionAt = performance.now();
}

function handleQualityEvent(event: Event): void {
  const detail = (event as CustomEvent<QualityDetail>).detail ?? {};
  const qualityName = detail.qualityName;
  if (!isQualityMetric(qualityName) || typeof detail.occurred !== 'boolean') return;
  recordLiveVoiceReleaseQuality(
    qualityName,
    detail.occurred,
    typeof detail.scenario === 'string' ? detail.scenario : currentScenario(),
  );
}

function recordLatency(metricName: LiveVoiceLatencyMetric, valueMs: number | null): void {
  if (valueMs === null) return;
  const rounded = Math.round(valueMs * 1000) / 1000;
  const scenario = currentScenario();
  reporter?.record('release_metric', {
    metric_name: metricName,
    value_ms: rounded,
    scenario,
    turn_id: state.turnId,
    observed_trace_id: state.activeTraceId,
  }, 'release_observer');
  dispatchObservation({ kind: 'latency', metricName, valueMs: rounded, scenario });
}

function dispatchObservation(observation: LiveVoiceReleaseObservation): void {
  if (typeof window === 'undefined') return;
  emitOmnixEvent(LIVE_VOICE_RELEASE_OBSERVATION_EVENT, observation);
}

function elapsed(start: number | null, end: number): number | null {
  return start === null ? null : Math.max(0, end - start);
}

function finiteNonnegative(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

function finiteNumericRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1]),
    ),
  );
}

function normalizedString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function normalizedTurnId(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function currentScenario(): string {
  try {
    const value = window.localStorage.getItem(SCENARIO_KEY)?.trim();
    return value || 'unlabeled';
  } catch {
    return 'unlabeled';
  }
}

function isQualityMetric(value: unknown): value is LiveVoiceQualityMetric {
  return value === 'false_interruption'
    || value === 'missed_interruption'
    || value === 'backchannel_false_positive'
    || value === 'playback_echo_submission';
}

function emptyState(): ReleaseState {
  return {
    speechEndedAt: null,
    sttRequestedAt: null,
    sttFinalAt: null,
    responseOpenedAt: null,
    firstTokenAt: null,
    firstPcmAt: null,
    firstPcmOutputId: null,
    firstPcmSegmentId: null,
    firstPlaybackAt: null,
    interruptionAt: null,
    turnId: null,
    activeTraceId: null,
  };
}
