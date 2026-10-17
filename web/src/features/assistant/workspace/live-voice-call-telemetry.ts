/** Diagnostics recorded as a capture call starts: the task contract, STT authority and STT client events. */
import { type LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { liveConversationStore } from './live-conversation-store';
import { currentLiveRuntimeProvenance } from './live-runtime-provenance';
import { liveSessionCoordinator } from './live-session-coordinator';
import { StreamingSttWebSocketClient } from './live-voice-websocket';
import { LiveVoiceSession, dispatchLiveVoicePerfEvent, readLiveTaskInstruction } from './live-voice-controller-state';
import { LiveSttSegmentTelemetryGate } from './live-voice-endpointing';

/** Acknowledges the call's task contract before capture starts, and records it. */
export async function prepareLiveTaskContract(sessionId: string, reporter: LiveCallDiagnosticsReporter): Promise<void> {
  const provenance = currentLiveRuntimeProvenance();
  reporter.record('live_runtime_provenance', provenance, 'live_voice_controller');
  const taskInstruction = readLiveTaskInstruction();
  await liveSessionCoordinator.prepareTaskContract(sessionId, taskInstruction);
  const coordination = liveConversationStore.getState().coordination;
  reporter.record('live_task_contract_acknowledged', {
    ...provenance,
    task_instruction_configured: Boolean(taskInstruction),
    task_contract_id: coordination.taskContract.taskContractId,
    task_contract_version: coordination.taskContract.version,
    context_version: coordination.contextVersion,
  }, 'live_voice_controller');
  dispatchLiveVoicePerfEvent({
    stage: 'live_task_contract_acknowledged',
    timestamp: new Date().toISOString(),
    taskInstructionConfigured: Boolean(taskInstruction),
    ...provenance,
  });
}

/** Records which STT provider has authority over the call's turn endings. */
export function recordSttAuthoritySelection(
  reporter: LiveCallDiagnosticsReporter,
  configuredSttUrl: string | undefined,
  sttAuthority: LiveVoiceSession['sttAuthority'],
): void {
  const selectedProvider = sttAuthority.authorityEnabled
    ? 'configured_authoritative'
    : sttAuthority.fallbackUsed
      ? 'fallback'
      : configuredSttUrl?.trim()
        ? 'configured_observational'
        : 'default_stt';
  reporter.record('stt_authority_selected', {
    selected_provider: selectedProvider,
    authority_enabled: sttAuthority.authorityEnabled,
    authority_mode: sttAuthority.mode,
    fallback_used: sttAuthority.fallbackUsed,
    endpoint_threshold: sttAuthority.endpointThreshold,
    reasons: sttAuthority.reasons,
  }, 'live_voice_controller');
  dispatchLiveVoicePerfEvent({
    stage: 'stt_authority_selected',
    timestamp: new Date().toISOString(),
    selectedProvider,
    authorityEnabled: sttAuthority.authorityEnabled,
    authorityMode: sttAuthority.mode,
    fallbackUsed: sttAuthority.fallbackUsed,
    endpointThreshold: sttAuthority.endpointThreshold,
    reasons: sttAuthority.reasons,
  });
}

/** The STT client's telemetry callbacks: each event goes to the call's diagnostics and the perf stream. */
export function createSttTelemetryHandlers(reporter: LiveCallDiagnosticsReporter) {
  const segmentTelemetryGate = new LiveSttSegmentTelemetryGate();
  return {
    onNegotiated: (negotiation) => {
      reporter.record('stt_negotiated', {
        provider: negotiation.provider,
        protocol: negotiation.protocol,
        sample_rate: negotiation.sampleRate,
        frame_samples: negotiation.frameSamples,
        encoding: negotiation.encoding,
        capabilities: negotiation.capabilities,
        config_version: negotiation.configVersion,
        language: negotiation.language,
      }, 'live_voice_controller');
      dispatchLiveVoicePerfEvent({
        stage: 'stt_negotiated',
        timestamp: new Date().toISOString(),
        provider: negotiation.provider,
        protocol: negotiation.protocol,
        sampleRate: negotiation.sampleRate,
        frameSamples: negotiation.frameSamples,
        encoding: negotiation.encoding,
        capabilities: negotiation.capabilities,
        configVersion: negotiation.configVersion,
        language: negotiation.language,
      });
    },
    onEndpointScore: (event) => {
      reporter.record('stt_endpoint_score', {
        provider: event.provider,
        segment_id: event.segmentId,
        source_sequence: event.sequence,
        probability: event.probability,
        model_time_ms: event.modelTimeMs,
        signal: event.signal,
      }, 'live_voice_controller');
      dispatchLiveVoicePerfEvent({
        stage: 'stt_endpoint_score',
        timestamp: new Date().toISOString(),
        provider: event.provider,
        segmentId: event.segmentId,
        sourceSequence: event.sequence,
        probability: event.probability,
        modelTimeMs: event.modelTimeMs,
        signal: event.signal,
      });
    },
    onProviderEvent: (event) => {
      const stage = `stt_${event.type}`;
      reporter.record(stage, {
        provider: event.provider,
        attempt_id: event.attemptId,
        wall_ms: event.wall_ms,
        model_ms: event.model_ms,
        realtime_factor: event.realtime_factor,
      }, 'live_voice_controller');
      dispatchLiveVoicePerfEvent({
        stage,
        timestamp: new Date().toISOString(),
        provider: event.provider,
        attemptId: event.attemptId,
        wallMs: event.wall_ms,
        modelMs: event.model_ms,
        realtimeFactor: event.realtime_factor,
      });
    },
    onSegmentStateChange: (state) => {
      if (!segmentTelemetryGate.shouldReport(state)) return;
      dispatchLiveVoicePerfEvent({
        stage: 'stt_segment_state',
        timestamp: new Date().toISOString(),
        protocol: state.protocol,
        activeSequence: state.activeSequence,
        pendingSegments: state.pendingSegments,
        queuedSegments: state.queuedSegments,
        absoluteSample: state.absoluteSample,
      });
    },
  } satisfies Partial<ConstructorParameters<typeof StreamingSttWebSocketClient>[0]>;
}
