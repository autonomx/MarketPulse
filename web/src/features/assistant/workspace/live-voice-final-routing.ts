/** Routes an accepted final transcript to the conversation, or suppresses it. */
import type { AcceptedVoiceFinal, LiveFinalRoutingResult } from './live-accepted-final';
import { acceptedFinalSuppressionReason } from './live-accepted-final-routing';
import { liveConversationStore } from './live-conversation-store';
import { currentLiveRuntimeProvenance } from './live-runtime-provenance';
import { liveSessionCoordinator } from './live-session-coordinator';
import { classifyOverlap } from './live-voice-overlap-classifier';
import { controller, currentAssistantSpeechText, dispatchLiveVoicePerfEvent, renderTranscript, setPanelStatus } from './live-voice-controller-state';
import { replayFinalizationBuffer } from './live-voice-finalization';
import { resetTurnState } from './live-voice-session-lifecycle';
import { emitOmnixEvent, LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, LIVE_STT_SPECULATION_FINAL_EVENT } from '../../../events/bus';

export async function handleAcceptedFinal(card: HTMLElement, final: AcceptedVoiceFinal): Promise<LiveFinalRoutingResult> {
  const session = controller.activeSession;
  if (!session || session.card !== card) {
    return failedRoutingResult(final, 'live_capture_session_inactive');
  }
  const receivedAt = performance.now();
  const partialOverlapIntent = session.overlapIntent;
  const finalOverlapAssessment = partialOverlapIntent === 'uncertain'
    ? classifyOverlap(final.text, currentAssistantSpeechText())
    : null;
  const overlapIntent = finalOverlapAssessment?.intent ?? partialOverlapIntent;
  const interruptionDispatched = session.interruptionDispatched;
  const suppressionReason = acceptedFinalSuppressionReason(final.text, overlapIntent);
  const continuation = session.finalizationBuffer.drain();
  emitOmnixEvent(LIVE_STT_SPECULATION_FINAL_EVENT, {
    chatSessionId: final.chatSessionId,
    segmentId: final.segmentId,
    sourceSequence: final.sourceSequence,
    text: final.text,
  });
  dispatchLiveVoicePerfEvent({
    stage: 'stt_final_received',
    turnId: session.perfTurnId ?? `voice-turn:${Date.now()}`,
    timestamp: new Date().toISOString(),
    transcriptChars: final.text.trim().length,
    sttFinalizeMs: session.sttFinalRequestedAt === null ? undefined : Math.round(receivedAt - session.sttFinalRequestedAt),
    segmentId: final.segmentId,
    sourceSequence: final.sourceSequence,
    captureEpoch: final.captureEpoch,
  });
  session.reporter.record('stt_final_received', {
    ...currentLiveRuntimeProvenance(),
    chat_session_id: final.chatSessionId,
    stt_session_id: final.sttSessionId,
    capture_epoch: final.captureEpoch,
    segment_id: final.segmentId,
    result_id: final.resultId,
    finalize_request_id: final.finalizeRequestId,
    source_sequence: final.sourceSequence,
    start_sample: final.startSample,
    end_sample: final.endSample,
    protocol: final.protocol,
    transcript_chars: final.text.trim().length,
    stt_finalize_ms: session.sttFinalRequestedAt === null ? undefined : Math.round(receivedAt - session.sttFinalRequestedAt),
    overlap_intent: overlapIntent,
    overlap_confidence: finalOverlapAssessment?.confidence,
    overlap_reason: finalOverlapAssessment?.reason,
    interruption_dispatched: interruptionDispatched,
  }, 'live_voice_controller');
  resetTurnState(session);
  try {
    if (suppressionReason) {
      const result = ignoredRoutingResult(final);
      session.reporter.record('coordination_completed', {
        segment_id: final.segmentId,
        result_id: final.resultId,
        source_sequence: final.sourceSequence,
        outcome: result.outcome,
        suppression_reason: suppressionReason,
        overlap_intent: overlapIntent,
        overlap_confidence: finalOverlapAssessment?.confidence,
        overlap_reason: finalOverlapAssessment?.reason,
      }, 'live_voice_controller');
      return result;
    }
    renderTranscript('You', final.text, 'final');
    session.reporter.record('coordination_started', {
      segment_id: final.segmentId,
      result_id: final.resultId,
      finalize_request_id: final.finalizeRequestId,
      source_sequence: final.sourceSequence,
      capture_epoch: final.captureEpoch,
      overlap_intent: overlapIntent,
      overlap_confidence: finalOverlapAssessment?.confidence,
      overlap_reason: finalOverlapAssessment?.reason,
      interruption_dispatched: interruptionDispatched,
    }, 'live_voice_controller');
    dispatchLiveVoicePerfEvent({
      stage: 'coordination_started',
      timestamp: new Date().toISOString(),
      segmentId: final.segmentId,
      sourceSequence: final.sourceSequence,
      captureEpoch: final.captureEpoch,
    });
    try {
      const result = await liveSessionCoordinator.routeAcceptedFinal(final);
      session.reporter.record('coordination_completed', {
        segment_id: final.segmentId,
        result_id: final.resultId,
        source_sequence: final.sourceSequence,
        outcome: result.outcome,
        task_contract_id: result.taskContractId,
        task_contract_version: result.taskContractVersion,
        error_code: result.errorCode,
      }, 'live_voice_controller');
      dispatchLiveVoicePerfEvent({
        stage: 'coordination_completed',
        timestamp: new Date().toISOString(),
        segmentId: final.segmentId,
        sourceSequence: final.sourceSequence,
        outcome: result.outcome,
        errorCode: result.errorCode,
      });
      if (result.outcome === 'failed') setPanelStatus(card, 'error');
      else setPanelStatus(card, 'connected');
      return result;
    } catch (error) {
      const result = failedRoutingResult(final, 'live_coordination_failed');
      session.reporter.record('coordination_completed', {
        segment_id: final.segmentId,
        result_id: final.resultId,
        source_sequence: final.sourceSequence,
        outcome: result.outcome,
        error_code: result.errorCode,
        error: error instanceof Error ? error.message : String(error),
      }, 'live_voice_controller');
      setPanelStatus(card, 'error');
      return result;
    }
  } finally {
    emitOmnixEvent(LIVE_STT_SPECULATION_DELIVERY_SETTLED_EVENT, {
      chatSessionId: final.chatSessionId,
      segmentId: final.segmentId,
      sourceSequence: final.sourceSequence,
      text: final.text,
    });
    replayFinalizationBuffer(session, continuation);
  }
}

export function ignoredRoutingResult(final: AcceptedVoiceFinal): LiveFinalRoutingResult {
  const task = liveConversationStore.getState().coordination.taskContract;
  return { outcome: 'ignored', segmentId: final.segmentId, sourceSequence: final.sourceSequence, taskContractId: task.taskContractId, taskContractVersion: task.version };
}

export function failedRoutingResult(final: AcceptedVoiceFinal, errorCode: string): LiveFinalRoutingResult {
  const task = liveConversationStore.getState().coordination.taskContract;
  return { outcome: 'failed', segmentId: final.segmentId, sourceSequence: final.sourceSequence, taskContractId: task.taskContractId, taskContractVersion: task.version, errorCode };
}
