/** Authoritative transcript previews during a pause, and barge-in assessment while the assistant speaks. */
import { liveConversationStore } from './live-conversation-store';
import { type OverlapIntent, classifyOverlap, shouldConfirmInterruption } from './live-voice-overlap-classifier';
import { liveCallPresentationStore } from './live-call-presentation-store';
import { AUTHORITATIVE_PREVIEW_PAUSE_MS, LiveVoiceSession, controller, currentAssistantSpeechText, dispatchLiveVoicePerfEvent } from './live-voice-controller-state';
import { ASSISTANT_VOICE_INTERRUPT_EVENT, emitOmnixEvent, LIVE_STT_SPECULATION_CANDIDATE_EVENT, LIVE_STT_SPECULATION_PARTIAL_EVENT } from '../../../events/bus';

export function scheduleAuthoritativePreview(session: LiveVoiceSession): void {
  if (session.previewTimer || session.previewRequestId || session.finalRequested) return;
  session.previewTimer = setTimeout(() => {
    session.previewTimer = null;
    if (
      controller.activeSession !== session
      || session.finalRequested
      || session.pauseStartedAt === null
      || !session.speechDetected
    ) return;
    session.previewRequestId = session.client.requestAuthoritativePreview();
    dispatchLiveVoicePerfEvent({
      stage: 'stt_authoritative_preview_requested',
      turnId: session.perfTurnId,
      timestamp: new Date().toISOString(),
      requested: session.previewRequestId !== null,
      pauseElapsedMs: Math.round(performance.now() - session.pauseStartedAt),
    });
  }, AUTHORITATIVE_PREVIEW_PAUSE_MS);
}

export function handleAuthoritativePreview(
  card: HTMLElement,
  event: {
    segmentId: string;
    sequence: number;
    previewRequestId: string;
    snapshotEndSample: number;
    text: string;
    provider?: string;
    providerMetrics?: Record<string, number>;
  },
): void {
  const session = controller.activeSession;
  const text = event.text.trim();
  if (
    !session
    || session.card !== card
    || (!session.finalRequested && session.pauseStartedAt === null)
    || event.previewRequestId !== session.previewRequestId
    || !text
  ) return;
  session.authoritativePreviewText = text;
  session.speculationSegmentId = event.segmentId;
  session.speculationSourceSequence = event.sequence;
  const detail = {
    chatSessionId: liveConversationStore.getState().sessionId,
    segmentId: event.segmentId,
    sourceSequence: event.sequence,
    text,
  };
  emitOmnixEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, detail);
  emitOmnixEvent(LIVE_STT_SPECULATION_CANDIDATE_EVENT, {
    ...detail,
    probability: 1,
    modelTimeMs: event.snapshotEndSample * 1_000 / 16_000,
  });
  dispatchLiveVoicePerfEvent({
    stage: 'stt_authoritative_preview_received',
    turnId: session.perfTurnId,
    timestamp: new Date().toISOString(),
    provider: event.provider,
    segmentId: event.segmentId,
    sourceSequence: event.sequence,
    transcriptChars: text.length,
    providerMetrics: event.providerMetrics,
  });
}

export function clearAuthoritativePreview(session: LiveVoiceSession, resumedSpeech = false): void {
  if (resumedSpeech) {
    if (
      session.authoritativePreviewText
      && session.speculationSegmentId
      && session.speculationSourceSequence !== null
      && session.partialTranscript
    ) {
      emitOmnixEvent(LIVE_STT_SPECULATION_PARTIAL_EVENT, {
        chatSessionId: liveConversationStore.getState().sessionId,
        segmentId: session.speculationSegmentId,
        sourceSequence: session.speculationSourceSequence,
        text: session.partialTranscript,
      });
    }
    session.speculationSegmentId = null;
    session.speculationSourceSequence = null;
  }
  if (session.previewTimer) clearTimeout(session.previewTimer);
  session.previewTimer = null;
  session.previewRequestId = null;
  session.authoritativePreviewText = '';
}

export function assessOverlapCandidate(session: LiveVoiceSession): void {
  const assessment = classifyOverlap(session.partialTranscript, currentAssistantSpeechText());
  session.overlapIntent = assessment.intent;
  dispatchLiveVoicePerfEvent({
    stage: 'overlap_classified',
    timestamp: new Date().toISOString(),
    intent: assessment.intent,
    confidence: assessment.confidence,
    reason: assessment.reason,
    transcriptChars: session.partialTranscript.length,
  });
  if (!session.interruptionDispatched && shouldConfirmInterruption(assessment)) {
    session.interruptionDispatched = true;
    dispatchAssistantVoiceInterrupt(session.card, assessment.intent, assessment.confidence);
  }
}

export function dispatchAssistantVoiceInterrupt(
  card: HTMLElement,
  intent: OverlapIntent = 'interrupt',
  confidence = 1,
): void {
  emitOmnixEvent(ASSISTANT_VOICE_INTERRUPT_EVENT, {
      source: 'live-voice',
      status: liveCallPresentationStore.getState().captureStatus,
      timestamp: new Date().toISOString(),
      intent,
      confidence,
    });
}
