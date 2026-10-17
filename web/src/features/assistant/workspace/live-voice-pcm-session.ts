import type { LiveCallDiagnosticsReporter } from './live-call-diagnostics-client';
import { createLiveSpeechSynthesisOptions } from './live-speech-synthesis-options';
import type { SpeechSynthesisOptions } from './live-speech-performance-contract';
import { resolveCueSamples, type LiveVoiceCueId } from './live-voice-cue-bank';
import {
  createCueSegmentId,
  createSilenceSegmentId,
  createSpeechSegmentId,
  millisecondsToPlaybackSamples,
  type PlaybackStartPolicyMs,
  type SilenceReason,
} from './live-voice-playback-contract';
import { LIVE_VOICE_PCM_WORKLET_NAME, LIVE_VOICE_PCM_WORKLET_URL } from './live-voice-pcm-worklet';
import { pcmControlEventSchema } from './live-voice-messages';
import { parseJson } from '../../../api/schemas/streams';
import { CHARACTER_AVATAR_PCM_EVENT, emitOmnixEvent } from '../../../events/bus';

const REQUESTED_SAMPLE_RATE = 24_000;
const START_BUFFER_SECONDS = 0.4;
const REBUFFER_SECONDS = 0.75;
const MAX_REBUFFER_SECONDS = 1.5;
const TRANSITION_FADE_SECONDS = 0.008;
const TTS_LIVE_CALL_WEBSOCKET_PATH = '/api/tts/live-call/websocket';
const TTS_CHUNK_SIZE = 8;
const DRAIN_TIMEOUT_MS = 120_000;
const WEBSOCKET_OPEN = 1;

export type LiveOutputOwnership = {
  outputId: string;
  generationEpoch: number;
  outputOrder: number;
};

export type LiveVoicePcmSessionOptions = {
  sessionScoped?: boolean;
  affinityKey?: string;
  onWorkletEvent?: (event: Record<string, unknown>) => void;
};

type StreamingAudioWindow = Window & typeof globalThis & {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
  AudioWorkletNode?: typeof AudioWorkletNode;
  WebSocket?: typeof WebSocket;
};

export type PcmControlEvent = {
  type?: string;
  message?: string;
  sample_rate?: number;
  stream_id?: string;
  phrase_index?: number;
  partial?: boolean;
  output_id?: string;
  generation_epoch?: number;
  output_order?: number;
  retry_after?: string | number;
  capacity_saturated?: boolean;
  segment_id?: string;
  last_frame_index?: number;
  generated_through_frame?: number;
  reason?: string;
};

type WorkletEvent = {
  type?: string;
  sample_rate?: number;
  segment_id?: string;
  segment_kind?: 'speech' | 'silence' | 'cue';
  phrase_index?: number;
  output_id?: string;
  generation_epoch?: number;
  output_order?: number;
  segment_played_samples?: number;
  buffered_samples?: number;
  buffered_speech_samples?: number;
  incoming_samples?: number;
  target_samples?: number;
  render_clock_samples?: number;
  segment_timeline_samples?: number;
  semantic_speech_samples?: number;
  played_samples?: number;
  underrun_count?: number;
  current_rebuffer_samples?: number;
  waiting_for_buffer?: boolean;
  waiting_for_following_speech?: boolean;
  input_ended?: boolean;
  reason?: string;
  removed_samples?: number;
  removed_speech_samples?: number;
};

type PhraseStats = {
  frameCount: number;
  receivedBytes: number;
  receivedSamples: number;
  playbackSamples: number;
  firstFrameAtMs: number | null;
  lastFrameAtMs: number | null;
  sampleRate: number;
};

type ActivePhrase = {
  text: string;
  phraseIndex: number;
  phraseStreamId: string;
  segmentId: string;
  ownership: LiveOutputOwnership | null;
  startedAtMs: number;
  stats: PhraseStats;
  completed: boolean;
  cancelled: boolean;
  resolve: () => void;
  reject: (error: Error) => void;
};

type QueuedOutput = {
  ownership: LiveOutputOwnership;
  cancelled: boolean;
};

export type LiveVoicePcmSession = {
  readonly sampleRate: number;
  enqueuePhrase: (
    text: string,
    phraseIndex: number,
    synthesisOptions?: SpeechSynthesisOptions,
  ) => Promise<void>;
  enqueueOutputPhrase: (
    text: string,
    phraseIndex: number,
    ownership: LiveOutputOwnership,
    synthesisOptions?: SpeechSynthesisOptions,
  ) => Promise<void>;
  enqueueSilence: (
    durationMs: number,
    reason: SilenceReason,
    minimumFollowingSpeechMs?: number,
  ) => Promise<void>;
  enqueueCue: (
    cueId: LiveVoiceCueId,
    variantId: string,
    gainValue?: number,
    allowProceduralFallback?: boolean,
  ) => Promise<void>;
  cancelSegment: (segmentId: string, reason?: string) => void;
  cancelOutputItem: (outputId: string, generationEpoch: number, reason?: string) => Promise<void>;
  cancelAllAfter: (outputOrder: number, reason?: string) => void;
  waitForOutputItem: (outputId: string, generationEpoch: number) => Promise<void>;
  setStartPolicy: (policy: Partial<PlaybackStartPolicyMs>) => void;
  finish: () => Promise<void>;
  stop: (reason?: string) => Promise<void>;
  isClosed: () => boolean;
};

type OutputTimestampSource = {
  getOutputTimestamp?: () => {
    contextTime?: number;
    performanceTime?: number;
  };
};

export function resolveWorkletPlaybackPerformanceTimeMs(
  event: Record<string, unknown>,
  audioContext: OutputTimestampSource,
  receivedAtMs = performance.now(),
): number | null {
  const eventContextTime = event.audio_context_time_seconds;
  if (typeof eventContextTime !== 'number' || !Number.isFinite(eventContextTime)) return null;
  if (typeof audioContext.getOutputTimestamp !== 'function') return null;
  try {
    const timestamp = audioContext.getOutputTimestamp();
    const { contextTime, performanceTime } = timestamp;
    if (typeof contextTime !== 'number' || !Number.isFinite(contextTime)
      || typeof performanceTime !== 'number' || !Number.isFinite(performanceTime)) return null;
    const projected = performanceTime
      + ((eventContextTime - contextTime) * 1_000);
    // Reject a broken or cross-origin clock mapping. A worklet notification can
    // be delayed, but a live-call playback event cannot reasonably predate its
    // receipt by more than ten seconds or be materially in the future.
    if (!Number.isFinite(projected)
      || projected < receivedAtMs - 10_000
      || projected > receivedAtMs + 250) return null;
    return Math.min(receivedAtMs, projected);
  } catch {
    return null;
  }
}

export async function createLiveVoicePcmSession(
  traceId: string,
  voiceId: string | null,
  reporter: LiveCallDiagnosticsReporter,
  options: LiveVoicePcmSessionOptions = {},
): Promise<LiveVoicePcmSession> {
  const liveWindow = window as StreamingAudioWindow;
  const AudioContextCtor = liveWindow.AudioContext ?? liveWindow.webkitAudioContext;
  const AudioWorkletNodeCtor = liveWindow.AudioWorkletNode;
  const WebSocketCtor = liveWindow.WebSocket;
  if (!AudioContextCtor || !AudioWorkletNodeCtor || !WebSocketCtor) {
    throw new Error('Live voice streaming requires AudioWorklet and WebSocket support.');
  }

  const startedAtMs = performance.now();
  const audioContext = new AudioContextCtor({ latencyHint: 'interactive', sampleRate: REQUESTED_SAMPLE_RATE });
  if (audioContext.state !== 'running') await audioContext.resume();
  await audioContext.audioWorklet.addModule(LIVE_VOICE_PCM_WORKLET_URL);

  let closed = false;
  const affinityCookieValue = setCallAffinityCookie(options.affinityKey);
  let inputFinished = false;
  let generationQueue: Promise<void> = Promise.resolve();
  let activePhrase: ActivePhrase | null = null;
  let socketFailure: Error | null = null;
  let socketOpened = false;
  let drained = false;
  let drainResolve: (() => void) | null = null;
  let totalFrames = 0;
  let totalReceivedSamples = 0;
  let totalPlaybackSamples = 0;
  let underruns = 0;
  let resumes = 0;
  let silenceSequence = 0;
  let cueSequence = 0;
  const queuedOutputs = new Map<string, QueuedOutput>();
  const cancelledOutputKeys = new Set<string>();
  const terminalOutputKeys = new Set<string>();
  const outputWaiters = new Map<string, Set<() => void>>();
  const drainPromise = new Promise<void>((resolve) => {
    drainResolve = resolve;
  });

  const outputKey = (outputId: string, generationEpoch: number): string => `${outputId}:${generationEpoch}`;
  const settleOutput = (key: string): void => {
    terminalOutputKeys.add(key);
    while (terminalOutputKeys.size > 512) terminalOutputKeys.delete(terminalOutputKeys.values().next().value as string);
    const waiters = outputWaiters.get(key);
    outputWaiters.delete(key);
    waiters?.forEach((resolve) => resolve());
  };

  const resumeAudioContext = async (reason: string): Promise<void> => {
    const before = audioContext.state;
    if (before === 'closed') {
      reporter.record('audio_context_resume_skipped', { reason, audio_context_state: before }, 'pcm_session');
      return;
    }
    if (before !== 'running') {
      await audioContext.resume().catch((error: unknown) => {
        reporter.record('audio_context_resume_failed', {
          reason,
          audio_context_state: before,
          error: error instanceof Error ? error.message : String(error),
        }, 'pcm_session');
      });
    }
    reporter.record('audio_context_resume_checked', {
      reason,
      audio_context_state_before: before,
      audio_context_state_after: audioContext.state,
      document_visibility: document.visibilityState,
    }, 'pcm_session');
  };

  const handlePlaybackResumeSignal = (event: Event): void => {
    if (closed) return;
    void resumeAudioContext(event.type);
  };
  document.addEventListener('visibilitychange', handlePlaybackResumeSignal);
  window.addEventListener('focus', handlePlaybackResumeSignal);
  window.addEventListener('pageshow', handlePlaybackResumeSignal);

  const startBufferSamples = Math.round(audioContext.sampleRate * START_BUFFER_SECONDS);
  const node = new AudioWorkletNodeCtor(audioContext, LIVE_VOICE_PCM_WORKLET_NAME, {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [1],
    processorOptions: {
      startBufferSamples,
      minimumBufferedSpeechSamples: startBufferSamples,
      notBeforeRenderSample: 0,
      rebufferSamples: Math.round(audioContext.sampleRate * REBUFFER_SECONDS),
      maxRebufferSamples: Math.round(audioContext.sampleRate * MAX_REBUFFER_SECONDS),
      transitionFadeSamples: Math.round(audioContext.sampleRate * TRANSITION_FADE_SECONDS),
    },
  });

  node.port.onmessage = (event: MessageEvent<WorkletEvent>) => {
    if (closed) return;
    const eventType = event.data?.type ?? 'unknown';
    if (eventType === 'underrun') underruns += 1;
    if (eventType === 'resumed') resumes += 1;
    const playbackPerformanceTimeMs = eventType === 'segment_started'
      ? resolveWorkletPlaybackPerformanceTimeMs(event.data, audioContext)
      : null;
    const details = {
      ...event.data,
      ...(playbackPerformanceTimeMs === null
        ? {}
        : { playback_performance_time_ms: playbackPerformanceTimeMs }),
      sample_rate: event.data?.sample_rate ?? audioContext.sampleRate,
      audio_context_state: audioContext.state,
      total_frames: totalFrames,
      total_received_samples: totalReceivedSamples,
      total_playback_samples: totalPlaybackSamples,
      total_received_audio_ms: totalReceivedSamples * 1000 / REQUESTED_SAMPLE_RATE,
      underruns,
      resumes,
    };
    reporter.record(`worklet_${eventType}`, details, 'audio_worklet');
    options.onWorkletEvent?.(details);
    const outputId = event.data?.output_id;
    const generationEpoch = event.data?.generation_epoch;
    if (outputId && typeof generationEpoch === 'number'
      && (eventType === 'segment_completed' || eventType === 'segment_cancelled' || eventType === 'segment_interrupted')) {
      settleOutput(outputKey(outputId, generationEpoch));
    }
    if (eventType === 'drained') {
      drained = true;
      drainResolve?.();
    }
  };
  node.connect(audioContext.destination);

  reporter.record('pcm_session_created', {
    voice_id: voiceId,
    audio_context_sample_rate: audioContext.sampleRate,
    audio_context_state: audioContext.state,
    start_buffer_samples: startBufferSamples,
    rebuffer_samples: Math.round(audioContext.sampleRate * REBUFFER_SECONDS),
    max_rebuffer_samples: Math.round(audioContext.sampleRate * MAX_REBUFFER_SECONDS),
    transition_fade_samples: Math.round(audioContext.sampleRate * TRANSITION_FADE_SECONDS),
    websocket_scope: options.sessionScoped ? 'live_session' : 'turn',
  }, 'pcm_session');

  let socketReadyResolve: (() => void) | null = null;
  let socketReadyReject: ((error: Error) => void) | null = null;
  const socketReady = new Promise<void>((resolve, reject) => {
    socketReadyResolve = resolve;
    socketReadyReject = reject;
  });
  const socket = new WebSocketCtor(ttsWebSocketUrl());
  socket.binaryType = 'arraybuffer';
  reporter.record('session_websocket_created', {
    websocket_path: TTS_LIVE_CALL_WEBSOCKET_PATH,
    websocket_scope: options.sessionScoped ? 'live_session' : 'turn',
  }, 'pcm_session');

  const settleCancelledPhrase = (phrase: ActivePhrase, reason: string): void => {
    if (phrase.completed) return;
    phrase.completed = true;
    phrase.cancelled = true;
    if (activePhrase === phrase) activePhrase = null;
    reporter.record('phrase_cancelled', {
      segment_id: phrase.segmentId,
      phrase_index: phrase.phraseIndex,
      phrase_stream_id: phrase.phraseStreamId,
      output_id: phrase.ownership?.outputId ?? null,
      generation_epoch: phrase.ownership?.generationEpoch ?? null,
      reason,
      elapsed_ms: performance.now() - phrase.startedAtMs,
    }, 'pcm_session');
    phrase.resolve();
  };

  const failActivePhrase = (error: Error): void => {
    const phrase = activePhrase;
    if (!phrase || phrase.completed) return;
    phrase.completed = true;
    activePhrase = null;
    reporter.record('phrase_generation_failed', {
      segment_id: phrase.segmentId,
      phrase_index: phrase.phraseIndex,
      phrase_stream_id: phrase.phraseStreamId,
      output_id: phrase.ownership?.outputId ?? null,
      generation_epoch: phrase.ownership?.generationEpoch ?? null,
      text: phrase.text,
      text_length: phrase.text.length,
      elapsed_ms: performance.now() - phrase.startedAtMs,
      error: error.message,
    }, 'pcm_session');
    phrase.reject(error);
  };

  const handleBinaryFrame = (buffer: ArrayBuffer): void => {
    const phrase = activePhrase;
    if (!phrase || phrase.completed || phrase.cancelled) {
      reporter.record('unexpected_audio_frame', { bytes: buffer.byteLength }, 'pcm_session');
      return;
    }
    if (phrase.ownership && cancelledOutputKeys.has(outputKey(phrase.ownership.outputId, phrase.ownership.generationEpoch))) {
      reporter.record('cancelled_output_frame_rejected', {
        output_id: phrase.ownership.outputId,
        generation_epoch: phrase.ownership.generationEpoch,
        bytes: buffer.byteLength,
      }, 'pcm_session');
      return;
    }
    const now = performance.now();
    if (phrase.stats.firstFrameAtMs === null) {
      phrase.stats.firstFrameAtMs = now;
      reporter.record('phrase_first_frame_received', {
        segment_id: phrase.segmentId,
        phrase_index: phrase.phraseIndex,
        phrase_stream_id: phrase.phraseStreamId,
        output_id: phrase.ownership?.outputId ?? null,
        generation_epoch: phrase.ownership?.generationEpoch ?? null,
        first_frame_delay_ms: now - phrase.startedAtMs,
      }, 'pcm_session');
    }
    phrase.stats.lastFrameAtMs = now;
    phrase.stats.frameCount += 1;
    phrase.stats.receivedBytes += buffer.byteLength;
    const sourceSamples = Math.floor(buffer.byteLength / 2);
    phrase.stats.receivedSamples += sourceSamples;
    totalFrames += 1;
    totalReceivedSamples += sourceSamples;
    const evenBytes = buffer.byteLength - (buffer.byteLength % 2);
    const sourcePcm = new Int16Array(buffer.slice(0, evenBytes));
    const converted = pcm16ToFloat32(sourcePcm, phrase.stats.sampleRate, audioContext.sampleRate);
    phrase.stats.playbackSamples += converted.length;
    totalPlaybackSamples += converted.length;
    const workletMessage: Record<string, unknown> = {
      type: 'push_segment_samples',
      segmentId: phrase.segmentId,
      segmentKind: 'speech',
      phraseIndex: phrase.phraseIndex,
      samples: converted,
    };
    if (phrase.ownership) {
      workletMessage.outputId = phrase.ownership.outputId;
      workletMessage.generationEpoch = phrase.ownership.generationEpoch;
      workletMessage.outputOrder = phrase.ownership.outputOrder;
    }
    // Keep the physical playback path ahead of synchronous visualization and
    // echo-reference consumers. A large promoted startup frame can make those
    // listeners do enough main-thread work to delay this postMessage by an
    // entire audio runway, even though PCM has already reached the browser.
    node.port.postMessage(workletMessage, [converted.buffer]);
    emitOmnixEvent(CHARACTER_AVATAR_PCM_EVENT, { samples: sourcePcm, sampleRate: phrase.stats.sampleRate });
  };

  const controlMatchesPhrase = (message: PcmControlEvent, phrase: ActivePhrase): boolean => {
    if (message.stream_id && message.stream_id !== phrase.phraseStreamId) return false;
    if (!phrase.ownership) return true;
    if (message.output_id && message.output_id !== phrase.ownership.outputId) return false;
    if (typeof message.generation_epoch === 'number'
      && message.generation_epoch !== phrase.ownership.generationEpoch) return false;
    return true;
  };

  const handleControlMessage = (message: PcmControlEvent): void => {
    if (message.type === 'cancel_accepted' || message.type === 'cancelled') {
      const outputId = message.output_id ?? '';
      const generationEpoch = message.generation_epoch ?? 0;
      if (outputId) cancelledOutputKeys.add(outputKey(outputId, generationEpoch));
      const phrase = activePhrase;
      if (phrase?.ownership
        && phrase.ownership.outputId === outputId
        && phrase.ownership.generationEpoch === generationEpoch) {
        settleCancelledPhrase(phrase, message.reason ?? message.type);
      }
      reporter.record(`output_${message.type}`, {
        output_id: outputId,
        generation_epoch: generationEpoch,
        segment_id: message.segment_id,
        generated_through_frame: message.generated_through_frame,
      }, 'pcm_session');
      return;
    }
    const phrase = activePhrase;
    if (!phrase || phrase.completed) {
      reporter.record('unexpected_control_message', { ...message }, 'pcm_session');
      return;
    }
    if (!controlMatchesPhrase(message, phrase)) {
      reporter.record('phrase_stream_id_mismatch', {
        segment_id: phrase.segmentId,
        phrase_index: phrase.phraseIndex,
        expected_stream_id: phrase.phraseStreamId,
        received_stream_id: message.stream_id,
        expected_output_id: phrase.ownership?.outputId,
        received_output_id: message.output_id,
        expected_generation_epoch: phrase.ownership?.generationEpoch,
        received_generation_epoch: message.generation_epoch,
        control_type: message.type,
      }, 'pcm_session');
      return;
    }
    if (message.type === 'start' || message.type === 'format') {
      if (typeof message.sample_rate === 'number' && message.sample_rate > 0) {
        phrase.stats.sampleRate = message.sample_rate;
      }
      return;
    }
    if (message.type === 'error') {
      if (message.capacity_saturated) {
        reporter.record('phrase_capacity_saturated', {
          phrase_index: message.phrase_index,
          retry_after: message.retry_after,
        }, 'pcm_session');
      }
      failActivePhrase(new Error(message.message || 'Live voice phrase generation failed.'));
      return;
    }
    if (message.type !== 'done') return;

    phrase.completed = true;
    node.port.postMessage({ type: 'segment_end', segmentId: phrase.segmentId });
    const elapsedMs = performance.now() - phrase.startedAtMs;
    const audioMs = phrase.stats.receivedSamples * 1000 / Math.max(1, phrase.stats.sampleRate);
    reporter.record('phrase_buffered', {
      segment_id: phrase.segmentId,
      segment_kind: 'speech',
      phrase_index: phrase.phraseIndex,
      phrase_stream_id: phrase.phraseStreamId,
      output_id: phrase.ownership?.outputId ?? null,
      generation_epoch: phrase.ownership?.generationEpoch ?? null,
      text: phrase.text,
      text_length: phrase.text.length,
      partial: message.partial ?? false,
      frames: phrase.stats.frameCount,
      received_bytes: phrase.stats.receivedBytes,
      received_samples: phrase.stats.receivedSamples,
      playback_samples: phrase.stats.playbackSamples,
      sample_rate: audioContext.sampleRate,
      audio_ms: audioMs,
      elapsed_ms: elapsedMs,
      generation_rtf: audioMs > 0 ? elapsedMs / audioMs : null,
      first_frame_delay_ms: phrase.stats.firstFrameAtMs === null
        ? null
        : phrase.stats.firstFrameAtMs - phrase.startedAtMs,
    }, 'pcm_session');
    try {
      socket.send(JSON.stringify({
        type: 'diagnostic',
        stream_id: phrase.phraseStreamId,
        event: 'playback_finished',
        details: {
          completion_scope: phrase.ownership ? 'output_item_buffered' : 'phrase_buffered_into_live_turn',
          segment_id: phrase.segmentId,
          phrase_index: phrase.phraseIndex,
          output_id: phrase.ownership?.outputId,
          generation_epoch: phrase.ownership?.generationEpoch,
          frames: phrase.stats.frameCount,
          received_samples: phrase.stats.receivedSamples,
          playback_samples: phrase.stats.playbackSamples,
          sample_rate: audioContext.sampleRate,
          audio_ms: audioMs,
          elapsed_ms: elapsedMs,
        },
      }));
    } catch {
      // The separate live-call log still records completion.
    }
    activePhrase = null;
    phrase.resolve();
  };

  socket.addEventListener('open', () => {
    if (closed) {
      socket.close(1000, 'session-closed');
      return;
    }
    socketOpened = true;
    reporter.record('session_websocket_opened', {
      websocket_path: TTS_LIVE_CALL_WEBSOCKET_PATH,
      turn_elapsed_ms: performance.now() - startedAtMs,
    }, 'pcm_session');
    socketReadyResolve?.();
  }, { once: true });

  socket.addEventListener('message', (event: MessageEvent<string | ArrayBuffer>) => {
    if (closed) return;
    if (event.data instanceof ArrayBuffer) {
      handleBinaryFrame(event.data);
      return;
    }
    const message = parseControlEvent(event.data);
    if (message) handleControlMessage(message);
  });

  socket.addEventListener('error', () => {
    const error = new Error('Live voice session WebSocket failed.');
    socketFailure = error;
    if (!socketOpened) socketReadyReject?.(error);
    failActivePhrase(error);
  });

  socket.addEventListener('close', (event: CloseEvent) => {
    reporter.record('session_websocket_closed', {
      close_code: event.code,
      close_reason: event.reason,
      clean: event.wasClean,
      opened: socketOpened,
      session_closed: closed,
    }, 'pcm_session');
    if (closed) return;
    const error = new Error('Live voice session WebSocket closed before completion.');
    socketFailure = error;
    if (!socketOpened) socketReadyReject?.(error);
    failActivePhrase(error);
  });

  const streamPhrase = async (
    text: string,
    phraseIndex: number,
    synthesisOptions: SpeechSynthesisOptions,
    ownership: LiveOutputOwnership | null,
  ): Promise<void> => {
    if (closed) throw new Error('Live voice PCM session is closed.');
    await socketReady;
    if (closed) throw new Error('Live voice PCM session is closed.');
    if (socketFailure) throw socketFailure;
    if (socket.readyState !== WEBSOCKET_OPEN) throw new Error('Live voice session WebSocket is not open.');
    if (activePhrase) throw new Error('Live voice phrase generation is already active.');
    if (ownership && cancelledOutputKeys.has(outputKey(ownership.outputId, ownership.generationEpoch))) return;

    const phraseStartedAtMs = performance.now();
    const phraseStreamId = createPhraseStreamId(traceId, phraseIndex, ownership);
    const segmentId = createSpeechSegmentId(traceId, phraseIndex);
    return new Promise<void>((resolve, reject) => {
      activePhrase = {
        text,
        phraseIndex,
        phraseStreamId,
        segmentId,
        ownership,
        startedAtMs: phraseStartedAtMs,
        completed: false,
        cancelled: false,
        resolve,
        reject,
        stats: {
          frameCount: 0,
          receivedBytes: 0,
          receivedSamples: 0,
          playbackSamples: 0,
          firstFrameAtMs: null,
          lastFrameAtMs: null,
          sampleRate: REQUESTED_SAMPLE_RATE,
        },
      };
      reporter.record('phrase_request_sent', {
        segment_id: segmentId,
        phrase_index: phraseIndex,
        phrase_stream_id: phraseStreamId,
        output_id: ownership?.outputId ?? null,
        generation_epoch: ownership?.generationEpoch ?? null,
        output_order: ownership?.outputOrder ?? null,
        text_length: text.length,
        performance_schema_version: synthesisOptions.performancePlan?.schema_version ?? null,
        pronunciation_count: synthesisOptions.pronunciationLexicon?.length ?? 0,
        turn_elapsed_ms: phraseStartedAtMs - startedAtMs,
        websocket_reused: true,
      }, 'pcm_session');
      try {
        socket.send(JSON.stringify({
          type: 'synthesize',
          request_id: phraseStreamId,
          phrase_index: phraseIndex,
          segment_id: segmentId,
          output_id: ownership?.outputId,
          generation_epoch: ownership?.generationEpoch ?? 0,
          output_order: ownership?.outputOrder,
          text,
          speaker: voiceId,
          language: 'English',
          chunk_size: TTS_CHUNK_SIZE,
          temperature: 0.6,
          top_k: 20,
          top_p: 0.85,
          repetition_penalty: 1.0,
          append_silence: false,
          non_streaming_mode: false,
          parity_mode: true,
          diagnostics_stream_id: phraseStreamId,
          delivery_plan: synthesisOptions.performancePlan,
          pronunciation_lexicon: synthesisOptions.pronunciationLexicon ?? [],
        }));
      } catch (error) {
        failActivePhrase(error instanceof Error ? error : new Error(String(error)));
      }
    });
  };

  const enqueueOutputPhrase = (
    text: string,
    phraseIndex: number,
    ownership: LiveOutputOwnership,
    synthesisOptions?: SpeechSynthesisOptions,
  ): Promise<void> => {
    if (closed || inputFinished) return Promise.reject(new Error('Live voice input is already closed.'));
    const key = outputKey(ownership.outputId, ownership.generationEpoch);
    if (queuedOutputs.has(key)) return Promise.reject(new Error('Live output epoch is already queued.'));
    const queued: QueuedOutput = { ownership, cancelled: false };
    queuedOutputs.set(key, queued);
    const resolvedOptions = synthesisOptions ?? createLiveSpeechSynthesisOptions(text);
    reporter.record('output_generation_queued', {
      output_id: ownership.outputId,
      generation_epoch: ownership.generationEpoch,
      output_order: ownership.outputOrder,
      phrase_index: phraseIndex,
      text_length: text.length,
    }, 'pcm_session');
    const task = generationQueue.catch(() => undefined).then(async () => {
      if (queued.cancelled || cancelledOutputKeys.has(key)) return;
      void resumeAudioContext('output_generation_started');
      await streamPhrase(text, phraseIndex, resolvedOptions, ownership);
    }).finally(() => {
      queuedOutputs.delete(key);
    });
    generationQueue = task;
    return task;
  };

  const enqueuePhrase = (
    text: string,
    phraseIndex: number,
    synthesisOptions?: SpeechSynthesisOptions,
  ): Promise<void> => {
    if (closed || inputFinished) return Promise.reject(new Error('Live voice input is already closed.'));
    const resolvedOptions = synthesisOptions ?? createLiveSpeechSynthesisOptions(text);
    reporter.record('phrase_generation_queued', {
      phrase_index: phraseIndex,
      text,
      text_length: text.length,
      performance_schema_version: resolvedOptions.performancePlan?.schema_version ?? null,
      pronunciation_count: resolvedOptions.pronunciationLexicon?.length ?? 0,
    }, 'pcm_session');
    const task = generationQueue.catch(() => undefined).then(() => {
      void resumeAudioContext('phrase_generation_started');
      reporter.record('phrase_generation_started', {
        phrase_index: phraseIndex,
        text_length: text.length,
      }, 'pcm_session');
      return streamPhrase(text, phraseIndex, resolvedOptions, null);
    });
    generationQueue = task;
    return task;
  };

  const enqueueSilence = (
    durationMs: number,
    reason: SilenceReason,
    minimumFollowingSpeechMs = 120,
  ): Promise<void> => {
    if (closed || inputFinished) return Promise.reject(new Error('Live voice input is already closed.'));
    const durationSamples = millisecondsToPlaybackSamples(durationMs, audioContext.sampleRate);
    if (durationSamples <= 0) return Promise.resolve();
    const minimumFollowingSpeechSamples = millisecondsToPlaybackSamples(
      minimumFollowingSpeechMs,
      audioContext.sampleRate,
    );
    const segmentId = createSilenceSegmentId(traceId, silenceSequence);
    silenceSequence += 1;
    const task = generationQueue.catch(() => undefined).then(() => {
      node.port.postMessage({
        type: 'push_segment_silence',
        segmentId,
        durationSamples,
        minimumFollowingSpeechSamples,
        reason,
      });
      reporter.record('silence_segment_queued', {
        segment_id: segmentId,
        segment_kind: 'silence',
        duration_samples: durationSamples,
        duration_ms: durationSamples * 1000 / audioContext.sampleRate,
        minimum_following_speech_samples: minimumFollowingSpeechSamples,
        minimum_following_speech_ms: minimumFollowingSpeechMs,
        sample_rate: audioContext.sampleRate,
        reason,
      }, 'pcm_session');
    });
    generationQueue = task;
    return task;
  };

  const enqueueCue = (
    cueId: LiveVoiceCueId,
    variantId: string,
    gainValue = 0.68,
    allowProceduralFallback = false,
  ): Promise<void> => {
    if (closed || inputFinished) return Promise.reject(new Error('Live voice input is already closed.'));
    const segmentId = createCueSegmentId(traceId, cueId, cueSequence);
    cueSequence += 1;
    const task = generationQueue.catch(() => undefined).then(() => {
      const resolution = resolveCueSamples(cueId, variantId, audioContext.sampleRate, {
        voiceId,
        allowProceduralFallback,
      });
      if (!resolution) {
        reporter.record('cue_segment_skipped', {
          segment_id: segmentId,
          segment_kind: 'cue',
          cue_id: cueId,
          variant_id: variantId,
          voice_id: voiceId,
          reason: 'voice_asset_unavailable',
          procedural_fallback_allowed: allowProceduralFallback,
          semantic_speech_samples: 0,
        }, 'pcm_session');
        return;
      }
      const samples = resolution.samples;
      const gain = Math.max(0, Math.min(1, gainValue));
      if (gain !== 1) {
        for (let index = 0; index < samples.length; index += 1) samples[index] *= gain;
      }
      totalPlaybackSamples += samples.length;
      node.port.postMessage({
        type: 'push_segment_samples',
        segmentId,
        segmentKind: 'cue',
        cueId,
        variantId,
        samples,
      }, [samples.buffer]);
      node.port.postMessage({ type: 'segment_end', segmentId });
      reporter.record('cue_segment_queued', {
        segment_id: segmentId,
        segment_kind: 'cue',
        cue_id: cueId,
        variant_id: variantId,
        voice_id: voiceId,
        cue_source: resolution.source,
        source_sample_rate: resolution.sourceSampleRate,
        playback_samples: samples.length,
        sample_rate: audioContext.sampleRate,
        semantic_speech_samples: 0,
      }, 'pcm_session');
    });
    generationQueue = task;
    return task;
  };

  const cancelSegment = (segmentId: string, reason = 'cancelled'): void => {
    if (closed) return;
    node.port.postMessage({ type: 'cancel_segment', segmentId, reason });
  };

  const cancelOutputItem = async (
    outputId: string,
    generationEpoch: number,
    reason = 'cancelled',
  ): Promise<void> => {
    if (closed) return;
    const key = outputKey(outputId, generationEpoch);
    cancelledOutputKeys.add(key);
    const queued = queuedOutputs.get(key);
    if (queued) queued.cancelled = true;
    node.port.postMessage({ type: 'cancel_output', outputId, generationEpoch, reason });
    if (socket.readyState === WEBSOCKET_OPEN) {
      socket.send(JSON.stringify({
        type: 'cancel',
        output_id: outputId,
        generation_epoch: generationEpoch,
        segment_id: activePhrase?.ownership?.outputId === outputId ? activePhrase.segmentId : undefined,
        reason,
      }));
    }
    const phrase = activePhrase;
    if (phrase?.ownership
      && phrase.ownership.outputId === outputId
      && phrase.ownership.generationEpoch === generationEpoch) {
      settleCancelledPhrase(phrase, reason);
    }
    settleOutput(key);
  };

  const cancelAllAfter = (outputOrder: number, reason = 'cancelled_after'): void => {
    if (closed) return;
    for (const [key, queued] of queuedOutputs) {
      if (queued.ownership.outputOrder <= outputOrder) continue;
      queued.cancelled = true;
      cancelledOutputKeys.add(key);
      if (socket.readyState === WEBSOCKET_OPEN) {
        socket.send(JSON.stringify({
          type: 'cancel',
          output_id: queued.ownership.outputId,
          generation_epoch: queued.ownership.generationEpoch,
          reason,
        }));
      }
    }
    node.port.postMessage({ type: 'cancel_all_after', outputOrder, reason });
  };

  const waitForOutputItem = (outputId: string, generationEpoch: number): Promise<void> => {
    const key = outputKey(outputId, generationEpoch);
    if (terminalOutputKeys.has(key) || cancelledOutputKeys.has(key)) return Promise.resolve();
    return new Promise<void>((resolve) => {
      const waiters = outputWaiters.get(key) ?? new Set<() => void>();
      waiters.add(resolve);
      outputWaiters.set(key, waiters);
    });
  };

  const setStartPolicy = (policy: Partial<PlaybackStartPolicyMs>): void => {
    if (closed) return;
    node.port.postMessage({
      type: 'set_start_policy',
      notBeforeRenderSample: millisecondsToPlaybackSamples(
        policy.notBeforeMs ?? 0,
        audioContext.sampleRate,
      ),
      minimumBufferedSpeechSamples: millisecondsToPlaybackSamples(
        policy.minimumBufferedSpeechMs ?? START_BUFFER_SECONDS * 1000,
        audioContext.sampleRate,
      ),
    });
  };

  const waitForDrain = async (): Promise<void> => {
    if (drained) return;
    await Promise.race([
      drainPromise,
      new Promise<never>((_, reject) => {
        window.setTimeout(() => reject(new Error('Live voice playback drain timed out.')), DRAIN_TIMEOUT_MS);
      }),
    ]);
  };

  const terminateSession = async (reason: string, workletAlreadyDrained = false): Promise<void> => {
    if (closed) return;
    closed = true;
    [...outputWaiters.keys()].forEach(settleOutput);
    document.removeEventListener('visibilitychange', handlePlaybackResumeSignal);
    window.removeEventListener('focus', handlePlaybackResumeSignal);
    window.removeEventListener('pageshow', handlePlaybackResumeSignal);
    if (!workletAlreadyDrained) node.port.postMessage({ type: 'stop', reason });
    node.port.onmessage = null;
    node.disconnect();
    if (socket.readyState === WEBSOCKET_OPEN) {
      try { socket.send(JSON.stringify({ type: 'close', reason })); } catch { /* ignore close send */ }
      socket.close(1000, reason);
    } else {
      try { socket.close(); } catch { /* ignore close failures */ }
    }
    clearCallAffinityCookie(options.affinityKey, affinityCookieValue);
    await audioContext.close().catch((error: unknown) => {
      reporter.record('audio_context_close_failed', {
        error: error instanceof Error ? error.message : String(error),
      }, 'pcm_session');
    });
  };

  const finish = async (): Promise<void> => {
    if (closed || inputFinished) return;
    let observedQueue = generationQueue;
    while (true) {
      await observedQueue.catch(() => undefined);
      await Promise.resolve();
      if (generationQueue === observedQueue) break;
      observedQueue = generationQueue;
    }
    if (closed || inputFinished) return;
    inputFinished = true;
    await generationQueue.catch(() => undefined);
    if (closed) return;
    node.port.postMessage({ type: 'end' });
    await waitForDrain();
    reporter.record('turn_playback_drained', {
      total_frames: totalFrames,
      total_received_samples: totalReceivedSamples,
      total_playback_samples: totalPlaybackSamples,
      semantic_speech_samples: totalReceivedSamples * audioContext.sampleRate / REQUESTED_SAMPLE_RATE,
      underruns,
      resumes,
      elapsed_ms: performance.now() - startedAtMs,
    }, 'pcm_session');
    await terminateSession('turn-finished', true);
  };

  const stop = async (reason = 'stopped'): Promise<void> => {
    if (closed) return;
    inputFinished = true;
    const phrase = activePhrase;
    if (phrase && !phrase.completed) settleCancelledPhrase(phrase, reason);
    await terminateSession(reason);
  };

  return {
    sampleRate: audioContext.sampleRate,
    enqueuePhrase,
    enqueueOutputPhrase,
    enqueueSilence,
    enqueueCue,
    cancelSegment,
    cancelOutputItem,
    cancelAllAfter,
    waitForOutputItem,
    setStartPolicy,
    finish,
    stop,
    isClosed: () => closed,
  };
}

function ttsWebSocketUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${TTS_LIVE_CALL_WEBSOCKET_PATH}`;
}

function setCallAffinityCookie(affinityKey: string | undefined): string | null {
  if (!affinityKey) return null;
  const value = encodeURIComponent(affinityKey);
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `omnix_call_affinity=${value}; Path=/; SameSite=Lax${secure}`;
  return value;
}

function clearCallAffinityCookie(affinityKey: string | undefined, value: string | null): void {
  if (!affinityKey || !value) return;
  const stored = document.cookie.split(';').map((part) => part.trim())
    .find((part) => part.startsWith('omnix_call_affinity='))
    ?.slice('omnix_call_affinity='.length);
  if (stored !== value) return;
  const secure = window.location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `omnix_call_affinity=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
}

function createPhraseStreamId(
  traceId: string,
  phraseIndex: number,
  ownership: LiveOutputOwnership | null = null,
): string {
  const base = traceId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-56) || 'live';
  const suffix = ownership
    ? `${ownership.outputId.replace(/[^A-Za-z0-9_.-]+/g, '-').slice(-24)}-e${ownership.generationEpoch}`
    : `p${phraseIndex}`;
  return `chat-live-${base}-${suffix}`.slice(0, 80);
}

function parseControlEvent(data: string): PcmControlEvent | null {
  return parseJson(pcmControlEventSchema, data);
}

function pcm16ToFloat32(source: Int16Array, sourceRate: number, targetRate: number): Float32Array {
  if (!source.length) return new Float32Array();
  const normalized = new Float32Array(source.length);
  for (let index = 0; index < source.length; index += 1) normalized[index] = source[index] / 32768;
  if (sourceRate === targetRate) return normalized;
  const ratio = sourceRate / targetRate;
  const outputLength = Math.max(1, Math.round(normalized.length / ratio));
  const output = new Float32Array(outputLength);
  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = index * ratio;
    const lower = Math.floor(sourcePosition);
    const upper = Math.min(normalized.length - 1, lower + 1);
    const fraction = sourcePosition - lower;
    output[index] = normalized[lower] * (1 - fraction) + normalized[upper] * fraction;
  }
  return output;
}
