import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createLiveVoicePcmSession } from './live-voice-pcm-session';

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  messages: Array<Record<string, unknown>> = [];

  postMessage(message: Record<string, unknown>): void {
    this.messages.push(message);
    if (message.type === 'end') {
      queueMicrotask(() => this.onmessage?.({
        data: {
          type: 'drained',
          sample_rate: 24_000,
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
  connect = vi.fn();
  disconnect = vi.fn();

  constructor() {
    FakeAudioWorkletNode.instances.push(this);
  }
}

class FakeAudioContext {
  state: AudioContextState = 'running';
  sampleRate = 24_000;
  destination = {} as AudioDestinationNode;
  audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
  resume = vi.fn(async () => undefined);
  close = vi.fn(async () => undefined);
}

type Listener = (event: Event | MessageEvent) => void;

type SentMessage = {
  type?: string;
  text?: string;
  diagnostics_stream_id?: string;
  phrase_index?: number;
};

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  binaryType: BinaryType = 'blob';
  readyState = 0;
  sent: string[] = [];
  private listeners = new Map<string, Listener[]>();

  constructor() {
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
    try { message = JSON.parse(text) as SentMessage; } catch { return; }
    if (message.type !== 'synthesize') return;
    queueMicrotask(() => {
      this.emit('message', {
        data: JSON.stringify({
          type: 'start',
          stream_id: message.diagnostics_stream_id,
          phrase_index: message.phrase_index,
          sample_rate: 24_000,
        }),
      } as MessageEvent);
      this.emit('message', { data: new Int16Array([1_000, -1_000]).buffer } as MessageEvent);
      this.emit('message', {
        data: JSON.stringify({
          type: 'done',
          stream_id: message.diagnostics_stream_id,
          phrase_index: message.phrase_index,
          partial: false,
        }),
      } as MessageEvent);
    });
  }

  close(): void {
    this.readyState = 3;
  }

  private emit(type: string, event: Event | MessageEvent): void {
    (this.listeners.get(type) ?? []).forEach((listener) => listener(event));
  }
}

const reporter = {
  traceId: 'live-call:queue-settlement',
  record: vi.fn(),
  flush: vi.fn(async () => undefined),
  close: vi.fn(async () => undefined),
};

beforeEach(() => {
  FakeAudioWorkletNode.instances = [];
  FakeWebSocket.instances = [];
  reporter.record.mockClear();
  vi.stubGlobal('AudioContext', FakeAudioContext as unknown as typeof AudioContext);
  vi.stubGlobal('AudioWorkletNode', FakeAudioWorkletNode as unknown as typeof AudioWorkletNode);
  vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('live voice PCM session finish queue settlement', () => {
  it('accepts a pause and phrase appended asynchronously while finish is waiting', async () => {
    const session = await createLiveVoicePcmSession('live-call:queue-settlement', 'Jinx', reporter);
    const first = session.enqueuePhrase('First phrase.', 0);
    const extension = first.then(async () => {
      await session.enqueueSilence(100, 'clause', 120);
      await session.enqueuePhrase('Second phrase.', 1);
    });

    const finishing = session.finish();
    await Promise.all([extension, finishing]);

    const sent = FakeWebSocket.instances[0].sent.map((message) => JSON.parse(message) as SentMessage);
    expect(sent.filter((message) => message.type === 'synthesize').map((message) => message.text)).toEqual([
      'First phrase.',
      'Second phrase.',
    ]);
    const workletMessages = FakeAudioWorkletNode.instances[0].port.messages;
    expect(workletMessages).toContainEqual(expect.objectContaining({
      type: 'push_segment_silence',
      reason: 'clause',
    }));
    expect(workletMessages.filter((message) => message.type === 'end')).toHaveLength(1);
    expect(reporter.record).not.toHaveBeenCalledWith(
      'phrase_generation_failed',
      expect.objectContaining({ error: 'Live voice input is already closed.' }),
      'pcm_session',
    );
  });
});
