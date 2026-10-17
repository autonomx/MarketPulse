import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  clearVoiceCueAssets,
  registerVoiceCueSamples,
} from './live-voice-cue-bank';
import {
  createLiveVoicePcmSession,
  resolveWorkletPlaybackPerformanceTimeMs,
} from './live-voice-pcm-session';

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  messages: unknown[] = [];

  postMessage(message: unknown): void {
    this.messages.push(message);
    const type = (message as { type?: string })?.type;
    if (type === 'push_segment_samples') {
      const samples = (message as { samples?: Float32Array }).samples;
      queueMicrotask(() => this.onmessage?.({
        data: {
          type: 'buffered',
          sample_rate: FakeAudioContext.instances[0]?.sampleRate ?? 24_000,
          buffered_samples: samples?.length ?? 0,
          buffered_speech_samples: (message as { segmentKind?: string }).segmentKind === 'speech'
            ? samples?.length ?? 0
            : 0,
          incoming_samples: samples?.length ?? 0,
          semantic_speech_samples: 0,
        },
      } as MessageEvent));
    }
    if (type === 'end') {
      queueMicrotask(() => this.onmessage?.({
        data: {
          type: 'drained',
          sample_rate: FakeAudioContext.instances[0]?.sampleRate ?? 24_000,
          buffered_samples: 0,
          buffered_speech_samples: 0,
          semantic_speech_samples: 4,
        },
      } as MessageEvent));
    }
  }
}

class FakeAudioWorkletNode {
  static instances: FakeAudioWorkletNode[] = [];
  readonly port = new FakePort();
  readonly options: AudioWorkletNodeOptions;
  connect = vi.fn();
  disconnect = vi.fn();

  constructor(
    _context?: BaseAudioContext,
    _name?: string,
    options: AudioWorkletNodeOptions = {},
  ) {
    this.options = options;
    FakeAudioWorkletNode.instances.push(this);
  }
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  static nextSampleRate = 24_000;
  state: AudioContextState = 'running';
  sampleRate = FakeAudioContext.nextSampleRate;
  destination = {} as AudioDestinationNode;
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  resume = vi.fn(async () => {
    this.state = 'running';
  });
  close = vi.fn().mockResolvedValue(undefined);

  constructor() {
    FakeAudioContext.instances.push(this);
  }
}

type Listener = (event: Event | MessageEvent) => void;

type SentMessage = {
  type?: string;
  text?: string;
  phrase_index?: number;
  segment_id?: string;
  diagnostics_stream_id?: string;
  non_streaming_mode?: boolean;
  parity_mode?: boolean;
  delivery_plan?: unknown;
};

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readonly url: string;
  binaryType: BinaryType = 'blob';
  readyState = 0;
  sent: string[] = [];
  close = vi.fn((code = 1000, reason = '') => {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit('close', { code, reason, wasClean: true } as CloseEvent);
  });
  private listeners = new Map<string, Listener[]>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1;
      this.emit('open', new Event('open'));
    });
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject | null): void {
    if (!listener) return;
    const callback: Listener = typeof listener === 'function'
      ? listener as Listener
      : (event) => listener.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    const text = String(data);
    this.sent.push(text);
    let message: SentMessage = {};
    try { message = JSON.parse(text) as SentMessage; } catch { /* no-op */ }
    if (message.type !== 'synthesize') return;
    const streamId = message.diagnostics_stream_id;
    const phraseIndex = message.phrase_index;
    queueMicrotask(() => {
      this.emit('message', {
        data: JSON.stringify({
          type: 'start',
          stream_id: streamId,
          phrase_index: phraseIndex,
          sample_rate: 24_000,
        }),
      } as MessageEvent);
      this.emit('message', { data: new Int16Array([1_000, -1_000]).buffer } as MessageEvent);
      this.emit('message', {
        data: JSON.stringify({
          type: 'done',
          stream_id: streamId,
          phrase_index: phraseIndex,
          partial: false,
        }),
      } as MessageEvent);
    });
  }

  private emit(type: string, event: Event | MessageEvent): void {
    (this.listeners.get(type) ?? []).forEach((listener) => listener(event));
  }
}

const reporter = {
  traceId: 'live-call:s1:test',
  record: vi.fn(),
  flush: vi.fn(async () => undefined),
  close: vi.fn(async () => undefined),
};

beforeEach(() => {
  FakeAudioContext.instances = [];
  FakeAudioContext.nextSampleRate = 24_000;
  FakeAudioWorkletNode.instances = [];
  FakeWebSocket.instances = [];
  clearVoiceCueAssets();
  window.localStorage.clear();
  reporter.record.mockClear();
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext);
  vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode as unknown as typeof AudioWorkletNode);
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  clearVoiceCueAssets();
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice PCM session', () => {
  it('keeps the call-affinity cookie for the session lifetime', async () => {
    const session = await createLiveVoicePcmSession(
      'live-call:s1:test', 'Jinx', reporter,
      { sessionScoped: true, affinityKey: 'session:one' },
    );

    expect(document.cookie).toContain('omnix_call_affinity=session%3Aone');
    await session.stop('test-cleanup');
    expect(document.cookie).not.toContain('omnix_call_affinity=');
  });

  it('maps a delayed worklet event back to its audio output time', () => {
    expect(resolveWorkletPlaybackPerformanceTimeMs(
      { audio_context_time_seconds: 10 },
      {
        getOutputTimestamp: () => ({
          contextTime: 10.25,
          performanceTime: 5_000,
        }),
      },
      5_300,
    )).toBe(4_750);
  });

  it('keeps one AudioContext, worklet, and websocket while preserving segment identity', async () => {
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    const first = session.enqueuePhrase('First phrase.', 0);
    const second = session.enqueuePhrase('Second phrase.', 1);

    await Promise.all([first, second]);
    await session.finish();

    expect(session.sampleRate).toBe(24_000);
    expect(FakeAudioContext.instances).toHaveLength(1);
    expect(FakeAudioWorkletNode.instances).toHaveLength(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain('/api/tts/live-call/websocket');
    const sent = FakeWebSocket.instances[0].sent.map((message) => JSON.parse(message) as SentMessage);
    const requests = sent.filter((message) => message.type === 'synthesize');
    expect(requests).toHaveLength(2);
    expect(requests.map((request) => request.text)).toEqual(['First phrase.', 'Second phrase.']);
    expect(requests.map((request) => request.segment_id)).toEqual([
      'speech-live-call-s1-test-p0',
      'speech-live-call-s1-test-p1',
    ]);
    expect(requests[0].non_streaming_mode).toBe(false);
    expect(requests[0].parity_mode).toBe(true);
    expect(requests[0].diagnostics_stream_id).toContain('chat-live-');
    expect(sent.filter((message) => message.type === 'diagnostic')).toHaveLength(2);
    expect(sent.at(-1)?.type).toBe('close');

    const messages = FakeAudioWorkletNode.instances[0].port.messages as Record<string, unknown>[];
    const pushes = messages.filter((message) => message.type === 'push_segment_samples');
    expect(pushes).toHaveLength(2);
    expect(pushes.map((message) => message.phraseIndex)).toEqual([0, 1]);
    expect(pushes.map((message) => message.segmentId)).toEqual([
      'speech-live-call-s1-test-p0',
      'speech-live-call-s1-test-p1',
    ]);
    expect(messages.filter((message) => message.type === 'segment_end')).toEqual([
      { type: 'segment_end', segmentId: 'speech-live-call-s1-test-p0' },
      { type: 'segment_end', segmentId: 'speech-live-call-s1-test-p1' },
    ]);
    expect(messages.filter((message) => message.type === 'end')).toHaveLength(1);
    expect(FakeAudioWorkletNode.instances[0].options.processorOptions).toMatchObject({
      startBufferSamples: 9_600,
      minimumBufferedSpeechSamples: 9_600,
      notBeforeRenderSample: 0,
      rebufferSamples: 18_000,
      maxRebufferSamples: 36_000,
      transitionFadeSamples: 192,
    });
    expect(reporter.record).toHaveBeenCalledWith(
      'turn_playback_drained',
      expect.objectContaining({ total_frames: 2, underruns: 0 }),
      'pcm_session',
    );
  });

  it('converts onset and pause readiness from milliseconds in the actual playback domain', async () => {
    FakeAudioContext.nextSampleRate = 48_000;
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    session.setStartPolicy({ notBeforeMs: 100, minimumBufferedSpeechMs: 200 });
    await session.enqueueSilence(250, 'reflection', 120);
    await session.stop('test-cleanup');

    const messages = FakeAudioWorkletNode.instances[0].port.messages as Record<string, unknown>[];
    expect(messages).toContainEqual({
      type: 'set_start_policy',
      notBeforeRenderSample: 4_800,
      minimumBufferedSpeechSamples: 9_600,
    });
    expect(messages).toContainEqual({
      type: 'push_segment_silence',
      segmentId: 'silence-live-call-s1-test-s0',
      durationSamples: 12_000,
      minimumFollowingSpeechSamples: 5_760,
      reason: 'reflection',
    });
    expect(messages.some((message) => message.type === 'push_segment_samples')).toBe(false);
  });

  it('queues voice-matched cues in the worklet without canonical phrase identity', async () => {
    registerVoiceCueSamples({
      voiceId: 'Jinx',
      cueId: 'hmm',
      variantId: 'hmm-v2',
      samples: new Float32Array([0, 0.2, -0.2, 0]),
      sampleRate: 24_000,
    });
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    await session.enqueueCue('hmm', 'hmm-v2', 0.5);
    await session.stop('test-cleanup');

    const messages = FakeAudioWorkletNode.instances[0].port.messages as Record<string, unknown>[];
    const cuePush = messages.find((message) => message.type === 'push_segment_samples');
    expect(cuePush).toMatchObject({
      segmentId: 'cue-live-call-s1-test-hmm-c0',
      segmentKind: 'cue',
      cueId: 'hmm',
      variantId: 'hmm-v2',
    });
    expect(cuePush).not.toHaveProperty('phraseIndex');
    expect(messages).toContainEqual({
      type: 'segment_end',
      segmentId: 'cue-live-call-s1-test-hmm-c0',
    });
    expect(reporter.record).toHaveBeenCalledWith(
      'cue_segment_queued',
      expect.objectContaining({
        segment_kind: 'cue',
        cue_source: 'voice_asset',
        voice_id: 'Jinx',
        semantic_speech_samples: 0,
      }),
      'pcm_session',
    );
  });

  it('skips a missing response cue unless procedural fallback is enabled', async () => {
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    await session.enqueueCue('hmm', 'hmm-v1', 0.5);

    let messages = FakeAudioWorkletNode.instances[0].port.messages as Record<string, unknown>[];
    expect(messages.some((message) => message.type === 'push_segment_samples')).toBe(false);
    expect(reporter.record).toHaveBeenCalledWith(
      'cue_segment_skipped',
      expect.objectContaining({
        voice_id: 'Jinx',
        reason: 'voice_asset_unavailable',
        procedural_fallback_allowed: false,
      }),
      'pcm_session',
    );

    await session.enqueueCue('hmm', 'hmm-v1', 0.5, true);
    await session.stop('test-cleanup');
    messages = FakeAudioWorkletNode.instances[0].port.messages as Record<string, unknown>[];
    expect(messages.some((message) => message.type === 'push_segment_samples')).toBe(true);
    expect(reporter.record).toHaveBeenCalledWith(
      'cue_segment_queued',
      expect.objectContaining({ cue_source: 'procedural_fallback' }),
      'pcm_session',
    );
  });

  it('normalizes generated speech into the actual 48 kHz playback sample domain', async () => {
    FakeAudioContext.nextSampleRate = 48_000;
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    await session.enqueuePhrase('Resample this phrase.', 0);
    await session.finish();

    const messages = FakeAudioWorkletNode.instances[0].port.messages as Array<{
      type?: string;
      samples?: Float32Array;
    }>;
    const push = messages.find((message) => message.type === 'push_segment_samples');
    expect(push?.samples).toHaveLength(4);
    expect(FakeAudioWorkletNode.instances[0].options.processorOptions).toMatchObject({
      startBufferSamples: 19_200,
      minimumBufferedSpeechSamples: 19_200,
    });
    expect(reporter.record).toHaveBeenCalledWith(
      'phrase_buffered',
      expect.objectContaining({ playback_samples: 4, sample_rate: 48_000 }),
      'pcm_session',
    );
  });

  it('hands received PCM to the worklet before synchronous avatar consumers', async () => {
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    let workletWasQueuedAtAvatarDispatch = false;
    const handleAvatarPcm = () => {
      workletWasQueuedAtAvatarDispatch = FakeAudioWorkletNode.instances[0].port.messages
        .some((message) => (message as { type?: string }).type === 'push_segment_samples');
    };
    window.addEventListener('omnix:character-avatar-pcm', handleAvatarPcm, { once: true });

    await session.enqueuePhrase('Prioritize audible playback.', 0);
    await session.finish();

    expect(workletWasQueuedAtAvatarDispatch).toBe(true);
  });

  it('closes the turn websocket and sends a cancellation reason to the worklet', async () => {
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    const phrase = session.enqueuePhrase('Interrupt this phrase.', 0);
    await phrase;
    await session.stop('barge-in');

    expect(session.isClosed()).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].close).toHaveBeenCalledWith(1000, 'barge-in');
    expect(FakeAudioWorkletNode.instances[0].port.messages).toContainEqual({
      type: 'stop',
      reason: 'barge-in',
    });
    expect(FakeAudioWorkletNode.instances[0].disconnect).toHaveBeenCalledTimes(1);
    expect(FakeAudioContext.instances[0].close).toHaveBeenCalledTimes(1);
  });

  it('resumes playback audio after page visibility or focus changes', async () => {
    const session = await createLiveVoicePcmSession('live-call:s1:test', 'Jinx', reporter);
    const context = FakeAudioContext.instances[0];
    context.state = 'suspended';

    document.dispatchEvent(new Event('visibilitychange'));
    await vi.waitFor(() => expect(context.resume).toHaveBeenCalledTimes(1));

    await vi.waitFor(() => expect(reporter.record).toHaveBeenCalledWith(
      'audio_context_resume_checked',
      expect.objectContaining({
        reason: 'visibilitychange',
        audio_context_state_before: 'suspended',
        audio_context_state_after: 'running',
      }),
      'pcm_session',
    ));

    await session.stop('test-cleanup');
    context.state = 'suspended';
    window.dispatchEvent(new Event('focus'));
    await Promise.resolve();
    expect(context.resume).toHaveBeenCalledTimes(1);
  });
});
