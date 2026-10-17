/** Provider endpoint candidates and partial transcripts: whether a pause ends the turn. */
import { liveConversationStore } from './live-conversation-store';
import { assessSemanticTurn } from './live-voice-floor-manager';
import { type StreamingSttSegmentState } from './live-voice-websocket';
import { endpointFusionAction } from './live-voice-turn-coordinator';
import { EndpointCommitState, PROVIDER_ENDPOINT_MIN_SILENCE_MS, ProviderEndpointCandidate, STT_SEGMENT_TELEMETRY_INTERVAL_MS, controller, dispatchLiveVoicePerfEvent, liveVoiceAssistantIsSpeaking, readConversationPace, renderTranscript } from './live-voice-controller-state';
import { requestFinalTranscript, rescheduleSemanticFinalization } from './live-voice-finalization';
import { assessOverlapCandidate } from './live-voice-preview';
import { emitOmnixEvent, LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

export class LiveSttSegmentTelemetryGate {
  private structuralKey = '';
  private lastReportedAt = Number.NEGATIVE_INFINITY;

  shouldReport(state: StreamingSttSegmentState, now = performance.now()): boolean {
    const structuralKey = [
      state.protocol ?? '',
      state.activeSequence ?? '',
      state.pendingSegments,
      state.queuedSegments,
    ].join(':');
    const structuralChange = structuralKey !== this.structuralKey;
    if (!structuralChange
      && now - this.lastReportedAt < STT_SEGMENT_TELEMETRY_INTERVAL_MS) return false;
    this.structuralKey = structuralKey;
    this.lastReportedAt = now;
    return true;
  }
}

export function shouldCommitProviderEndpoint(state: EndpointCommitState): boolean {
  if (
    !state.authorityEnabled
    || !Number.isFinite(state.probability)
    || !state.speechDetected
    || state.finalRequested
    || !state.pausePending
  ) return false;
  return endpointFusionAction({
    endpointProbability: state.probability,
    endpointThreshold: state.endpointThreshold,
    silenceMs: state.pauseElapsedMs,
    transcriptStableMs: state.transcriptStableMs ?? 80,
    semanticProbabilityDone: state.semanticProbabilityDone ?? 1,
    transcriptWords: state.transcriptWords ?? 2,
    correctionPending: state.correctionPending ?? false,
  }) === 'commit';
}

export function handleProviderEndpointCandidate(card: HTMLElement, event: ProviderEndpointCandidate): void {
  const session = controller.activeSession;
  if (!session || session.card !== card) return;
  const now = performance.now();
  const candidateText = session.partialTranscript.trim();
  const pauseElapsedMs = session.pauseStartedAt === null
    ? 0
    : Math.max(0, now - session.pauseStartedAt);
  const assessment = assessSemanticTurn(candidateText, readConversationPace());
  const transcriptStableMs = Math.max(0, now - session.partialTranscriptUpdatedAt);
  const transcriptWords = candidateText ? candidateText.split(/\s+/u).length : 0;
  const correctionPending = assessment.reason === 'self_correction';
  const fusionAction = endpointFusionAction({
    endpointProbability: event.probability,
    endpointThreshold: session.sttAuthority.endpointThreshold,
    silenceMs: pauseElapsedMs,
    transcriptStableMs,
    semanticProbabilityDone: assessment.probabilityDone,
    transcriptWords,
    correctionPending,
  });
  session.reporter.record('stt_endpoint_candidate', {
    provider: event.provider,
    segment_id: event.segmentId,
    source_sequence: event.sequence,
    probability: event.probability,
    model_time_ms: event.modelTimeMs,
    transcript_chars: candidateText.length,
    transcript_words: transcriptWords,
    transcript_stable_ms: Math.round(transcriptStableMs),
    semantic_probability_done: assessment.probabilityDone,
    semantic_reason: assessment.reason,
    endpoint_fusion_action: fusionAction,
    authority_enabled: session.sttAuthority.authorityEnabled,
    pause_elapsed_ms: Math.round(pauseElapsedMs),
    endpoint_min_silence_ms: PROVIDER_ENDPOINT_MIN_SILENCE_MS,
  }, 'live_voice_controller');
  dispatchLiveVoicePerfEvent({
    stage: 'stt_endpoint_candidate',
    timestamp: new Date().toISOString(),
    provider: event.provider,
    segmentId: event.segmentId,
    sourceSequence: event.sequence,
    probability: event.probability,
    modelTimeMs: event.modelTimeMs,
    transcriptChars: candidateText.length,
    transcriptWords,
    transcriptStableMs: Math.round(transcriptStableMs),
    semanticProbabilityDone: assessment.probabilityDone,
    semanticReason: assessment.reason,
    endpointFusionAction: fusionAction,
    authorityEnabled: session.sttAuthority.authorityEnabled,
    pauseElapsedMs: Math.round(pauseElapsedMs),
    endpointMinSilenceMs: PROVIDER_ENDPOINT_MIN_SILENCE_MS,
  });
  if (
    session.sttAuthority.authorityEnabled
    && !session.client.authoritativePreviewSupported
    && candidateText
    && fusionAction !== 'continue'
  ) {
    session.speculationSegmentId = event.segmentId;
    session.speculationSourceSequence = event.sequence;
    const detail = {
      chatSessionId: liveConversationStore.getState().sessionId,
      segmentId: event.segmentId,
      sourceSequence: event.sequence,
      text: candidateText,
    };
    emitOmnixEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, detail);
    emitOmnixEvent(LIVE_STT_SPECULATION_CANDIDATE_EVENT, {
      ...detail,
      probability: event.probability,
      modelTimeMs: event.modelTimeMs,
    });
  }
  if (!shouldCommitProviderEndpoint({
    authorityEnabled: session.sttAuthority.authorityEnabled,
    probability: event.probability,
    endpointThreshold: session.sttAuthority.endpointThreshold,
    speechDetected: session.speechDetected,
    finalRequested: session.finalRequested,
    pausePending: session.silenceTimer !== null,
    pauseElapsedMs,
    transcriptStableMs,
    semanticProbabilityDone: assessment.probabilityDone,
    transcriptWords,
    correctionPending,
  })) return;
  requestFinalTranscript(session, 'provider_endpoint', event);
}

export function handlePartialTranscript(card: HTMLElement, text: string): void {
  const session = controller.activeSession;
  if (session?.card === card) {
    const normalized = text.trim();
    const transcriptChanged = normalized !== session.partialTranscript;
    if (transcriptChanged) {
      session.partialTranscript = normalized;
      session.partialTranscriptUpdatedAt = performance.now();
      if (
        session.silenceTimer
        && session.pauseStartedAt !== null
        && !session.finalRequested
      ) {
        rescheduleSemanticFinalization(session);
      }
    }
    if (
      session.sttAuthority.authorityEnabled
      && session.speculationSegmentId
      && session.speculationSourceSequence !== null
    ) {
      emitOmnixEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, {
        chatSessionId: liveConversationStore.getState().sessionId,
        segmentId: session.speculationSegmentId,
        sourceSequence: session.speculationSourceSequence,
        text: session.authoritativePreviewText && session.pauseStartedAt !== null
          ? session.authoritativePreviewText
          : session.partialTranscript,
      });
    }
    if (session.floorState === 'overlap_candidate' && liveVoiceAssistantIsSpeaking()) {
      assessOverlapCandidate(session);
    }
  }
  renderTranscript('You', text, 'draft');
}
