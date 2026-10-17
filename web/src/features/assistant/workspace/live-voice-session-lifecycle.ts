/** Ending a capture call and resetting its turn state. */
import { liveConversationStore } from './live-conversation-store';
import { reduceUserFloor } from './live-voice-floor-manager';
import { type StreamingSttConnectionStatus } from './live-voice-websocket';
import { LiveVoiceAudioPipeline, LiveVoiceSession, controller, resetVoiceVisualizer, setPanelStatus } from './live-voice-controller-state';
import { clearAuthoritativePreview } from './live-voice-preview';

export function handleExternalStop(): void {
  if (controller.activeSession) stopLiveVoice(controller.activeSession.card, 'idle');
  else if (controller.pendingStart) {
    controller.pendingStart = null;
    controller.startToken += 1;
  }
}

export function resetTurnState(session: LiveVoiceSession): void {
  if (session.silenceTimer) clearTimeout(session.silenceTimer);
  if (session.finalResponseTimer) clearTimeout(session.finalResponseTimer);
  session.silenceTimer = null;
  session.pauseStartedAt = null;
  session.finalResponseTimer = null;
  session.speechDetected = false;
  session.speechFrameCount = 0;
  session.preSpeechBuffer.clear();
  clearAuthoritativePreview(session);
  session.finalRequested = false;
  session.perfTurnId = null;
  session.sttFinalRequestedAt = null;
  session.partialTranscript = '';
  session.partialTranscriptUpdatedAt = performance.now();
  session.speculationSegmentId = null;
  session.speculationSourceSequence = null;
  session.overlapIntent = null;
  session.interruptionDispatched = false;
  session.floorState = reduceUserFloor(session.floorState, { type: 'reset' });
  session.floorState = reduceUserFloor(session.floorState, { type: 'listen' });
}

export function stopLiveVoice(card: HTMLElement, nextStatus: StreamingSttConnectionStatus): void {
  controller.startToken += 1;
  if (controller.pendingStart?.card === card) controller.pendingStart = null;
  const session = controller.activeSession;
  if (session?.card === card) {
    controller.activeSession = null;
    cleanupSession(session);
  }
  setPanelStatus(card, nextStatus);
  resetVoiceVisualizer(card);
}

export function cleanupSession(session: LiveVoiceSession): void {
  session.finalizationBuffer.clear();
  resetTurnState(session);
  session.audioPipeline.cleanup();
  session.source.disconnect();
  session.stream.getTracks().forEach((track) => track.stop());
  session.client.disconnect();
  void session.audioContext.close().catch(() => undefined);
  void session.reporter.close('live_capture_session_closed', {
    session_id: liveConversationStore.getState().sessionId,
  });
}

export function closePendingResources(
  stream: MediaStream | null,
  audioContext: AudioContext | null,
  source: MediaStreamAudioSourceNode | null,
  audioPipeline: LiveVoiceAudioPipeline | null,
): void {
  audioPipeline?.cleanup();
  source?.disconnect();
  stream?.getTracks().forEach((track) => track.stop());
  if (audioContext) void audioContext.close().catch(() => undefined);
}
