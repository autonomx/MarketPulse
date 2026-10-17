import { waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { assistantPcmStream, stopAssistantPcmStream, toggleAssistantPcmStream } from './assistant-pcm-stream-websocket-player';

type SocketListener = (event: Event | MessageEvent) => void;

class FakeMessagePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  messages: unknown[] = [];
  start = vi.fn();
  close = vi.fn();

  postMessage(message: unknown): void {
    this.messages.push(message);
    const messageType = (message as { type?: string })?.type;
    if (messageType === 'push') {
      const samples = (message as { samples?: Float32Array }).samples;
      queueMicrotask(() => this.onmessage?.({
        data: {
          type: 'buffered',
          incoming_samples: samples?.length ?? 0,
          buffered_samples: samples?.length ?? 0,
        },
      } as MessageEvent));
    }
    if (messageType === 'end') {
      queueMicrotask(() => this.onmessage?.({ data: { type: 'input_ended', buffered_samples: 0 } } as MessageEvent));
      queueMicrotask(() => this.onmessage?.({ data: { type: 'drained', buffered_samples: 0 } } as MessageEvent));
    }
  }

  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  dispatchEvent = vi.fn().mockReturnValue(true);
}

class FakeAudioWorkletNode {
  static nodes: FakeAudioWorkletNode[] = [];
  readonly port = new FakeMessagePort();
  readonly options: AudioWorkletNodeOptions;
  connect = vi.fn();
  disconnect = vi.fn();

  constructor(
    _context: BaseAudioContext,
    _name: string,
    options: AudioWorkletNodeOptions = {},
  ) {
    this.options = options;
    FakeAudioWorkletNode.nodes.push(this);
  }
}

class FakeAudioContext {
  static contexts: FakeAudioContext[] = [];
  state: AudioContextState = 'running';
  sampleRate = 24_000;
  baseLatency = 0.01;
  outputLatency = 0.02;
  destination = {} as AudioDestinationNode;
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  resume = vi.fn().mockResolvedValue(undefined);
  close = vi.fn().mockResolvedValue(undefined);

  constructor() {
    FakeAudioContext.contexts.push(this);
  }

  createBufferSource(): AudioBufferSourceNode {
    throw new Error('continuous AudioWorklet playback must not schedule AudioBufferSourceNode blocks');
  }
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static frames: ArrayBuffer[] = [];

  readonly url: string;
  binaryType: BinaryType = 'blob';
  bufferedAmount = 0;
  readyState = 0;
  sent: string[] = [];
  private listeners = new Map<string, SocketListener[]>();
  private streamStarted = false;

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
    const callback: SocketListener = typeof listener === 'function'
      ? listener as SocketListener
      : (event) => listener.handleEvent(event);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), callback]);
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    const text = String(data);
    this.sent.push(text);
    let message: { type?: string } = {};
    try { message = JSON.parse(text) as { type?: string }; } catch { /* no-op */ }
    if (message.type === 'diagnostic' || this.streamStarted) return;
    this.streamStarted = true;
    queueMicrotask(() => {
      this.emit('message', {
        data: JSON.stringify({
          type: 'start',
          stream_id: 'server-stream-id',
          sample_rate: 24_000,
          diagnostics_log: 'resources/logs/tts-streaming.log',
        }),
      } as MessageEvent);
      FakeWebSocket.frames.forEach((frame) => {
        this.emit('message', { data: frame } as MessageEvent);
      });
      this.emit('message', { data: JSON.stringify({ type: 'done', stream_id: 'server-stream-id' }) } as MessageEvent);
    });
  }

  close(code = 1000, reason = ''): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit('close', {
      code,
      reason,
      wasClean: true,
    } as CloseEvent);
  }

  parsedMessages(): Array<Record<string, unknown>> {
    return this.sent.map((value) => JSON.parse(value) as Record<string, unknown>);
  }

  private emit(type: string, event: Event | MessageEvent): void {
    (this.listeners.get(type) ?? []).forEach((listener) => listener(event));
  }
}

function streamReply(voiceId: string | null = 'ari-clone'): void {
  void toggleAssistantPcmStream('message-1', 'Stream this reply.', voiceId);
}

function finished(): void {
  expect(assistantPcmStream.getState()).toEqual({ messageId: null, status: 'Streaming response audio finished.' });
}

function pcmFrame(samples: Int16Array): ArrayBuffer {
  return samples.buffer.slice(samples.byteOffset, samples.byteOffset + samples.byteLength) as ArrayBuffer;
}

function installAudioFakes(frames: ArrayBuffer[]): void {
  FakeWebSocket.frames = frames;
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext);
  vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode as unknown as typeof AudioWorkletNode);
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
}

afterEach(() => {
  stopAssistantPcmStream();
  FakeAudioContext.contexts = [];
  FakeAudioWorkletNode.nodes = [];
  FakeWebSocket.instances = [];
  FakeWebSocket.frames = [];
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('assistant PCM stream player', () => {
  it('correlates binary websocket PCM with structured diagnostics', async () => {
    installAudioFakes([pcmFrame(new Int16Array([0, 0]))]);

    streamReply();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const [socket] = FakeWebSocket.instances;
    await waitFor(finished);
    expect(new URL(socket.url).pathname).toBe('/api/tts/stream/websocket');
    expect(socket.binaryType).toBe('arraybuffer');
    const messages = socket.parsedMessages();
    const requestBody = messages.find((message) => message.type !== 'diagnostic');
    expect(requestBody).toMatchObject({
      text: 'Stream this reply.',
      speaker: 'ari-clone',
      chunk_size: 8,
      non_streaming_mode: false,
      parity_mode: true,
    });
    expect(requestBody).toHaveProperty('diagnostics_stream_id');
    expect(requestBody).not.toHaveProperty('max_new_tokens');
    const diagnosticEvents = messages
      .filter((message) => message.type === 'diagnostic')
      .map((message) => message.event);
    expect(diagnosticEvents).toContain('websocket_opened');
    expect(diagnosticEvents).toContain('network_frame_received');
    expect(diagnosticEvents).toContain('worklet_buffered');
    expect(diagnosticEvents).toContain('playback_finished');
  });

  it('publishes streaming PCM timing for avatar mouth animation', async () => {
    installAudioFakes([pcmFrame(new Int16Array([1_000, -1_000]))]);
    const avatarPcmEvents: CustomEvent[] = [];
    const onAvatarPcm = (event: Event) => avatarPcmEvents.push(event as CustomEvent);
    window.addEventListener('omnix:character-avatar-pcm', onAvatarPcm);

    try {
      streamReply();

      await waitFor(finished);
      expect(avatarPcmEvents).toHaveLength(1);
      expect(avatarPcmEvents[0].detail.samples).toBeInstanceOf(Int16Array);
      expect(Array.from(avatarPcmEvents[0].detail.samples)).toEqual([1_000, -1_000]);
      expect(avatarPcmEvents[0].detail.sampleRate).toBe(24_000);
      expect(avatarPcmEvents[0].detail.startDelayMs).toBe(400);
    } finally {
      window.removeEventListener('omnix:character-avatar-pcm', onAvatarPcm);
    }
  });

  it('sends the voice Chat chose (the Character Mode speaker) through the PCM websocket', async () => {
    installAudioFakes([pcmFrame(new Int16Array([0, 0]))]);

    streamReply('Inigo');

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const [socket] = FakeWebSocket.instances;
    await waitFor(finished);
    const requestBody = socket.parsedMessages().find((message) => message.type !== 'diagnostic');
    expect(requestBody).toMatchObject({
      speaker: 'Inigo',
      text: 'Stream this reply.',
    });
  });

  it('keeps the adaptive AudioWorklet startup and recovery reserves', async () => {
    installAudioFakes(Array.from({ length: 8 }, () => pcmFrame(new Int16Array(2_400))));

    streamReply();

    await waitFor(() => expect(FakeAudioWorkletNode.nodes).toHaveLength(1));
    const [node] = FakeAudioWorkletNode.nodes;
    expect(node.options.processorOptions).toMatchObject({
      startBufferSamples: 9_600,
      rebufferSamples: 18_000,
      maxRebufferSamples: 36_000,
      transitionFadeSamples: 192,
    });
    expect(node.connect).toHaveBeenCalledTimes(1);
    expect(FakeAudioContext.contexts[0].audioWorklet.addModule).toHaveBeenCalledTimes(1);
  });

  it('marks the streaming message and stops it on a second press', async () => {
    installAudioFakes([]);
    vi.spyOn(FakeWebSocket.prototype, 'send').mockImplementation(() => undefined);

    streamReply();
    expect(assistantPcmStream.getState()).toEqual({ messageId: 'message-1', status: 'Buffering streaming response audio…' });
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    streamReply();

    expect(assistantPcmStream.getState()).toEqual({ messageId: null, status: 'Streaming response audio stopped.' });
  });

  it('posts every binary PCM sample to the continuous queue in exact order', async () => {
    installAudioFakes([
      pcmFrame(new Int16Array([1_000, -1_000])),
      pcmFrame(new Int16Array([2_000, -2_000])),
    ]);

    streamReply();

    await waitFor(finished);
    const pushMessages = FakeAudioWorkletNode.nodes[0].port.messages
      .filter((message) => (message as { type?: string }).type === 'push') as Array<{
        type: string;
        samples: Float32Array;
      }>;
    expect(pushMessages).toHaveLength(2);
    expect(Array.from(pushMessages[0].samples)).toEqual([
      1_000 / 32_768,
      -1_000 / 32_768,
    ]);
    expect(Array.from(pushMessages[1].samples)).toEqual([
      2_000 / 32_768,
      -2_000 / 32_768,
    ]);
  });
});
