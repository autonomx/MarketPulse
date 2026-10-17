import { ASSISTANT_VOICE_PERF_EVENT, emitOmnixEvent } from '../../../events/bus';
import type { AcceptedVoiceFinal, LiveFinalRoutingResult, LiveSttProtocol } from './live-accepted-final';
import { streamingSttUrl, type SpeechLocation } from './stt-url';
import { streamingSttMessageSchema } from './live-voice-messages';
import { parseJson } from '../../../api/schemas/streams';
import { startTicker } from '../../../shared/timers';

export type StreamingSttReady = {
  type: 'ready';
  protocol?: string;
  provider?: string;
  connectionId?: string;
  sampleRate?: number;
  frameSamples?: number;
  encoding?: string;
  capabilities?: string[];
  configVersion?: string;
  maxSegmentAudioMs?: number;
  language?: string;
};

export type StreamingSttWord = {
  type: 'word';
  provider?: string;
  segmentId: string;
  sequence: number;
  text: string;
  startMs?: number;
  endMs?: number;
};

export type StreamingSttEndpointScore = {
  type: 'endpoint_score';
  provider?: string;
  segmentId?: string;
  sequence?: number;
  probability: number;
  modelTimeMs?: number;
  signal?: string;
};

export type StreamingSttEndpointCandidate = {
  type: 'endpoint_candidate';
  provider?: string;
  segmentId: string;
  sequence: number;
  probability: number;
  modelTimeMs?: number;
};

export type StreamingSttPreviewResult = {
  type: 'preview_result';
  provider?: string;
  segmentId: string;
  sequence: number;
  previewRequestId: string;
  snapshotEndSample: number;
  text: string;
  providerMetrics?: Record<string, number>;
};

export type StreamingSttProviderEvent = {
  type: 'flush_started' | 'flush_completed' | 'flush_cancelled';
  provider?: string;
  attemptId?: string;
  wall_ms?: number;
  model_ms?: number;
  realtime_factor?: number;
};

export type StreamingSttMessage =
  | StreamingSttReady
  | { type: 'session_ready'; sessionId: string; provider?: string; results?: SegmentedSttResult[] }
  | { type: 'text'; text: string; segmentId?: string; sequence?: number }
  | { type: 'partial'; text: string; segmentId: string; sequence: number }
  | StreamingSttWord
  | StreamingSttEndpointScore
  | StreamingSttEndpointCandidate
  | StreamingSttPreviewResult
  | StreamingSttProviderEvent
  | LegacySttResult
  | { type: 'audio_buffered'; segmentId: string; sequence: number; acceptedThroughSample: number }
  | { type: 'finalize_queued'; segmentId: string; sequence: number; queuedSegments?: number }
  | SegmentedSttResult
  | { type: 'segment_error'; segmentId?: string; sequence?: number; retryable?: boolean; errorCode?: string; error?: string }
  | { type: 'error'; errorCode?: string; retryable?: boolean; error?: string };

export type SegmentedSttResult = {
  type: 'result_available';
  sessionId: string;
  captureEpoch: string;
  segmentId: string;
  sequence: number;
  resultId: string;
  finalizeRequestId: string;
  startSample: number;
  endSample: number;
  text: string;
  acceptedThroughSample?: number;
  provider?: string;
  providerMetrics?: Record<string, number>;
};

export type LegacySttResult = {
  type: 'done';
  sessionId: string;
  captureEpoch: string;
  segmentId: string;
  sequence: number;
  resultId: string;
  finalizeRequestId: string;
  startSample: number;
  endSample: number;
  text: string;
};

export type StreamingSttNegotiation = {
  provider: string;
  protocol: LiveSttProtocol;
  sampleRate: number;
  frameSamples: number;
  encoding: 'pcm16le';
  capabilities: readonly string[];
  configVersion: string;
  language?: string;
};

export type StreamingSttSocketLike = {
  readyState: number;
  onopen: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: (() => void) | null;
  send(data: string | ArrayBuffer): void;
  close(): void;
};

export type StreamingSttWebSocketCtor = {
  readonly OPEN: number;
  new (url: string): StreamingSttSocketLike;
};

export type StreamingSttWebSocketClientOptions = {
  url: string;
  webSocketCtor: StreamingSttWebSocketCtor;
  chatSessionId?: string;
  connectTimeoutMs?: number;
  reconnectDelayMs?: number;
  maxPendingChunks?: number;
  hardSegmentMs?: number;
  overlapMs?: number;
  maxFinalResultAgeMs?: number;
  onPartialTranscript?: (text: string) => void;
  onWord?: (event: StreamingSttWord) => void;
  onAcceptedFinal?: (final: AcceptedVoiceFinal) => Promise<LiveFinalRoutingResult>;
  onFinalRejected?: (reason: string, identity: Partial<AcceptedVoiceFinal>) => void;
  onStatusChange?: (status: StreamingSttConnectionStatus) => void;
  onError?: (message: string) => void;
  onSegmentStateChange?: (state: StreamingSttSegmentState) => void;
  onNegotiated?: (negotiation: StreamingSttNegotiation) => void;
  onEndpointScore?: (event: StreamingSttEndpointScore) => void;
  onEndpointCandidate?: (event: StreamingSttEndpointCandidate) => void;
  onPreviewTranscript?: (event: StreamingSttPreviewResult) => void;
  onProviderEvent?: (event: StreamingSttProviderEvent) => void;
};

export type StreamingSttConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error';

export type StreamingSttSegmentState = {
  protocol: LiveSttProtocol;
  sessionId: string;
  captureEpoch: string;
  activeSequence: number;
  pendingSegments: number;
  queuedSegments: number;
  absoluteSample: number;
  negotiation: StreamingSttNegotiation | null;
};

type PendingAudioFrame = {
  audio: Float32Array;
  sourceSampleRate: number;
};

type PendingSegmentFrame = {
  sampleStart: number;
  sampleEnd: number;
  pcm: Uint8Array;
};

type PendingSegment = {
  segmentId: string;
  sequence: number;
  captureStartSample: number;
  primaryStartSample: number;
  frames: PendingSegmentFrame[];
  acceptedThroughSample: number;
  finalized: boolean;
  finalizeQueued: boolean;
  finalizeRequestId: string | null;
  finalizeRequestedAtMs: number | null;
  endSample: number | null;
};

type LegacyFinalizeIdentity = {
  segmentId: string;
  sequence: number;
  finalizeRequestId: string;
  startSample: number;
  endSample: number;
  requestedAtMs: number;
};

const DEFAULT_STT_SAMPLE_RATE = 16_000;
const DEFAULT_STT_FRAME_SAMPLES = 320;
const DEFAULT_STT_PROVIDER = 'parakeet';
const DEFAULT_STT_CONFIG_VERSION = 'legacy-default';
const DEFAULT_CONNECT_TIMEOUT_MS = 5_000;
const DEFAULT_RECONNECT_DELAY_MS = 300;
const DEFAULT_MAX_PENDING_CHUNKS = 250;
const DEFAULT_HARD_SEGMENT_MS = 10_000;
const DEFAULT_OVERLAP_MS = 300;
const DEFAULT_MAX_FINAL_RESULT_AGE_MS = 8_000;
const SEGMENTED_PROTOCOL = 'segmented-v1';
const CAP_CLIENT_AUDIO_REPLAY = 'client_audio_replay';
const CAP_AUTHORITATIVE_PREVIEW = 'authoritative_preview';
// Audio as binary frames: uint32 LE header length, JSON header, PCM16 bytes.
const CAP_BINARY_AUDIO_FRAMES = 'binary_audio_frames';

export function getDefaultStreamingSttWebSocketUrl(
  locationLike: SpeechLocation = globalThis.location,
  sttServiceUrl?: string,
): string {
  return streamingSttUrl(sttServiceUrl?.trim() || '/api/stt', locationLike).toString();
}

function dispatchSttDiagnostic(stage: string, detail: Record<string, unknown>): void {
  emitOmnixEvent(ASSISTANT_VOICE_PERF_EVENT, { stage, timestamp: new Date().toISOString(), ...detail });
}

export class StreamingFloat32Resampler {
  private sourceSampleRate = 0;
  private targetSampleRate = 0;
  private step = 1;
  private carry = new Float32Array();
  private position = 0;

  transform(audio: Float32Array, sourceSampleRate: number, targetSampleRate: number): Float32Array {
    if (sourceSampleRate <= 0 || targetSampleRate <= 0) throw new Error('Audio sample rates must be positive.');
    if (sourceSampleRate === targetSampleRate) {
      this.reset();
      return new Float32Array(audio);
    }
    if (this.sourceSampleRate !== sourceSampleRate || this.targetSampleRate !== targetSampleRate) {
      this.sourceSampleRate = sourceSampleRate;
      this.targetSampleRate = targetSampleRate;
      this.step = sourceSampleRate / targetSampleRate;
      this.carry = new Float32Array();
      this.position = 0;
    }
    const combined = concatFloat32([this.carry, audio]) as Float32Array<ArrayBuffer>;
    if (combined.length < 2) {
      this.carry = combined;
      return new Float32Array();
    }
    const output: number[] = [];
    while (this.position < combined.length - 1) {
      const index0 = Math.floor(this.position);
      const index1 = Math.min(index0 + 1, combined.length - 1);
      const fraction = this.position - index0;
      output.push(combined[index0] * (1 - fraction) + combined[index1] * fraction);
      this.position += this.step;
    }
    const keepIndex = Math.min(Math.floor(this.position), combined.length - 1);
    this.carry = combined.slice(keepIndex);
    this.position -= keepIndex;
    return Float32Array.from(output);
  }

  reset(): void {
    this.sourceSampleRate = 0;
    this.targetSampleRate = 0;
    this.step = 1;
    this.carry = new Float32Array();
    this.position = 0;
  }
}

export function resampleFloat32(
  audio: Float32Array,
  sourceSampleRate: number,
  targetSampleRate: number,
): Float32Array {
  return new StreamingFloat32Resampler().transform(audio, sourceSampleRate, targetSampleRate);
}

export function downsampleFloat32To16Khz(
  audio: Float32Array,
  sourceSampleRate: number,
  targetSampleRate: number = DEFAULT_STT_SAMPLE_RATE,
): Float32Array {
  return resampleFloat32(audio, sourceSampleRate, targetSampleRate);
}

export function encodePcm16(audio: Float32Array): Uint8Array {
  const int16 = new Int16Array(audio.length);
  for (let i = 0; i < audio.length; i += 1) int16[i] = Math.max(-1, Math.min(1, audio[i])) * 32767;
  return new Uint8Array(int16.buffer);
}

export function encodePcm16Base64(audio: Float32Array): string {
  return bytesToBase64(encodePcm16(audio));
}

/** One binary audio message: uint32 LE header length, UTF-8 JSON header, PCM16 bytes. */
export function encodeBinaryAudioFrame(header: Record<string, unknown>, pcm: Uint8Array): ArrayBuffer {
  const encodedHeader = new TextEncoder().encode(JSON.stringify(header));
  const frame = new Uint8Array(4 + encodedHeader.length + pcm.length);
  new DataView(frame.buffer).setUint32(0, encodedHeader.length, true);
  frame.set(encodedHeader, 4);
  frame.set(pcm, 4 + encodedHeader.length);
  return frame.buffer;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  const chunkSize = 8192;
  for (let i = 0; i < bytes.length; i += chunkSize) binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  return globalThis.btoa(binary);
}

export function calculateRms(audio: Float32Array): number {
  if (!audio.length) return 0;
  let sum = 0;
  for (const sample of audio) sum += sample * sample;
  return Math.sqrt(sum / audio.length);
}

export function deduplicateSegmentBoundary(previous: string, current: string): string {
  const previousTokens = previous.trim().split(/\s+/).filter(Boolean);
  const currentTokens = current.trim().split(/\s+/).filter(Boolean);
  if (!currentTokens.length || !previousTokens.length) return current.trim();
  const maximum = Math.min(16, previousTokens.length, currentTokens.length);
  let overlap = 0;
  for (let count = maximum; count > 0; count -= 1) {
    const left = previousTokens.slice(-count).map(normalizeBoundaryToken);
    const right = currentTokens.slice(0, count).map(normalizeBoundaryToken);
    if (left.every((token, index) => token === right[index])) {
      overlap = count;
      break;
    }
  }
  return currentTokens.slice(overlap).join(' ');
}

function normalizeBoundaryToken(token: string): string {
  return token.toLocaleLowerCase().replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
}

function createRuntimeId(prefix: string): string {
  const cryptoWithUuid = globalThis.crypto as Crypto & { randomUUID?: () => string };
  const suffix = cryptoWithUuid?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function concatFloat32(frames: readonly Float32Array[]): Float32Array {
  const length = frames.reduce((total, frame) => total + frame.length, 0);
  const result = new Float32Array(length);
  let offset = 0;
  for (const frame of frames) {
    result.set(frame, offset);
    offset += frame.length;
  }
  return result;
}

function normalizeReady(message: StreamingSttReady): StreamingSttNegotiation {
  const protocol: LiveSttProtocol = message.protocol === SEGMENTED_PROTOCOL ? 'segmented-v1' : 'legacy';
  const sampleRate = message.sampleRate ?? DEFAULT_STT_SAMPLE_RATE;
  const frameSamples = message.frameSamples ?? DEFAULT_STT_FRAME_SAMPLES;
  const encoding = message.encoding ?? 'pcm16le';
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) throw new Error('Live STT returned an invalid sample rate.');
  if (!Number.isFinite(frameSamples) || frameSamples <= 0) throw new Error('Live STT returned an invalid frame size.');
  if (encoding !== 'pcm16le') throw new Error(`Live STT returned unsupported encoding: ${encoding}`);
  return {
    provider: message.provider?.trim() || DEFAULT_STT_PROVIDER,
    protocol,
    sampleRate: Math.round(sampleRate),
    frameSamples: Math.round(frameSamples),
    encoding,
    capabilities: [...new Set(message.capabilities ?? [])].sort(),
    configVersion: message.configVersion?.trim() || DEFAULT_STT_CONFIG_VERSION,
    language: message.language?.trim() || undefined,
  };
}

function negotiationsMatch(left: StreamingSttNegotiation, right: StreamingSttNegotiation): boolean {
  return left.provider === right.provider
    && left.protocol === right.protocol
    && left.sampleRate === right.sampleRate
    && left.frameSamples === right.frameSamples
    && left.encoding === right.encoding
    && left.configVersion === right.configVersion
    && left.language === right.language
    && left.capabilities.length === right.capabilities.length
    && left.capabilities.every((capability, index) => capability === right.capabilities[index]);
}

export class StreamingSttWebSocketClient {
  private socket: StreamingSttSocketLike | null = null;
  private connecting = false;
  private autoReconnect = false;
  private serverReady = false;
  private awaitingSessionReady = false;
  private protocol: LiveSttProtocol = 'legacy';
  private negotiation: StreamingSttNegotiation | null = null;
  private pendingAudio: PendingAudioFrame[] = [];
  private pendingFinal = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly sessionId = createRuntimeId('stt-session');
  private readonly captureEpoch = createRuntimeId('capture');
  private readonly resampler = new StreamingFloat32Resampler();
  private preparedAudioCarry = new Float32Array();
  private preparedAudioFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private captureClosed = false;
  private absoluteSample = 0;
  private nextSequence = 0;
  private activeSegment: PendingSegment | null = null;
  private readonly pendingSegments = new Map<string, PendingSegment>();
  private readonly pendingResults = new Map<number, SegmentedSttResult>();
  private readonly failedSequences = new Set<number>();
  private readonly committedResultIds = new Set<string>();
  private nextCommitSequence = 0;
  private previousCommittedText = '';
  private recentOverlapFrames: Float32Array[] = [];
  private recentOverlapSamples = 0;
  private queuedSegments = 0;
  private drainingResults = false;
  private legacySequence = 0;
  private legacyStartSample = 0;
  private legacyFinalize: LegacyFinalizeIdentity | null = null;

  constructor(private readonly options: StreamingSttWebSocketClientOptions) {}

  get segmentedProtocolActive(): boolean {
    return this.protocol === 'segmented-v1' && this.serverReady && !this.awaitingSessionReady;
  }

  get segmentState(): StreamingSttSegmentState {
    return {
      protocol: this.protocol,
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      activeSequence: this.activeSegment?.sequence ?? (this.protocol === 'legacy' ? this.legacySequence : this.nextSequence),
      pendingSegments: this.pendingSegments.size + (this.legacyFinalize ? 1 : 0),
      queuedSegments: this.queuedSegments,
      absoluteSample: this.absoluteSample,
      negotiation: this.negotiation,
    };
  }

  async connect(): Promise<void> {
    if (this.captureClosed) throw new Error('Live voice capture epoch is closed.');
    if (this.socket?.readyState === this.options.webSocketCtor.OPEN) return;
    if (this.connecting) {
      await this.waitForExistingConnection();
      return;
    }
    this.connecting = true;
    this.setStatus('connecting');
    this.clearReconnectTimer();
    await new Promise<void>((resolve, reject) => {
      const socket = new this.options.webSocketCtor(this.options.url);
      this.socket = socket;
      let opened = false;
      let settled = false;
      const failInitialConnection = (message: string) => {
        if (settled) return;
        settled = true;
        this.connecting = false;
        this.autoReconnect = false;
        this.pendingAudio = [];
        this.setStatus('error');
        try { socket.close(); } catch { /* cleanup best effort */ }
        reject(new Error(message));
      };
      const timeout = setTimeout(() => failInitialConnection('WebSocket connection timeout'), this.options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS);
      socket.onopen = () => {
        if (settled) return;
        clearTimeout(timeout);
        opened = true;
        settled = true;
        this.connecting = false;
        this.autoReconnect = true;
        this.setStatus('connected');
        resolve();
      };
      socket.onerror = () => {
        clearTimeout(timeout);
        this.options.onError?.('Live voice WebSocket failed.');
        if (!opened) failInitialConnection('Live voice WebSocket failed.');
        else {
          this.connecting = false;
          this.setStatus('error');
        }
      };
      socket.onclose = () => {
        clearTimeout(timeout);
        this.connecting = false;
        this.serverReady = false;
        this.awaitingSessionReady = false;
        this.socket = null;
        if (!opened) {
          failInitialConnection('Live voice WebSocket closed before connecting.');
          return;
        }
        this.setStatus('disconnected');
        this.scheduleReconnect();
      };
      socket.onmessage = (event) => { void this.handleMessage(event.data); };
    });
  }

  sendAudio(audio: Float32Array, sampleRate: number): void {
    const frame: PendingAudioFrame = { audio: new Float32Array(audio), sourceSampleRate: sampleRate };
    if (!this.socket || this.socket.readyState !== this.options.webSocketCtor.OPEN || !this.serverReady || !this.negotiation || this.awaitingSessionReady) {
      this.bufferAudio(frame);
      return;
    }
    this.sendPreparedAudio(frame);
  }

  sendFinal(): string | null {
    if (!this.socket || this.socket.readyState !== this.options.webSocketCtor.OPEN || !this.serverReady || this.awaitingSessionReady) {
      this.pendingFinal = true;
      return null;
    }
    this.flushPreparedAudio();
    if (this.protocol === 'segmented-v1') return this.finalizeActiveSegment();
    if (this.legacyFinalize || this.absoluteSample <= this.legacyStartSample) return this.legacyFinalize?.finalizeRequestId ?? null;
    const finalizeRequestId = createRuntimeId('finalize');
    const segmentId = createRuntimeId(`legacy-segment-${this.legacySequence}`);
    this.legacyFinalize = {
      segmentId,
      sequence: this.legacySequence,
      finalizeRequestId,
      startSample: this.legacyStartSample,
      endSample: this.absoluteSample,
      requestedAtMs: performance.now(),
    };
    this.pendingAudio = [];
    this.socket.send(JSON.stringify({
      type: 'final',
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      clientSegmentId: segmentId,
      sequence: this.legacySequence,
      finalizeRequestId,
      startSample: this.legacyStartSample,
      endSample: this.absoluteSample,
    }));
    this.emitSegmentState();
    return finalizeRequestId;
  }

  requestAuthoritativePreview(): string | null {
    if (
      !this.socket
      || this.socket.readyState !== this.options.webSocketCtor.OPEN
      || !this.serverReady
      || this.awaitingSessionReady
      || this.protocol !== 'segmented-v1'
      || !this.negotiation?.capabilities.includes(CAP_AUTHORITATIVE_PREVIEW)
    ) return null;
    this.flushPreparedAudio();
    const segment = this.activeSegment;
    if (!segment || segment.finalized || this.absoluteSample <= segment.primaryStartSample) return null;
    const previewRequestId = createRuntimeId('preview');
    this.socket.send(JSON.stringify({
      type: 'preview',
      protocol: SEGMENTED_PROTOCOL,
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      segmentId: segment.segmentId,
      sequence: segment.sequence,
      previewRequestId,
      snapshotEndSample: this.absoluteSample,
    }));
    return previewRequestId;
  }

  cancelFlush(attemptId: string): void {
    if (!attemptId || !this.socket || this.socket.readyState !== this.options.webSocketCtor.OPEN || !this.serverReady) return;
    this.socket.send(JSON.stringify({
      type: 'cancel_flush',
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      attemptId,
    }));
  }

  disconnect(): void {
    this.captureClosed = true;
    this.autoReconnect = false;
    this.clearReconnectTimer();
    this.pendingAudio = [];
    this.pendingFinal = false;
    this.pendingSegments.clear();
    this.pendingResults.clear();
    this.failedSequences.clear();
    this.activeSegment = null;
    this.legacyFinalize = null;
    this.serverReady = false;
    this.awaitingSessionReady = false;
    this.resampler.reset();
    this.clearPreparedAudioFlushTimer();
    this.preparedAudioCarry = new Float32Array();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setStatus('idle');
  }

  private get targetSampleRate(): number {
    return this.negotiation?.sampleRate ?? DEFAULT_STT_SAMPLE_RATE;
  }

  private get retainAcknowledgedAudio(): boolean {
    return this.negotiation?.capabilities.includes(CAP_CLIENT_AUDIO_REPLAY) ?? false;
  }

  get authoritativePreviewSupported(): boolean {
    return this.negotiation?.capabilities.includes(CAP_AUTHORITATIVE_PREVIEW) ?? false;
  }

  private get binaryAudioSupported(): boolean {
    return this.negotiation?.capabilities.includes(CAP_BINARY_AUDIO_FRAMES) ?? false;
  }

  private sendAudioMessage(header: Record<string, unknown>, pcm: Uint8Array): void {
    if (!this.socket) return;
    if (this.binaryAudioSupported) this.socket.send(encodeBinaryAudioFrame(header, pcm));
    else this.socket.send(JSON.stringify({ type: 'audio', ...header, data: bytesToBase64(pcm) }));
  }

  private sendPreparedAudio(frame: PendingAudioFrame): void {
    const resampled = this.resampler.transform(frame.audio, frame.sourceSampleRate, this.targetSampleRate);
    if (!resampled.length) return;
    const batched = concatFloat32([this.preparedAudioCarry, resampled]);
    const frameSamples = Math.max(1, this.negotiation?.frameSamples ?? DEFAULT_STT_FRAME_SAMPLES);
    let offset = 0;
    while (batched.length - offset >= frameSamples) {
      this.sendPreparedAudioChunk(batched.slice(offset, offset + frameSamples));
      offset += frameSamples;
    }
    this.preparedAudioCarry = batched.slice(offset);
    if (this.preparedAudioCarry.length) this.schedulePreparedAudioFlush();
    else this.clearPreparedAudioFlushTimer();
  }

  private sendPreparedAudioChunk(audio: Float32Array): void {
    if (this.protocol === 'segmented-v1') this.sendSegmentAudio(audio);
    else {
      this.sendLegacyAudio(audio);
      this.absoluteSample += audio.length;
    }
  }

  private schedulePreparedAudioFlush(): void {
    if (this.preparedAudioFlushTimer) return;
    const sampleRate = Math.max(1, this.targetSampleRate);
    const frameSamples = Math.max(1, this.negotiation?.frameSamples ?? DEFAULT_STT_FRAME_SAMPLES);
    const delayMs = Math.max(1, Math.round(frameSamples * 1_000 / sampleRate));
    this.preparedAudioFlushTimer = setTimeout(() => {
      this.preparedAudioFlushTimer = null;
      this.flushPreparedAudio();
    }, delayMs);
  }

  private flushPreparedAudio(): void {
    this.clearPreparedAudioFlushTimer();
    if (!this.preparedAudioCarry.length) return;
    const audio = this.preparedAudioCarry;
    this.preparedAudioCarry = new Float32Array();
    this.sendPreparedAudioChunk(audio);
  }

  private clearPreparedAudioFlushTimer(): void {
    if (!this.preparedAudioFlushTimer) return;
    clearTimeout(this.preparedAudioFlushTimer);
    this.preparedAudioFlushTimer = null;
  }

  private sendLegacyAudio(audio: Float32Array): void {
    this.sendAudioMessage({ sampleRate: this.targetSampleRate }, encodePcm16(audio));
  }

  private ensureActiveSegment(): PendingSegment {
    if (this.activeSegment) return this.activeSegment;
    const overlap = concatFloat32(this.recentOverlapFrames);
    const primaryStartSample = this.absoluteSample;
    const captureStartSample = Math.max(0, primaryStartSample - overlap.length);
    const segment: PendingSegment = {
      segmentId: createRuntimeId(`segment-${this.nextSequence}`),
      sequence: this.nextSequence,
      captureStartSample,
      primaryStartSample,
      frames: [],
      acceptedThroughSample: captureStartSample,
      finalized: false,
      finalizeQueued: false,
      finalizeRequestId: null,
      finalizeRequestedAtMs: null,
      endSample: null,
    };
    this.nextSequence += 1;
    this.activeSegment = segment;
    this.pendingSegments.set(segment.segmentId, segment);
    if (overlap.length) this.sendSegmentFrame(segment, overlap, captureStartSample);
    this.emitSegmentState();
    return segment;
  }

  private sendSegmentAudio(audio: Float32Array): void {
    const segment = this.ensureActiveSegment();
    const sampleStart = this.absoluteSample;
    this.sendSegmentFrame(segment, audio, sampleStart);
    this.absoluteSample += audio.length;
    this.rememberOverlap(audio);
    const hardSegmentSamples = Math.round(this.targetSampleRate * (this.options.hardSegmentMs ?? DEFAULT_HARD_SEGMENT_MS) / 1_000);
    if (this.absoluteSample - segment.primaryStartSample >= hardSegmentSamples) this.finalizeActiveSegment();
    this.emitSegmentState();
  }

  private sendSegmentFrame(segment: PendingSegment, audio: Float32Array, sampleStart: number): void {
    const frame: PendingSegmentFrame = {
      sampleStart,
      sampleEnd: sampleStart + audio.length,
      pcm: encodePcm16(audio),
    };
    segment.frames.push(frame);
    this.sendFrame(segment, frame);
  }

  private sendFrame(segment: PendingSegment, frame: PendingSegmentFrame): void {
    if (!this.socket || this.socket.readyState !== this.options.webSocketCtor.OPEN || !this.serverReady || this.awaitingSessionReady) return;
    this.sendAudioMessage({
      protocol: SEGMENTED_PROTOCOL,
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      segmentId: segment.segmentId,
      sequence: segment.sequence,
      captureStartSample: segment.captureStartSample,
      primaryStartSample: segment.primaryStartSample,
      sampleStart: frame.sampleStart,
      sampleEnd: frame.sampleEnd,
      sampleRate: this.targetSampleRate,
    }, frame.pcm);
  }

  private finalizeActiveSegment(): string | null {
    const segment = this.activeSegment;
    if (!segment || segment.finalized || this.absoluteSample <= segment.primaryStartSample) return segment?.finalizeRequestId ?? null;
    segment.finalized = true;
    segment.finalizeRequestId = createRuntimeId('finalize');
    segment.finalizeRequestedAtMs = performance.now();
    segment.endSample = this.absoluteSample;
    this.socket?.send(JSON.stringify({
      type: 'finalize',
      protocol: SEGMENTED_PROTOCOL,
      sessionId: this.sessionId,
      captureEpoch: this.captureEpoch,
      segmentId: segment.segmentId,
      sequence: segment.sequence,
      finalizeRequestId: segment.finalizeRequestId,
      captureStartSample: segment.captureStartSample,
      primaryStartSample: segment.primaryStartSample,
      endSample: segment.endSample,
    }));
    this.activeSegment = null;
    this.emitSegmentState();
    return segment.finalizeRequestId;
  }

  private rememberOverlap(audio: Float32Array): void {
    this.recentOverlapFrames.push(new Float32Array(audio));
    this.recentOverlapSamples += audio.length;
    const maximum = Math.round(this.targetSampleRate * (this.options.overlapMs ?? DEFAULT_OVERLAP_MS) / 1_000);
    while (this.recentOverlapSamples > maximum && this.recentOverlapFrames.length) {
      const first = this.recentOverlapFrames[0];
      const excess = this.recentOverlapSamples - maximum;
      if (first.length <= excess) {
        this.recentOverlapFrames.shift();
        this.recentOverlapSamples -= first.length;
      } else {
        this.recentOverlapFrames[0] = first.slice(excess);
        this.recentOverlapSamples -= excess;
      }
    }
  }

  private async waitForExistingConnection(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const stopWaiting = startTicker(() => {
        if (this.socket?.readyState === this.options.webSocketCtor.OPEN) {
          stopWaiting();
          resolve();
        } else if (!this.connecting) {
          stopWaiting();
          reject(new Error('WebSocket connection failed'));
        }
      }, 100);
    });
  }

  private async handleMessage(rawData: string): Promise<void> {
    const message = parseJson(streamingSttMessageSchema, rawData);
    if (!message) {
      this.options.onError?.('Could not parse live voice transcript message.');
      return;
    }
    switch (message.type) {
      case 'ready': {
        let received: StreamingSttNegotiation;
        try {
          received = normalizeReady(message);
        } catch (error) {
          this.failNegotiation(error instanceof Error ? error.message : 'Live STT negotiation failed.');
          return;
        }
        if (this.negotiation && !negotiationsMatch(this.negotiation, received)) {
          this.failNegotiation('Live STT negotiation changed during the active capture epoch.');
          return;
        }
        if (!this.negotiation) {
          this.negotiation = received;
          this.options.onNegotiated?.(received);
          dispatchSttDiagnostic('stt_negotiated', {
            provider: received.provider,
            protocol: received.protocol,
            sampleRate: received.sampleRate,
            frameSamples: received.frameSamples,
            capabilities: received.capabilities,
            language: received.language,
          });
        }
        this.protocol = this.negotiation.protocol;
        this.serverReady = true;
        if (this.protocol === 'segmented-v1') {
          this.awaitingSessionReady = true;
          this.socket?.send(JSON.stringify({
            type: 'hello',
            protocol: SEGMENTED_PROTOCOL,
            sessionId: this.sessionId,
            captureEpoch: this.captureEpoch,
            sampleRate: this.targetSampleRate,
            configVersion: this.negotiation.configVersion,
            language: this.negotiation.language,
          }));
        } else {
          this.flushPendingAudio();
          if (this.pendingFinal) {
            this.pendingFinal = false;
            this.sendFinal();
          }
        }
        this.emitSegmentState();
        return;
      }
      case 'session_ready':
        for (const result of message.results ?? []) this.acceptResult(result, false);
        await this.commitOrderedResults();
        this.awaitingSessionReady = false;
        dispatchSttDiagnostic('stt_session_restored', {
          provider: message.provider ?? this.negotiation?.provider,
          replayedResults: message.results?.length ?? 0,
          pendingSegments: this.pendingSegments.size,
        });
        this.replayPendingSegments();
        this.flushPendingAudio();
        if (this.pendingFinal) {
          this.pendingFinal = false;
          this.sendFinal();
        }
        this.emitSegmentState();
        return;
      case 'text':
      case 'partial':
        this.options.onPartialTranscript?.(message.text);
        return;
      case 'word':
        dispatchSttDiagnostic('stt_word', {
          provider: message.provider,
          segmentId: message.segmentId,
          sourceSequence: message.sequence,
          startMs: message.startMs,
          endMs: message.endMs,
          textChars: message.text.length,
        });
        this.options.onWord?.(message);
        return;
      case 'endpoint_score':
        dispatchSttDiagnostic('stt_endpoint_score', {
          provider: message.provider,
          segmentId: message.segmentId,
          sourceSequence: message.sequence,
          probability: message.probability,
          modelTimeMs: message.modelTimeMs,
          signal: message.signal,
        });
        this.options.onEndpointScore?.(message);
        return;
      case 'endpoint_candidate':
        dispatchSttDiagnostic('stt_endpoint_candidate', {
          provider: message.provider,
          segmentId: message.segmentId,
          sourceSequence: message.sequence,
          probability: message.probability,
          modelTimeMs: message.modelTimeMs,
        });
        this.options.onEndpointCandidate?.(message);
        return;
      case 'preview_result':
        dispatchSttDiagnostic('stt_authoritative_preview', {
          provider: message.provider,
          segmentId: message.segmentId,
          sourceSequence: message.sequence,
          previewRequestId: message.previewRequestId,
          snapshotEndSample: message.snapshotEndSample,
          transcriptChars: message.text.trim().length,
          providerMetrics: message.providerMetrics,
        });
        this.options.onPreviewTranscript?.(message);
        return;
      case 'flush_started':
      case 'flush_completed':
      case 'flush_cancelled':
        dispatchSttDiagnostic(`stt_${message.type}`, {
          provider: message.provider,
          attemptId: message.attemptId,
          wallMs: message.wall_ms,
          modelMs: message.model_ms,
          realtimeFactor: message.realtime_factor,
        });
        this.options.onProviderEvent?.(message);
        return;
      case 'done':
        await this.acceptLegacyResult(message);
        return;
      case 'audio_buffered': {
        const segment = this.pendingSegments.get(message.segmentId);
        if (!segment) return;
        segment.acceptedThroughSample = Math.max(segment.acceptedThroughSample, message.acceptedThroughSample);
        if (!this.retainAcknowledgedAudio) {
          segment.frames = segment.frames.filter((frame) => frame.sampleEnd > segment.acceptedThroughSample);
        }
        this.emitSegmentState();
        return;
      }
      case 'finalize_queued': {
        const segment = this.pendingSegments.get(message.segmentId);
        if (segment) segment.finalizeQueued = true;
        this.queuedSegments = message.queuedSegments ?? this.queuedSegments;
        this.emitSegmentState();
        return;
      }
      case 'result_available':
        dispatchSttDiagnostic('stt_provider_final', {
          provider: message.provider,
          segmentId: message.segmentId,
          sourceSequence: message.sequence,
          providerMetrics: message.providerMetrics,
          transcriptChars: message.text.trim().length,
        });
        this.acceptResult(message);
        return;
      case 'segment_error':
        this.handleSegmentError(message);
        return;
      case 'error':
        dispatchSttDiagnostic('stt_provider_error', {
          provider: this.negotiation?.provider,
          errorCode: message.errorCode,
          retryable: message.retryable,
        });
        this.options.onError?.(message.error ?? message.errorCode ?? 'Live voice transcription failed.');
        return;
      default:
        this.options.onError?.('Unknown live voice transcript message.');
    }
  }

  private failNegotiation(message: string): void {
    dispatchSttDiagnostic('stt_negotiation_failed', { message });
    this.options.onError?.(message);
    this.autoReconnect = false;
    this.serverReady = false;
    this.awaitingSessionReady = false;
    this.setStatus('error');
    try { this.socket?.close(); } catch { /* best-effort close */ }
  }

  private handleSegmentError(message: Extract<StreamingSttMessage, { type: 'segment_error' }>): void {
    dispatchSttDiagnostic('stt_segment_error', {
      provider: this.negotiation?.provider,
      segmentId: message.segmentId,
      sourceSequence: message.sequence,
      errorCode: message.errorCode,
      retryable: message.retryable,
    });
    this.options.onError?.(message.error ?? message.errorCode ?? 'Live voice segment failed.');
    const segment = message.segmentId ? this.pendingSegments.get(message.segmentId) : undefined;
    const sequence = message.sequence ?? segment?.sequence;
    if (segment) {
      this.pendingSegments.delete(segment.segmentId);
      if (this.activeSegment?.segmentId === segment.segmentId) this.activeSegment = null;
    }
    if (sequence !== undefined && sequence >= this.nextCommitSequence) {
      this.pendingResults.delete(sequence);
      this.failedSequences.add(sequence);
      this.advanceFailedSequences();
      void this.commitOrderedResults();
    }
    this.rejectFinal('segment_error', {
      segmentId: message.segmentId,
      sourceSequence: sequence,
    });
    this.emitSegmentState();
  }

  private async acceptLegacyResult(result: LegacySttResult): Promise<void> {
    const pending = this.legacyFinalize;
    if (!pending) {
      this.rejectFinal('legacy_finalize_not_pending', result);
      return;
    }
    if (this.captureClosed || result.captureEpoch !== this.captureEpoch) {
      this.rejectFinal('capture_epoch_closed_or_mismatched', result);
      return;
    }
    if (result.finalizeRequestId !== pending.finalizeRequestId || result.sequence !== pending.sequence || result.segmentId !== pending.segmentId) {
      this.rejectFinal('legacy_finalize_identity_mismatch', result);
      return;
    }
    if (this.isExpired(pending.requestedAtMs)) {
      this.rejectFinal('finalize_result_expired', result);
      this.legacyFinalize = null;
      this.legacySequence += 1;
      this.legacyStartSample = pending.endSample;
      this.emitSegmentState();
      return;
    }
    const final = this.toAcceptedFinal(result, pending.requestedAtMs, 'legacy');
    await this.deliverAcceptedFinal(final);
    this.committedResultIds.add(result.resultId);
    this.legacyFinalize = null;
    this.legacySequence += 1;
    this.legacyStartSample = pending.endSample;
    this.emitSegmentState();
  }

  private acceptResult(result: SegmentedSttResult, scheduleCommit = true): void {
    if (this.committedResultIds.has(result.resultId) || result.sequence < this.nextCommitSequence) return;
    const segment = this.pendingSegments.get(result.segmentId);
    if (!segment) {
      this.rejectFinal('segment_not_pending', result);
      return;
    }
    if (this.captureClosed || result.captureEpoch !== this.captureEpoch) {
      this.rejectFinal('capture_epoch_closed_or_mismatched', result);
      return;
    }
    if (!segment.finalizeRequestId || result.finalizeRequestId !== segment.finalizeRequestId) {
      this.rejectFinal('finalize_request_mismatch', result);
      return;
    }
    if (segment.finalizeRequestedAtMs === null || this.isExpired(segment.finalizeRequestedAtMs)) {
      this.rejectFinal('finalize_result_expired', result);
      this.pendingSegments.delete(result.segmentId);
      this.failedSequences.add(result.sequence);
      this.advanceFailedSequences();
      return;
    }
    this.pendingResults.set(result.sequence, result);
    if (scheduleCommit) void this.commitOrderedResults();
  }

  private advanceFailedSequences(): void {
    while (this.failedSequences.delete(this.nextCommitSequence)) this.nextCommitSequence += 1;
  }

  private async commitOrderedResults(): Promise<void> {
    if (this.drainingResults) return;
    this.drainingResults = true;
    try {
      this.advanceFailedSequences();
      while (this.pendingResults.has(this.nextCommitSequence)) {
        const result = this.pendingResults.get(this.nextCommitSequence)!;
        const segment = this.pendingSegments.get(result.segmentId);
        if (!segment || segment.finalizeRequestedAtMs === null) {
          this.pendingResults.delete(this.nextCommitSequence);
          this.rejectFinal('segment_commit_state_missing', result);
          this.nextCommitSequence += 1;
          this.advanceFailedSequences();
          continue;
        }
        const deduplicated = deduplicateSegmentBoundary(this.previousCommittedText, result.text);
        const final = this.toAcceptedFinal({ ...result, text: deduplicated }, segment.finalizeRequestedAtMs, 'segmented-v1');
        await this.deliverAcceptedFinal(final);
        this.pendingResults.delete(this.nextCommitSequence);
        this.committedResultIds.add(result.resultId);
        if (result.text.trim()) this.previousCommittedText = result.text.trim();
        this.pendingSegments.delete(result.segmentId);
        this.nextCommitSequence += 1;
        this.advanceFailedSequences();
      }
    } finally {
      this.drainingResults = false;
      this.emitSegmentState();
    }
  }

  private toAcceptedFinal(
    result: SegmentedSttResult | LegacySttResult,
    finalizeRequestedAtMs: number,
    protocol: LiveSttProtocol,
  ): AcceptedVoiceFinal {
    return {
      chatSessionId: this.options.chatSessionId ?? 'unbound-chat-session',
      sttSessionId: result.sessionId,
      captureEpoch: result.captureEpoch,
      segmentId: result.segmentId,
      resultId: result.resultId,
      finalizeRequestId: result.finalizeRequestId,
      sourceSequence: result.sequence,
      startSample: result.startSample,
      endSample: result.endSample,
      protocol,
      text: result.text.trim(),
      provider: 'provider' in result ? result.provider : undefined,
      providerMetrics: 'providerMetrics' in result ? result.providerMetrics : undefined,
      finalizeRequestedAtMs,
      receivedAtMs: performance.now(),
    };
  }

  private async deliverAcceptedFinal(final: AcceptedVoiceFinal): Promise<void> {
    try {
      await this.options.onAcceptedFinal?.(final);
    } catch (error) {
      this.options.onError?.(error instanceof Error ? error.message : 'Live final routing failed.');
    }
  }

  private rejectFinal(reason: string, identity: Partial<AcceptedVoiceFinal> | SegmentedSttResult | LegacySttResult): void {
    this.options.onFinalRejected?.(reason, identity as Partial<AcceptedVoiceFinal>);
  }

  private isExpired(requestedAtMs: number): boolean {
    return performance.now() - requestedAtMs > (this.options.maxFinalResultAgeMs ?? DEFAULT_MAX_FINAL_RESULT_AGE_MS);
  }

  private bufferAudio(frame: PendingAudioFrame): void {
    if (!this.connecting && !this.autoReconnect && !this.socket) return;
    this.pendingAudio.push(frame);
    const maxPendingChunks = this.options.maxPendingChunks ?? DEFAULT_MAX_PENDING_CHUNKS;
    while (this.pendingAudio.length > maxPendingChunks) this.pendingAudio.shift();
  }

  private flushPendingAudio(): void {
    if (!this.socket || this.socket.readyState !== this.options.webSocketCtor.OPEN || !this.serverReady || !this.negotiation || this.awaitingSessionReady) return;
    const pending = this.pendingAudio;
    this.pendingAudio = [];
    for (const frame of pending) this.sendPreparedAudio(frame);
  }

  private replayPendingSegments(): void {
    if (!this.segmentedProtocolActive) return;
    for (const segment of [...this.pendingSegments.values()].sort((left, right) => left.sequence - right.sequence)) {
      for (const frame of segment.frames) this.sendFrame(segment, frame);
      if (segment.finalized && segment.finalizeRequestId && segment.endSample !== null) {
        this.socket?.send(JSON.stringify({
          type: 'finalize',
          protocol: SEGMENTED_PROTOCOL,
          sessionId: this.sessionId,
          captureEpoch: this.captureEpoch,
          segmentId: segment.segmentId,
          sequence: segment.sequence,
          finalizeRequestId: segment.finalizeRequestId,
          captureStartSample: segment.captureStartSample,
          primaryStartSample: segment.primaryStartSample,
          endSample: segment.endSample,
        }));
      }
    }
  }

  private scheduleReconnect(): void {
    if (!this.autoReconnect || this.captureClosed) return;
    this.clearReconnectTimer();
    this.reconnectTimer = setTimeout(() => {
      void this.connect().catch((error) => {
        this.options.onError?.(error instanceof Error ? error.message : 'Live voice reconnect failed.');
      });
    }, this.options.reconnectDelayMs ?? DEFAULT_RECONNECT_DELAY_MS);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setStatus(status: StreamingSttConnectionStatus): void {
    this.options.onStatusChange?.(status);
  }

  private emitSegmentState(): void {
    this.options.onSegmentStateChange?.(this.segmentState);
  }
}
