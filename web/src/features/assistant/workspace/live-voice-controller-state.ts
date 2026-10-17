/** Shared state of the live voice capture call: its session type, constants, the one active call, settings, and status and event helpers. */
import { readCurrentAssistantDiagnosticText } from './live-conversation-assistant-summary';
import { type LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { liveConversationStore } from './live-conversation-store';
import { type AuthoritySelection } from './live-stt-authority-controller';
import { type ConversationPace, type UserFloorState, reduceUserFloor } from './live-voice-floor-manager';
import { FinalizationAudioBuffer } from './live-voice-finalization-buffer';
import { LiveVoicePreSpeechBuffer } from './live-voice-pre-speech-buffer';
import { type OverlapIntent } from './live-voice-overlap-classifier';
import { StreamingSttWebSocketClient, type StreamingSttConnectionStatus } from './live-voice-websocket';
import { liveVoiceVisualScales, smoothLiveVoiceLevel } from './live-voice-level';
import { liveVoiceTranscriptStore, type LiveVoiceSpeaker } from './live-voice-transcript-store';
import { liveCallPresentationStore } from './live-call-presentation-store';
import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';

/** The one capture call of the page, or the start in flight; `startToken` invalidates stale starts. */
export const controller: {
  activeSession: LiveVoiceSession | null;
  pendingStart: PendingStart | null;
  startToken: number;
  initialized: boolean;
} = {
  activeSession: null,
  pendingStart: null,
  startToken: 0,
  initialized: false,
};

export type LiveVoiceWindow = Window & typeof globalThis & {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
};

export type LiveVoiceAudioPipeline = { node: AudioNode; cleanup: () => void };

export type LiveVoiceSession = {
  card: HTMLElement;
  stream: MediaStream;
  audioContext: AudioContext;
  source: MediaStreamAudioSourceNode;
  audioPipeline: LiveVoiceAudioPipeline;
  client: StreamingSttWebSocketClient;
  finalizationBuffer: FinalizationAudioBuffer;
  preSpeechBuffer: LiveVoicePreSpeechBuffer;
  reporter: LiveCallDiagnosticsReporter;
  sttAuthority: AuthoritySelection;
  speculationSegmentId: string | null;
  speculationSourceSequence: number | null;
  speechDetected: boolean;
  finalRequested: boolean;
  silenceTimer: ReturnType<typeof setTimeout> | null;
  previewTimer: ReturnType<typeof setTimeout> | null;
  previewRequestId: string | null;
  authoritativePreviewText: string;
  pauseStartedAt: number | null;
  finalResponseTimer: ReturnType<typeof setTimeout> | null;
  voiceLevel: number;
  speechFrameCount: number;
  perfTurnId: string | null;
  sttFinalRequestedAt: number | null;
  partialTranscript: string;
  partialTranscriptUpdatedAt: number;
  floorState: UserFloorState;
  overlapIntent: OverlapIntent | null;
  interruptionDispatched: boolean;
};

export type PendingStart = { card: HTMLElement; token: number };

export type ProviderEndpointCandidate = {
  provider?: string;
  segmentId: string;
  sequence: number;
  probability: number;
  modelTimeMs?: number;
};

export type EndpointCommitState = {
  authorityEnabled: boolean;
  probability: number;
  endpointThreshold: number;
  speechDetected: boolean;
  finalRequested: boolean;
  pausePending: boolean;
  pauseElapsedMs: number;
  transcriptStableMs?: number;
  semanticProbabilityDone?: number;
  transcriptWords?: number;
  correctionPending?: boolean;
};

export const ASSISTANT_SETTINGS_STORAGE_KEY = 'omnix.chatbot.assistantSettings';

export const DEFAULT_LIVE_VOICE_SENSITIVITY = 55;

export const DEFAULT_CONVERSATION_PACE: ConversationPace = 'balanced';

export const MIN_SPEECH_RMS_THRESHOLD = 0.012;

export const MAX_SPEECH_RMS_THRESHOLD = 0.06;

export const INTERRUPT_CONFIRMATION_FRAMES = 3;

export const PROVIDER_ENDPOINT_MIN_SILENCE_MS = 160;

export const FINAL_RESPONSE_TIMEOUT_MS = 8_000;

export const LIVE_SESSION_SELECTION_TIMEOUT_MS = 5_000;

export const FINALIZATION_BUFFER_MS = FINAL_RESPONSE_TIMEOUT_MS;

export const PRE_SPEECH_BUFFER_MS = 240;

export const STT_SEGMENT_TELEMETRY_INTERVAL_MS = 250;

// Start the private high-context preview as soon as a short, real pause is
// established. The result is never committed directly: resumed speech
// invalidates it, and only an exact authoritative final can promote it.
export const AUTHORITATIVE_PREVIEW_PAUSE_MS = 40;







export function isCardStartingOrActive(card: HTMLElement): boolean {
  return controller.activeSession?.card === card || controller.pendingStart?.card === card;
}

export type LiveVoiceSessionResources = Pick<LiveVoiceSession, 'card' | 'stream' | 'audioContext' | 'source' | 'client' | 'reporter' | 'sttAuthority'>;

/** A new call's session before its audio pipeline exists: buffers sized to the context's rate, turn state at rest. */
export function createLiveVoiceSessionShell({ card, stream, audioContext, source, client, reporter, sttAuthority }: LiveVoiceSessionResources) {
  return {
    card,
    stream,
    audioContext,
    source,
    client,
    finalizationBuffer: new FinalizationAudioBuffer(
      Math.max(1, Math.round(audioContext.sampleRate * FINALIZATION_BUFFER_MS / 1_000)),
    ),
    preSpeechBuffer: new LiveVoicePreSpeechBuffer(
      Math.max(1, Math.round(audioContext.sampleRate * PRE_SPEECH_BUFFER_MS / 1_000)),
    ),
    reporter,
    sttAuthority,
    speculationSegmentId: null,
    speculationSourceSequence: null,
    speechDetected: false,
    finalRequested: false,
    silenceTimer: null,
    previewTimer: null,
    previewRequestId: null,
    authoritativePreviewText: '',
    pauseStartedAt: null,
    finalResponseTimer: null,
    voiceLevel: 0,
    speechFrameCount: 0,
    perfTurnId: null,
    sttFinalRequestedAt: null,
    partialTranscript: '',
    partialTranscriptUpdatedAt: performance.now(),
    floorState: reduceUserFloor('idle', { type: 'listen' }),
    overlapIntent: null,
    interruptionDispatched: false,
  } satisfies Omit<LiveVoiceSession, 'audioPipeline'>;
}


export function updateVoiceVisualizer(session: LiveVoiceSession, rms: number): void {
  session.voiceLevel = smoothLiveVoiceLevel(session.voiceLevel, rms);
  const scales = liveVoiceVisualScales(session.voiceLevel);
  session.card.style.setProperty('--voice-level', session.voiceLevel.toFixed(3));
  session.card.style.setProperty('--voice-bar-scale', scales.barScale.toFixed(3));
  session.card.style.setProperty('--voice-ambient-scale', scales.ambientScale.toFixed(3));
  session.card.style.setProperty('--voice-core-scale', scales.coreScale.toFixed(3));
  session.card.style.setProperty('--voice-input-scale', scales.inputScale.toFixed(3));
  liveCallPresentationStore.update({ hearing: session.voiceLevel >= 0.14 });
}

export function readLiveTaskInstruction(): string | undefined {
  return liveCallPresentationStore.getState().taskInstruction.trim() || undefined;
}

export function liveVoiceSpeechThreshold(): number {
  const normalized = (readLiveVoiceSensitivity() - 1) / 99;
  return MAX_SPEECH_RMS_THRESHOLD - normalized * (MAX_SPEECH_RMS_THRESHOLD - MIN_SPEECH_RMS_THRESHOLD);
}

export function readSettings(): Record<string, unknown> {
  try {
    if (typeof window === 'undefined') return {};
    return JSON.parse(window.localStorage.getItem(ASSISTANT_SETTINGS_STORAGE_KEY) || '{}') as Record<string, unknown>;
  } catch {
    return {};
  }
}

export function readLiveVoiceSensitivity(): number {
  const value = readSettings().liveVoiceSensitivity;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed)
    ? Math.min(100, Math.max(1, Math.round(parsed)))
    : DEFAULT_LIVE_VOICE_SENSITIVITY;
}

export function readConversationPace(): ConversationPace {
  const value = readSettings().conversationPace;
  return value === 'quick' || value === 'reflective' ? value : DEFAULT_CONVERSATION_PACE;
}

export function liveVoiceAssistantIsSpeaking(): boolean {
  return liveConversationStore.getState().conversation.assistantTurn === 'speaking';
}

export function liveVoiceAssistantOwnsFloor(): boolean {
  const conversation = liveConversationStore.getState().conversation;
  return conversation.assistantTurn === 'speaking' && conversation.floorOwner === 'assistant';
}

export function currentAssistantSpeechText(): string {
  return readCurrentAssistantDiagnosticText();
}

// The card is rendered by Chat from the presentation store.
export function setPanelStatus(card: HTMLElement, status: StreamingSttConnectionStatus): void {
  liveCallPresentationStore.update({ captureStatus: status, captureActive: isCardStartingOrActive(card) });
  // Starting and stopping settle the active session after reporting the status.
  queueMicrotask(() => liveCallPresentationStore.update({ captureActive: isCardStartingOrActive(card) }));
}

export function resetVoiceVisualizer(card: HTMLElement): void {
  for (const property of ['--voice-level', '--voice-bar-scale', '--voice-ambient-scale', '--voice-core-scale', '--voice-input-scale']) {
    card.style.removeProperty(property);
  }
  liveCallPresentationStore.update({ hearing: false });
}

export function renderTranscript(speaker: LiveVoiceSpeaker, text: string, mode: 'draft' | 'final'): void {
  liveVoiceTranscriptStore.write(speaker, text, mode);
}

export function showLiveVoiceError(card: HTMLElement, message: string): void {
  renderTranscript('Omnix', message, 'final');
  setPanelStatus(card, 'error');
}


export function dispatchLiveVoicePerfEvent(detail: Record<string, unknown>): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, detail);
  console.info('[Omnix Voice Perf]', detail);
}

export function setText(element: Element | null, value: string): void {
  if (element && element.textContent !== value) element.textContent = value;
}

export function setDataAttribute(element: HTMLElement, key: string, value: string): void {
  if (element.dataset[key] !== value) element.dataset[key] = value;
}
