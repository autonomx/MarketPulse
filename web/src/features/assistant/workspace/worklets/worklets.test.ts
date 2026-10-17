import { afterEach, describe, expect, it, vi } from 'vitest';

import { loadWorkletProcessor } from '../../../../test/audioWorklet';

type Message = Record<string, unknown> & { type?: string };
type Port = { onmessage: ((event: MessageEvent<Message>) => void) | null; postMessage: ReturnType<typeof vi.fn> };
type Processor = { port: Port; process: (inputs: Float32Array[][], outputs: Float32Array[][]) => boolean };

class FakeAudioWorkletProcessor {
  readonly port: Port = { onmessage: null, postMessage: vi.fn() };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function send(processor: Processor, data: Message): void {
  processor.port.onmessage?.({ data } as MessageEvent<Message>);
}

function types(processor: Processor): unknown[] {
  return processor.port.postMessage.mock.calls.map(([message]) => (message as Message).type);
}

describe('assistant PCM stream worklet', () => {
  it('buffers, plays and drains the stream it is sent', async () => {
    const Stream = await loadWorkletProcessor<new (options: object) => Processor>(
      () => import('./assistant-pcm-stream.worklet'),
      FakeAudioWorkletProcessor,
    );
    const processor = new Stream({ processorOptions: { startBufferSamples: 4, transitionFadeSamples: 1 } });
    send(processor, { type: 'push', samples: [0.5, 0.5, 0.5, 0.5] });
    send(processor, { type: 'end' });

    const output = new Float32Array(4);
    expect(processor.process([], [[output]])).toBe(false);
    expect(output[1]).toBe(0.5);
    expect(types(processor)).toEqual(['buffered', 'started', 'input_ended', 'drained']);
  });
});

describe('live voice capture worklet', () => {
  it('posts a transferable copy of each microphone quantum', async () => {
    const Capture = await loadWorkletProcessor<new () => Processor>(
      () => import('./live-voice-capture.worklet'),
      FakeAudioWorkletProcessor,
    );
    const processor = new Capture();
    const input = new Float32Array([0.1, 0.2]);

    expect(processor.process([[input]], [])).toBe(true);
    const [audio, transfer] = processor.port.postMessage.mock.calls[0] as [Float32Array, ArrayBuffer[]];
    expect(Array.from(audio)).toEqual([Math.fround(0.1), Math.fround(0.2)]);
    expect(audio).not.toBe(input);
    expect(transfer).toEqual([audio.buffer]);
  });
});
