/** Per-frame speech detection and turn finalization: when a pause ends the user's turn, the final transcript is requested. */
import { liveSttUsesAuthoritativeEou } from './live-stt-capability-state';
import { type ConversationPace, assessSemanticTurn, reduceUserFloor, semanticFinalizeDelay } from './live-voice-floor-manager';
import { calculateRms } from './live-voice-websocket';
import { FINAL_RESPONSE_TIMEOUT_MS, INTERRUPT_CONFIRMATION_FRAMES, LiveVoiceSession, PROVIDER_ENDPOINT_MIN_SILENCE_MS, ProviderEndpointCandidate, controller, dispatchLiveVoicePerfEvent, liveVoiceAssistantIsSpeaking, liveVoiceAssistantOwnsFloor, liveVoiceSpeechThreshold, readConversationPace, renderTranscript, setPanelStatus, updateVoiceVisualizer } from './live-voice-controller-state';
import { assessOverlapCandidate, clearAuthoritativePreview, scheduleAuthoritativePreview } from './live-voice-preview';
import { resetTurnState, stopLiveVoice } from './live-voice-session-lifecycle';
import { ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, emitOmnixEvent } from '../../../events/bus';

export function semanticFinalizationRemainingMs(
  text: string,
  pace: ConversationPace,
  pauseElapsedMs: number,
  transcriptStableMs: number = Number.POSITIVE_INFINITY,
): number {
  const targetDelayMs = semanticFinalizeDelay(text, pace);
  const pauseRemainingMs = targetDelayMs - Math.max(0, pauseElapsedMs);
  // When authoritative EOU is negotiated, the semantic timeout is only a
  // watchdog for a missed or delayed EOU. Nemotron partials can legitimately
  // revise late in the acoustic pause; restarting the whole 600 ms watchdog on
  // each revision stretched real traces to ~1 second. Keep the watchdog tied
  // to the microphone pause while the provider endpoint gate still protects
  // short intra-sentence pauses.
  if (liveSttUsesAuthoritativeEou()) return Math.max(0, pauseRemainingMs);
  const transcriptRemainingMs = Number.isFinite(transcriptStableMs)
    ? targetDelayMs - Math.max(0, transcriptStableMs)
    : 0;
  return Math.max(0, pauseRemainingMs, transcriptRemainingMs);
}

export function processAudioFrame(session: LiveVoiceSession, audio: Float32Array): void {
  const assistantOwnsFloor = liveVoiceAssistantOwnsFloor();
  const assistantSpeaking = liveVoiceAssistantIsSpeaking();
  const rms = calculateRms(audio);
  updateVoiceVisualizer(session, rms);
  if (session.finalRequested) {
    if (session.client.segmentedProtocolActive) {
      session.client.sendAudio(audio, session.audioContext.sampleRate);
      return;
    }
    const buffered = session.finalizationBuffer.push(audio);
    if (!buffered.accepted) handleFinalizationBufferOverflow(session, buffered.bufferedSamples, buffered.maxSamples);
    return;
  }
  const speechWasDetected = session.speechDetected;
  if (!speechWasDetected) session.preSpeechBuffer.push(audio);
  const speechStarted = rms >= liveVoiceSpeechThreshold();
  session.speechFrameCount = speechStarted ? session.speechFrameCount + 1 : 0;
  const confirmedSpeech = session.speechFrameCount >= INTERRUPT_CONFIRMATION_FRAMES;
  if (confirmedSpeech && !session.speechDetected) {
    emitOmnixEvent(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, {
      timestamp: new Date().toISOString(),
      rms,
      assistantSpeaking,
      assistantOwnsFloor,
    });
  }
  if (assistantOwnsFloor && confirmedSpeech && !session.speechDetected) {
    session.overlapIntent = 'uncertain';
    session.floorState = reduceUserFloor(session.floorState, {
      type: 'speech_confirmed',
      assistantSpeaking: true,
    });
    dispatchLiveVoicePerfEvent({
      stage: 'overlap_candidate',
      timestamp: new Date().toISOString(),
      rms,
    });
  }
  if (confirmedSpeech) {
    if (session.pauseStartedAt !== null) clearAuthoritativePreview(session, true);
    session.speechDetected = true;
    session.pauseStartedAt = null;
    if (!assistantOwnsFloor) {
      session.overlapIntent = null;
      session.floorState = reduceUserFloor(session.floorState, {
        type: 'speech_confirmed',
        assistantSpeaking: false,
      });
    }
    if (session.silenceTimer) {
      clearTimeout(session.silenceTimer);
      session.silenceTimer = null;
      session.floorState = reduceUserFloor(session.floorState, { type: 'resume' });
    }
  } else if (session.speechDetected && !session.silenceTimer) {
    session.pauseStartedAt = performance.now();
    session.floorState = reduceUserFloor(session.floorState, { type: 'pause' });
    scheduleSemanticFinalization(session);
    scheduleAuthoritativePreview(session);
  }
  if (!session.speechDetected) return;
  if (!speechWasDetected) {
    const preSpeechFrames = session.preSpeechBuffer.drain();
    const preSpeechSamples = preSpeechFrames.reduce((total, frame) => total + frame.length, 0);
    dispatchLiveVoicePerfEvent({
      stage: 'stt_pre_speech_buffer_flushed',
      timestamp: new Date().toISOString(),
      frames: preSpeechFrames.length,
      samples: preSpeechSamples,
      sampleRate: session.audioContext.sampleRate,
      assistantOwnsFloor,
    });
    preSpeechFrames.forEach((frame) => session.client.sendAudio(frame, session.audioContext.sampleRate));
    return;
  }
  session.client.sendAudio(audio, session.audioContext.sampleRate);
}

export function handleFinalizationBufferOverflow(session: LiveVoiceSession, bufferedSamples: number, maxSamples: number): void {
  dispatchLiveVoicePerfEvent({
    stage: 'stt_finalization_buffer_overflow',
    timestamp: new Date().toISOString(),
    bufferedSamples,
    maxSamples,
    sampleRate: session.audioContext.sampleRate,
  });
  stopLiveVoice(session.card, 'error');
  renderTranscript(
    'Omnix',
    'Live voice paused because transcription fell behind. Restart the call to continue; no buffered audio was silently discarded.',
    'final',
  );
}

export function scheduleSemanticFinalization(session: LiveVoiceSession): void {
  session.perfTurnId ??= `voice-turn:${Date.now()}`;
  session.floorState = reduceUserFloor(session.floorState, { type: 'completion_check' });
  armSemanticFinalizationTimer(session, 'semantic_turn_assessed');
}

export function rescheduleSemanticFinalization(session: LiveVoiceSession): void {
  armSemanticFinalizationTimer(session, 'semantic_turn_rescheduled');
}

export function armSemanticFinalizationTimer(
  session: LiveVoiceSession,
  stage: 'semantic_turn_assessed' | 'semantic_turn_rescheduled',
): void {
  const now = performance.now();
  const pace = readConversationPace();
  const assessment = assessSemanticTurn(session.partialTranscript, pace);
  const targetDelayMs = semanticFinalizeDelay(session.partialTranscript, pace);
  const pauseElapsedMs = session.pauseStartedAt === null
    ? 0
    : Math.max(0, now - session.pauseStartedAt);
  const transcriptStableMs = Math.max(0, now - session.partialTranscriptUpdatedAt);
  const remainingMs = semanticFinalizationRemainingMs(
    session.partialTranscript,
    pace,
    pauseElapsedMs,
    transcriptStableMs,
  );
  if (session.silenceTimer) clearTimeout(session.silenceTimer);
  dispatchLiveVoicePerfEvent({
    stage,
    turnId: session.perfTurnId,
    timestamp: new Date().toISOString(),
    pace,
    probabilityDone: assessment.probabilityDone,
    reason: assessment.reason,
    delayMs: targetDelayMs,
    pauseElapsedMs: Math.round(pauseElapsedMs),
    transcriptStableMs: Math.round(transcriptStableMs),
    remainingMs: Math.round(remainingMs),
    transcriptChars: session.partialTranscript.length,
  });
  session.silenceTimer = setTimeout(
    () => requestFinalTranscript(session, 'semantic_timeout'),
    remainingMs,
  );
}

export function shouldDeferSemanticTimeout(session: LiveVoiceSession): boolean {
  if (session.pauseStartedAt === null) return false;
  const now = performance.now();
  const pace = readConversationPace();
  const pauseElapsedMs = Math.max(0, now - session.pauseStartedAt);
  const transcriptStableMs = Math.max(0, now - session.partialTranscriptUpdatedAt);
  const remainingMs = semanticFinalizationRemainingMs(
    session.partialTranscript,
    pace,
    pauseElapsedMs,
    transcriptStableMs,
  );
  if (remainingMs <= 1) return false;
  const assessment = assessSemanticTurn(session.partialTranscript, pace);
  dispatchLiveVoicePerfEvent({
    stage: 'semantic_turn_commit_deferred',
    turnId: session.perfTurnId,
    timestamp: new Date().toISOString(),
    pace,
    probabilityDone: assessment.probabilityDone,
    reason: assessment.reason,
    pauseElapsedMs: Math.round(pauseElapsedMs),
    transcriptStableMs: Math.round(transcriptStableMs),
    remainingMs: Math.round(remainingMs),
    transcriptChars: session.partialTranscript.length,
  });
  armSemanticFinalizationTimer(session, 'semantic_turn_rescheduled');
  return true;
}

export function requestFinalTranscript(
  session: LiveVoiceSession,
  trigger: 'semantic_timeout' | 'provider_endpoint' = 'semantic_timeout',
  endpoint?: ProviderEndpointCandidate,
): void {
  if (controller.activeSession !== session || session.finalRequested) return;
  if (trigger === 'semantic_timeout' && shouldDeferSemanticTimeout(session)) return;
  if (session.silenceTimer) clearTimeout(session.silenceTimer);
  if (session.previewTimer) clearTimeout(session.previewTimer);
  session.silenceTimer = null;
  session.previewTimer = null;
  session.pauseStartedAt = null;
  if (session.floorState === 'overlap_candidate' && session.partialTranscript) assessOverlapCandidate(session);
  session.floorState = reduceUserFloor(session.floorState, { type: 'commit' });
  session.finalRequested = true;
  session.perfTurnId ??= `voice-turn:${Date.now()}`;
  session.sttFinalRequestedAt = performance.now();
  if (trigger === 'provider_endpoint' && endpoint) {
    session.reporter.record('stt_endpoint_committed', {
      provider: endpoint.provider,
      segment_id: endpoint.segmentId,
      source_sequence: endpoint.sequence,
      probability: endpoint.probability,
      model_time_ms: endpoint.modelTimeMs,
      endpoint_threshold: session.sttAuthority.endpointThreshold,
      endpoint_min_silence_ms: PROVIDER_ENDPOINT_MIN_SILENCE_MS,
    }, 'live_voice_controller');
    dispatchLiveVoicePerfEvent({
      stage: 'stt_endpoint_committed',
      timestamp: new Date().toISOString(),
      turnId: session.perfTurnId,
      provider: endpoint.provider,
      segmentId: endpoint.segmentId,
      sourceSequence: endpoint.sequence,
      probability: endpoint.probability,
      modelTimeMs: endpoint.modelTimeMs,
      endpointThreshold: session.sttAuthority.endpointThreshold,
      endpointMinSilenceMs: PROVIDER_ENDPOINT_MIN_SILENCE_MS,
    });
  }
  dispatchLiveVoicePerfEvent({
    stage: 'stt_final_requested',
    turnId: session.perfTurnId,
    timestamp: new Date().toISOString(),
    trigger,
  });
  session.client.sendFinal();
  session.finalResponseTimer = setTimeout(() => {
    if (controller.activeSession !== session) return;
    const continuation = session.finalizationBuffer.drain();
    resetTurnState(session);
    setPanelStatus(session.card, 'connected');
    replayFinalizationBuffer(session, continuation);
  }, FINAL_RESPONSE_TIMEOUT_MS);
}

export function replayFinalizationBuffer(session: LiveVoiceSession, frames: Float32Array[]): void {
  if (controller.activeSession !== session || frames.length === 0) return;
  const samples = frames.reduce((total, frame) => total + frame.length, 0);
  dispatchLiveVoicePerfEvent({
    stage: 'stt_finalization_buffer_replayed',
    timestamp: new Date().toISOString(),
    frames: frames.length,
    samples,
    sampleRate: session.audioContext.sampleRate,
  });
  frames.forEach((frame) => processAudioFrame(session, frame));
}
