/** The live voice capture controller: starts and stops a call's microphone stream to the STT service (WP-9.5 split it into the live-voice-* modules it imports). */
import { createLiveCallDiagnosticsReporter, createLiveCallTraceId, type LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { liveConversationStore } from './live-conversation-store';
import { type AuthoritySelection, resolveAuthoritySelection } from './live-stt-authority-controller';
import { StreamingSttWebSocketClient, getDefaultStreamingSttWebSocketUrl, type StreamingSttWebSocketCtor } from './live-voice-websocket';
import { createAssistantWorkspaceRuntimeConfig } from './runtime-config';
import LIVE_VOICE_CAPTURE_WORKLET_URL from './worklets/live-voice-capture.worklet?worker&url';
import { LIVE_VOICE_CAPTURE_WORKLET_NAME } from './worklets/names';
import type { SpeechLocation } from './stt-url';
import { pipelineFetch } from '../../../api/fetchPipeline';
import { liveCallPresentationStore } from './live-call-presentation-store';
import { createSttTelemetryHandlers, prepareLiveTaskContract, recordSttAuthoritySelection } from './live-voice-call-telemetry';
import { LIVE_SESSION_SELECTION_TIMEOUT_MS, LiveVoiceAudioPipeline, LiveVoiceSession, LiveVoiceWindow, controller, createLiveVoiceSessionShell, dispatchLiveVoicePerfEvent, isCardStartingOrActive, setPanelStatus, showLiveVoiceError } from './live-voice-controller-state';
import { handlePartialTranscript, handleProviderEndpointCandidate } from './live-voice-endpointing';
import { handleAcceptedFinal } from './live-voice-final-routing';
import { processAudioFrame } from './live-voice-finalization';
import { handleAuthoritativePreview } from './live-voice-preview';
import { cleanupSession, closePendingResources, handleExternalStop, stopLiveVoice } from './live-voice-session-lifecycle';
import { ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, ASSISTANT_LIVE_VOICE_CALL_START_EVENT, ASSISTANT_LIVE_VOICE_STOP_EVENT, emitOmnixEvent } from '../../../events/bus';

const liveVoiceWorkletContexts = new WeakSet<AudioContext>();

export async function resolveLiveVoiceSttSelection(
  configuredUrl: string | undefined,
  locationLike: SpeechLocation,
  fetchImpl: typeof fetch,
): Promise<AuthoritySelection> {
  if (configuredUrl?.trim()) {
    return resolveAuthoritySelection(configuredUrl, locationLike, fetchImpl);
  }
  return {
    websocketUrl: getDefaultStreamingSttWebSocketUrl(locationLike),
    authorityEnabled: false,
    mode: 'observational',
    endpointThreshold: 0.75,
    fallbackUsed: false,
    reasons: ['default_parakeet'],
  };
}

/** Whether the dedicated live voice controller owns the call (Chat defers to it). */
export function isLiveVoiceControllerInstalled(): boolean {
  return controller.initialized;
}

export function initializeLiveVoiceController(): () => void {
  if (controller.initialized || typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;
  controller.initialized = true;
  window.addEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleExternalStop);
  liveCallPresentationStore.update({ captureOwned: true });
  return () => {
    // Leaving Chat ends a running call, as the stop button would.
    handleExternalStop();
    window.removeEventListener(ASSISTANT_LIVE_VOICE_STOP_EVENT, handleExternalStop);
    liveCallPresentationStore.update({ captureOwned: false, captureStatus: 'idle', captureActive: false, hearing: false });
    controller.initialized = false;
  };
}

/** Starts capture for the call card (Chat's call controls); a running call is left alone. */
export function startLiveVoiceCall(card: HTMLElement): void {
  if (!isCardStartingOrActive(card)) void startLiveVoice(card);
}

/** Starts or ends capture for the call card. */
export function toggleLiveVoiceCall(card: HTMLElement): void {
  toggleLiveVoice(card);
}

function toggleLiveVoice(card: HTMLElement): void {
  if (isCardStartingOrActive(card)) stopLiveVoice(card, 'idle');
  else void startLiveVoice(card);
}

async function startLiveVoice(card: HTMLElement): Promise<void> {
  if (isCardStartingOrActive(card)) return;
  if (controller.activeSession) stopLiveVoice(controller.activeSession.card, 'idle');
  const token = ++controller.startToken;
  controller.pendingStart = { card, token };
  setPanelStatus(card, 'connecting');
  emitOmnixEvent(ASSISTANT_LIVE_VOICE_CALL_START_EVENT, {
    token,
    timestamp: new Date().toISOString(),
  });
  let audioContext: AudioContext | null = null;
  let sessionReporter: LiveCallDiagnosticsReporter | null = null;
  let stream: MediaStream | null = null;
  let source: MediaStreamAudioSourceNode | null = null;
  let audioPipeline: LiveVoiceAudioPipeline | null = null;
  try {
    const sessionId = await waitForLiveConversationSessionId();
    if (!isCurrentStart(card, token)) return;
    if (!sessionId) throw new Error('Select or create a chat session before starting Live voice.');
    const reporter = createLiveCallDiagnosticsReporter(createLiveCallTraceId(`${sessionId}:capture`));
    sessionReporter = reporter;
    await prepareLiveTaskContract(sessionId, reporter);
    const liveWindow = window as LiveVoiceWindow;
    const AudioContextCtor = liveWindow.AudioContext ?? liveWindow.webkitAudioContext;
    const WebSocketCtor = liveWindow.WebSocket as unknown as StreamingSttWebSocketCtor | undefined;
    if (!AudioContextCtor || !WebSocketCtor) throw new Error('Live voice requires browser AudioContext and WebSocket support.');
    const runtimeConfig = createAssistantWorkspaceRuntimeConfig();
    const sttAuthority = await resolveLiveVoiceSttSelection(
      runtimeConfig.sttServiceUrl,
      window.location,
      pipelineFetch,
    );
    recordSttAuthoritySelection(reporter, runtimeConfig.sttServiceUrl, sttAuthority);
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Live voice requires browser microphone access.');
    audioContext = new AudioContextCtor({ latencyHint: 'interactive' });
    await ensureAudioContextRunning(audioContext);
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    if (!isCurrentStart(card, token)) {
      closePendingResources(stream, audioContext, source, audioPipeline);
      return;
    }
    source = audioContext.createMediaStreamSource(stream);
    const client = new StreamingSttWebSocketClient({
      url: sttAuthority.websocketUrl,
      webSocketCtor: WebSocketCtor,
      chatSessionId: sessionId,
      ...createSttTelemetryHandlers(reporter),
      onEndpointCandidate: (event) => handleProviderEndpointCandidate(card, event),
      onPreviewTranscript: (event) => handleAuthoritativePreview(card, event),
      onStatusChange: (status) => {
        if (controller.activeSession?.card === card || controller.pendingStart?.card === card) setPanelStatus(card, status);
      },
      onPartialTranscript: (text) => handlePartialTranscript(card, text),
      onAcceptedFinal: (final) => handleAcceptedFinal(card, final),
      onFinalRejected: (reason, identity) => {
        reporter.record('stt_final_rejected', {
          reason,
          segment_id: identity.segmentId,
          result_id: identity.resultId,
          finalize_request_id: identity.finalizeRequestId,
          source_sequence: identity.sourceSequence,
          capture_epoch: identity.captureEpoch,
        }, 'live_voice_controller');
        dispatchLiveVoicePerfEvent({ stage: 'stt_final_rejected', timestamp: new Date().toISOString(), reason, segmentId: identity.segmentId, sourceSequence: identity.sourceSequence });
        setPanelStatus(card, 'error');
      },
      onError: (message) => showLiveVoiceError(card, message),
    });
    const shell = createLiveVoiceSessionShell({ card, stream, audioContext, source, client, reporter, sttAuthority });
    audioPipeline = await createLiveVoiceAudioPipeline(audioContext, (audio) => {
      const session = controller.activeSession;
      if (session?.card === card) processAudioFrame(session, audio);
    });
    source.connect(audioPipeline.node);
    await ensureAudioContextRunning(audioContext);
    const session: LiveVoiceSession = { ...shell, audioPipeline };
    controller.activeSession = session;
    controller.pendingStart = null;
    await client.connect();
    if (controller.activeSession !== session || token !== controller.startToken) {
      cleanupSession(session);
      return;
    }
    setPanelStatus(card, 'connected');
    emitOmnixEvent(ASSISTANT_LIVE_VOICE_CALL_CONNECTED_EVENT, {
      token,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    if (controller.activeSession?.card === card) {
      const session = controller.activeSession;
      controller.activeSession = null;
      cleanupSession(session);
    } else closePendingResources(stream, audioContext, source, audioPipeline);
    if (controller.pendingStart?.token === token) controller.pendingStart = null;
    if (sessionReporter) {
      void sessionReporter.close('live_capture_start_failed', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
    showLiveVoiceError(card, error instanceof Error ? error.message : 'Could not start live voice.');
  }
}

export function waitForLiveConversationSessionId(
  timeoutMs = LIVE_SESSION_SELECTION_TIMEOUT_MS,
): Promise<string | null> {
  const selected = liveConversationStore.getState().sessionId;
  if (selected) return Promise.resolve(selected);
  return new Promise((resolve) => {
    let unsubscribe = () => undefined;
    let timerId: ReturnType<typeof window.setTimeout> | null = null;
    let settled = false;
    const finish = (sessionId: string | null) => {
      if (settled) return;
      settled = true;
      unsubscribe();
      if (timerId !== null) window.clearTimeout(timerId);
      resolve(sessionId);
    };
    unsubscribe = liveConversationStore.subscribe(() => {
      const sessionId = liveConversationStore.getState().sessionId;
      if (sessionId) finish(sessionId);
    });
    const sessionId = liveConversationStore.getState().sessionId;
    if (sessionId) {
      finish(sessionId);
      return;
    }
    timerId = window.setTimeout(() => finish(null), Math.max(0, timeoutMs));
  });
}

function isCurrentStart(card: HTMLElement, token: number): boolean {
  return controller.pendingStart?.card === card && controller.pendingStart.token === token && controller.startToken === token;
}

async function ensureAudioContextRunning(audioContext: AudioContext): Promise<void> {
  if (audioContext.state === 'closed') throw new Error('Microphone audio processing closed before capture started.');
  if (audioContext.state !== 'running') await audioContext.resume();
  if (audioContext.state !== 'running') throw new Error('Microphone audio processing is suspended. Allow audio playback and try again.');
}

async function createLiveVoiceAudioPipeline(
  audioContext: AudioContext,
  onAudioFrame: (audio: Float32Array) => void,
): Promise<LiveVoiceAudioPipeline> {
  if ('audioWorklet' in audioContext && typeof AudioWorkletNode !== 'undefined') {
    try {
      await ensureLiveVoiceWorklet(audioContext);
      const node = new AudioWorkletNode(audioContext, LIVE_VOICE_CAPTURE_WORKLET_NAME);
      const silentOutput = audioContext.createGain();
      silentOutput.gain.value = 0;
      node.port.onmessage = (event: MessageEvent<Float32Array>) => onAudioFrame(new Float32Array(event.data));
      node.connect(silentOutput);
      silentOutput.connect(audioContext.destination);
      return {
        node,
        cleanup: () => {
          node.port.onmessage = null;
          node.disconnect();
          silentOutput.disconnect();
        },
      };
    } catch {
      // Fall through to ScriptProcessor on browsers that reject dynamic worklets.
    }
  }
  const processor = audioContext.createScriptProcessor(1024, 1, 1);
  const silentOutput = audioContext.createGain();
  silentOutput.gain.value = 0;
  processor.onaudioprocess = (event) => onAudioFrame(new Float32Array(event.inputBuffer.getChannelData(0)));
  processor.connect(silentOutput);
  silentOutput.connect(audioContext.destination);
  return {
    node: processor,
    cleanup: () => {
      processor.onaudioprocess = null;
      processor.disconnect();
      silentOutput.disconnect();
    },
  };
}

async function ensureLiveVoiceWorklet(audioContext: AudioContext): Promise<void> {
  if (liveVoiceWorkletContexts.has(audioContext)) return;
  await audioContext.audioWorklet.addModule(LIVE_VOICE_CAPTURE_WORKLET_URL);
  liveVoiceWorkletContexts.add(audioContext);
}

export { LiveSttSegmentTelemetryGate, shouldCommitProviderEndpoint } from './live-voice-endpointing';
export { semanticFinalizationRemainingMs } from './live-voice-finalization';
export { liveVoiceSpeechThreshold, liveVoiceAssistantOwnsFloor } from './live-voice-controller-state';
