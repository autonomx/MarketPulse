/* eslint-disable @typescript-eslint/no-unused-vars -- baseline WP-9.x */
 
import { readEffectiveLiveConversationProfile, type DuplexMode, type LiveConversationProfile } from '../chat/liveConversationProfileClient';
import { assessAcousticBargeIn, calculatePcm16Rms } from './live-voice-barge-in-detector';
import { readLatestLiveVoiceCalibration, resolveCalibrationDuplex, type LiveVoiceCalibrationRecord } from './live-voice-calibration';
import { resolveLiveVoiceDeviceKey } from './live-voice-device-key';
import { createLiveVoiceMicrophoneTap, type LiveVoiceMicrophoneTap } from './live-voice-microphone-tap';
import { liveConversationStore } from './live-conversation-store';
import {
  BoundedWaveformReference,
  compareRecentWaveforms,
  pcm16ToFloat32Reference,
  resampleWaveform,
} from './live-voice-waveform-reference';
import { ASSISTANT_AUDIO_DUCK_EVENT, ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, ASSISTANT_VOICE_INTERRUPT_EVENT, ASSISTANT_VOICE_PERF_EVENT, ASSISTANT_VOICE_RELEASE_QUALITY_EVENT, CHARACTER_AVATAR_PCM_EVENT, emitOmnixEvent, LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, LIVE_VOICE_CALIBRATION_UPDATED_EVENT } from '../../../events/bus';

let liveVoiceDuplexGateInstalled = false;

const trackedStreams = new Set<MediaStream>();
const microphoneTaps = new Map<MediaStream, LiveVoiceMicrophoneTap>();
const CANDIDATE_TIMEOUT_MS = 1_500;
const DEFAULT_PLAYBACK_SAMPLE_RATE = 24_000;
const PLAYBACK_REFERENCE_SECONDS = 4;

let playbackReference = new BoundedWaveformReference(
  DEFAULT_PLAYBACK_SAMPLE_RATE * PLAYBACK_REFERENCE_SECONDS,
);
let playbackSampleRate = DEFAULT_PLAYBACK_SAMPLE_RATE;
let playbackRms = 0;
let playbackReferenceAt = 0;
let activeDeviceKey: string | null = null;
let deviceRefreshGeneration = 0;
let candidateTimer: ReturnType<typeof setTimeout> | null = null;
let candidateStartedAt: number | null = null;
let ducked = false;

export type ResolvedDuplexMode = 'half_duplex' | 'echo_aware';

export function resolveDuplexMode(
  mode: DuplexMode,
  echoAwareSupported = true,
  calibration: LiveVoiceCalibrationRecord | null = readLatestLiveVoiceCalibration(),
  currentDeviceKey: string | null = null,
): ResolvedDuplexMode {
  if (!echoAwareSupported || mode === 'half_duplex') return 'half_duplex';
  if (mode === 'echo_aware') return 'echo_aware';
  if (!currentDeviceKey) return 'half_duplex';
  return resolveCalibrationDuplex(calibration, currentDeviceKey).mode;
}

export function shouldMuteLiveMic(
  speaking: boolean,
  mode: ResolvedDuplexMode = resolvedRuntimeDuplexMode(),
): boolean {
  return speaking && mode === 'half_duplex';
}

export function initializeLiveVoiceDuplexGate(): () => void {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return () => undefined;
  if (liveVoiceDuplexGateInstalled) return () => undefined;
  liveVoiceDuplexGateInstalled = true;
  const mediaDevices = navigator.mediaDevices;
  if (!mediaDevices?.getUserMedia) return () => undefined;

  const originalGetUserMedia = mediaDevices.getUserMedia.bind(mediaDevices);
  const patchedGetUserMedia: typeof mediaDevices.getUserMedia = async (constraints) => {
    const stream = await originalGetUserMedia(constraints);
    const connection = liveConversationStore.getState().conversation.connection;
    if (constraints?.audio && connection !== 'disconnected') trackLiveVoiceStream(stream);
    return stream;
  };
  mediaDevices.getUserMedia = patchedGetUserMedia;

  const handlePlaybackState = (event: Event): void => {
    const speaking = Boolean((event as CustomEvent<{ speaking?: boolean }>).detail?.speaking);
    if (!speaking) {
      clearCandidate('playback-finished');
      playbackReference.clear();
      playbackRms = 0;
      playbackReferenceAt = 0;
    }
    applyDuplexGate();
  };
  const handleProfile = (event: Event): void => {
    const detail = (event as CustomEvent<LiveConversationProfile>).detail;
    liveConversationStore.dispatch({ type: 'profile', profile: detail ?? readEffectiveLiveConversationProfile() });
    clearCandidate('duplex-mode-changed');
    applyDuplexGate();
  };
  const handleCalibration = (event: Event): void => {
    const calibration = (event as CustomEvent<LiveVoiceCalibrationRecord>).detail
      ?? readLatestLiveVoiceCalibration();
    liveConversationStore.dispatch({ type: 'duplex', duplex: { calibration } });
    clearCandidate('calibration-updated');
    applyDuplexGate();
  };
  const handlePlaybackPcm = (event: Event): void => {
    const detail = (event as CustomEvent<{ samples?: Int16Array; sampleRate?: number }>).detail;
    if (!(detail?.samples instanceof Int16Array)) return;
    const sampleRate = typeof detail.sampleRate === 'number' && detail.sampleRate > 0
      ? Math.round(detail.sampleRate)
      : DEFAULT_PLAYBACK_SAMPLE_RATE;
    if (sampleRate !== playbackSampleRate) {
      playbackSampleRate = sampleRate;
      playbackReference = new BoundedWaveformReference(sampleRate * PLAYBACK_REFERENCE_SECONDS);
    }
    playbackRms = calculatePcm16Rms(detail.samples);
    playbackReference.append(pcm16ToFloat32Reference(detail.samples));
    playbackReferenceAt = performance.now();
  };
  const handleUserSpeech = (event: Event): void => {
    const runtime = liveConversationStore.getState();
    const assistantSpeaking = assistantAudioIsActive();
    if (!assistantSpeaking || resolvedRuntimeDuplexMode() !== 'echo_aware') return;
    const detail = (event as CustomEvent<{ rms?: number; assistantSpeaking?: boolean }>).detail;
    const eventMicrophoneRms = typeof detail?.rms === 'number' ? detail.rms : 0;
    const tap = firstMicrophoneTap();
    const microphoneFrame = tap?.read() ?? new Float32Array(0);
    const resampledMicrophone = tap
      ? resampleWaveform(microphoneFrame, tap.sampleRate, playbackSampleRate)
      : microphoneFrame;
    const waveform = compareRecentWaveforms(
      playbackReference.snapshot(),
      resampledMicrophone,
      playbackSampleRate,
    );
    const microphoneRms = waveform.alignedMicrophoneRms ?? eventMicrophoneRms;
    const alignedPlaybackRms = waveform.alignedPlaybackRms ?? playbackRms;
    const sensitivity = runtime.presencePolicy?.values.interruption_sensitivity ?? 0.7;
    const calibration = runtime.duplex.calibration;
    const assessment = assessAcousticBargeIn({
      assistantSpeaking: detail?.assistantSpeaking ?? assistantSpeaking,
      microphoneRms,
      playbackRms: alignedPlaybackRms,
      playbackReferenceAgeMs: Math.max(0, performance.now() - playbackReferenceAt),
      speechThreshold: adaptiveSpeechThreshold(calibration, sensitivity),
      waveformSimilarity: waveform.similarity,
      residualSpeechRatio: waveform.residualRatio,
      estimatedEchoGain: waveform.estimatedEchoGain,
      calibratedEchoGain: calibration?.echoGain ?? null,
      interruptionSensitivity: sensitivity,
    });
    dispatchPerf('barge_in_acoustic_candidate', {
      decision: assessment.decision,
      confidence: assessment.confidence,
      reason: assessment.reason,
      microphone_rms: assessment.microphoneRms,
      playback_rms: assessment.playbackRms,
      event_microphone_rms: eventMicrophoneRms,
      energy_ratio: assessment.energyRatio,
      waveform_similarity: assessment.waveformSimilarity,
      waveform_lag_ms: waveform.lagMs,
      waveform_samples: waveform.comparedSamples,
      estimated_echo_gain: assessment.estimatedEchoGain,
      residual_rms: waveform.residualRms,
      residual_speech_ratio: assessment.residualSpeechRatio,
      calibration_confidence: calibration?.confidence ?? 0,
      presence_policy_version: runtime.presencePolicy?.version ?? null,
      interruption_sensitivity: sensitivity,
    });
    if (assessment.decision === 'likely_echo' || assessment.decision === 'no_playback') {
      if (assessment.decision === 'likely_echo') recordReleaseQuality('playback_echo_submission', false);
      return;
    }
    candidateStartedAt = performance.now();
    setDucked(true, assessment.reason);
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'confirming' } });
    projectBargeIn('confirming');
    if (candidateTimer) window.clearTimeout(candidateTimer);
    candidateTimer = window.setTimeout(() => clearCandidate('candidate-timeout'), CANDIDATE_TIMEOUT_MS);
  };
  const handlePerf = (event: Event): void => {
    const detail = (event as CustomEvent<{ stage?: unknown; intent?: unknown; reason?: unknown }>).detail;
    if (detail?.stage !== 'overlap_classified' || !ducked) return;
    if (detail.intent === 'noise' || detail.intent === 'backchannel' || detail.intent === 'uncertain') {
      liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'rejected' } });
      clearCandidate(String(detail.reason ?? detail.intent ?? 'candidate-rejected'));
      return;
    }
    liveConversationStore.dispatch({ type: 'conversation', event: { type: 'barge_in', value: 'accepted' } });
    projectBargeIn('accepted');
    if (candidateStartedAt !== null) {
      dispatchPerf('barge_in_confirmed', { duck_to_cancel_ms: performance.now() - candidateStartedAt });
    }
  };
  const handleStop = () => clearCandidate('playback-stopped');
  const handleDeviceChange = () => { void refreshActiveDeviceKey(); };

  window.addEventListener(ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, handlePlaybackState);
  window.addEventListener(LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, handleProfile);
  window.addEventListener(LIVE_VOICE_CALIBRATION_UPDATED_EVENT, handleCalibration);
  window.addEventListener(CHARACTER_AVATAR_PCM_EVENT, handlePlaybackPcm);
  window.addEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
  window.addEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
  window.addEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleStop);
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);
  mediaDevices.addEventListener?.('devicechange', handleDeviceChange);
  applyDuplexGate();

  return () => {
    window.removeEventListener(ASSISTANT_AUDIO_PLAYBACK_STATE_EVENT, handlePlaybackState);
    window.removeEventListener(LIVE_CONVERSATION_PROFILE_CHANGED_EVENT, handleProfile);
    window.removeEventListener(LIVE_VOICE_CALIBRATION_UPDATED_EVENT, handleCalibration);
    window.removeEventListener(CHARACTER_AVATAR_PCM_EVENT, handlePlaybackPcm);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_USER_SPEECH_EVENT, handleUserSpeech);
    window.removeEventListener(ASSISTANT_VOICE_PERF_EVENT, handlePerf);
    window.removeEventListener(ASSISTANT_VOICE_INTERRUPT_EVENT, handleStop);
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleStop);
    mediaDevices.removeEventListener?.('devicechange', handleDeviceChange);
    mediaDevices.getUserMedia = originalGetUserMedia;
    deviceRefreshGeneration += 1;
    activeDeviceKey = null;
    playbackReference.clear();
    playbackRms = 0;
    playbackReferenceAt = 0;
    clearCandidate('gate-disposed');
    for (const tap of microphoneTaps.values()) void tap.close();
    microphoneTaps.clear();
    trackedStreams.clear();
    liveVoiceDuplexGateInstalled = false;
  };
}

/** Compatibility-only inspection helper. Runtime ownership is store-derived. */
export function assistantAudioIsActive(_root: ParentNode = document): boolean {
  const conversation = liveConversationStore.getState().conversation;
  return conversation.assistantTurn === 'speaking' || conversation.delivery === 'audio_started';
}

function trackLiveVoiceStream(stream: MediaStream): void {
  if (!stream.getAudioTracks().length) return;
  trackedStreams.add(stream);
  void createLiveVoiceMicrophoneTap(stream).then((tap) => {
    if (!tap || !trackedStreams.has(stream)) {
      if (tap) void tap.close();
      return;
    }
    microphoneTaps.set(stream, tap);
  });
  for (const track of stream.getAudioTracks()) {
    track.addEventListener('ended', () => {
      if (!stream.getAudioTracks().every((candidate) => candidate.readyState === 'ended')) return;
      trackedStreams.delete(stream);
      const tap = microphoneTaps.get(stream);
      microphoneTaps.delete(stream);
      if (tap) void tap.close();
      void refreshActiveDeviceKey();
    }, { once: true });
  }
  void refreshActiveDeviceKey();
  applyDuplexGate();
}

async function refreshActiveDeviceKey(): Promise<void> {
  const generation = ++deviceRefreshGeneration;
  const stream = Array.from(trackedStreams).find((candidate) => candidate.getAudioTracks().some((track) => track.readyState !== 'ended'));
  const next = stream ? await resolveLiveVoiceDeviceKey(stream) : null;
  if (generation !== deviceRefreshGeneration) return;
  activeDeviceKey = next;
  applyDuplexGate();
}

function applyDuplexGate(): void {
  const runtime = liveConversationStore.getState();
  const configuredMode = runtime.profile?.duplex_mode
    ?? readEffectiveLiveConversationProfile()?.duplex_mode
    ?? runtime.duplex.configuredMode;
  const calibration = runtime.duplex.calibration ?? readLatestLiveVoiceCalibration();
  const resolvedMode = resolveDuplexMode(configuredMode, true, calibration, activeDeviceKey);
  const resolution = configuredMode === 'automatic'
    ? activeDeviceKey
      ? resolveCalibrationDuplex(calibration, activeDeviceKey)
      : { mode: 'half_duplex' as const, confidence: calibration?.confidence ?? 0, reason: 'current_device_unavailable' }
    : { mode: resolvedMode, confidence: calibration?.confidence ?? 0, reason: 'explicit_user_selection' };
  liveConversationStore.dispatch({
    type: 'duplex',
    duplex: {
      configuredMode,
      resolvedMode,
      reason: resolution.reason,
      confidence: resolution.confidence,
      calibration,
    },
  });
  const assistantSpeaking = assistantAudioIsActive();
  const enabled = !shouldMuteLiveMic(assistantSpeaking, resolvedMode);
  for (const stream of Array.from(trackedStreams)) {
    const tracks = stream.getAudioTracks();
    if (!tracks.length || tracks.every((track) => track.readyState === 'ended')) {
      trackedStreams.delete(stream);
      continue;
    }
    for (const track of tracks) track.enabled = enabled;
  }
  document.querySelectorAll<HTMLElement>('.assistant-live-card').forEach((card) => {
    card.dataset.duplexMode = resolvedMode;
    card.dataset.duplexReason = resolution.reason;
    card.dataset.calibrationConfidence = String(resolution.confidence);
    card.dataset.duplexGate = assistantSpeaking
      ? resolvedMode === 'echo_aware' ? 'echo-aware-listening' : 'assistant-speaking'
      : 'listening';
  });
}

function resolvedRuntimeDuplexMode(): ResolvedDuplexMode {
  const runtime = liveConversationStore.getState();
  return resolveDuplexMode(
    runtime.profile?.duplex_mode ?? runtime.duplex.configuredMode,
    true,
    runtime.duplex.calibration,
    activeDeviceKey,
  );
}

function firstMicrophoneTap(): LiveVoiceMicrophoneTap | null {
  for (const stream of trackedStreams) {
    const tap = microphoneTaps.get(stream);
    if (tap) return tap;
  }
  return null;
}

function adaptiveSpeechThreshold(
  calibration: LiveVoiceCalibrationRecord | null,
  interruptionSensitivity: number,
): number {
  const sensitivity = Math.max(0, Math.min(1, interruptionSensitivity));
  if (!calibration) return Math.max(0.008, 0.016 - sensitivity * 0.006);
  const multiplier = 2.9 - sensitivity * 0.7;
  return Math.max(0.006, Math.min(0.08, calibration.noiseFloorRms * multiplier));
}

function setDucked(value: boolean, reason: string): void {
  if (ducked === value) return;
  ducked = value;
  liveConversationStore.dispatch({
    type: 'conversation',
    event: { type: 'barge_in', value: value ? 'ducking' : 'inactive' },
  });
  emitOmnixEvent(ASSISTANT_AUDIO_DUCK_EVENT, { ducked: value, gain: value ? 0.18 : 1, reason, timestamp: performance.now() });
  dispatchPerf(value ? 'barge_in_ducked' : 'barge_in_restored', {
    reason,
    ...(candidateStartedAt !== null ? { elapsed_ms: performance.now() - candidateStartedAt } : {}),
  });
}

function clearCandidate(reason: string): void {
  if (candidateTimer) window.clearTimeout(candidateTimer);
  candidateTimer = null;
  setDucked(false, reason);
  candidateStartedAt = null;
  projectBargeIn('inactive');
}

function projectBargeIn(value: string): void {
  document.querySelectorAll<HTMLElement>('.assistant-live-card').forEach((card) => {
    card.dataset.bargeIn = value;
  });
}

function recordReleaseQuality(qualityName: string, occurred: boolean): void {
  emitOmnixEvent(ASSISTANT_VOICE_RELEASE_QUALITY_EVENT, { qualityName, occurred });
}

function dispatchPerf(stage: string, details: Record<string, unknown>): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...details });
}
