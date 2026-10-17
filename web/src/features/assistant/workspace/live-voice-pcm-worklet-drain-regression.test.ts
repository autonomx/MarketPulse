import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { loadWorkletProcessor } from '../../../test/audioWorklet';

type WorkletMessage = Record<string, unknown> & { type?: string };
type WorkletPort = {
  onmessage: ((event: MessageEvent<WorkletMessage>) => void) | null;
  postMessage: ReturnType<typeof vi.fn>;
};
type ProcessorInstance = {
  port: WorkletPort;
  process: (inputs: unknown[], outputs: Float32Array[][]) => boolean;
};
type ProcessorConstructor = new (options: { processorOptions?: Record<string, number> }) => ProcessorInstance;

class FakeAudioWorkletProcessor {
  readonly port: WorkletPort = {
    onmessage: null,
    postMessage: vi.fn(),
  };
}

let Processor: ProcessorConstructor;

beforeEach(async () => {
  Processor = await loadWorkletProcessor<ProcessorConstructor>(
    () => import('./worklets/live-voice-pcm-stream.worklet'),
    FakeAudioWorkletProcessor,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function send(processor: ProcessorInstance, data: WorkletMessage): void {
  processor.port.onmessage?.({ data } as MessageEvent<WorkletMessage>);
}

function events(processor: ProcessorInstance, type: string): WorkletMessage[] {
  return processor.port.postMessage.mock.calls
    .map(([message]) => message as WorkletMessage)
    .filter((message) => message.type === type);
}

describe('live voice trailing pause drain regression', () => {
  it('drains after input ends even when a guarded pause has no following speech', () => {
    const processor = new Processor({
      processorOptions: {
        startBufferSamples: 4,
        minimumBufferedSpeechSamples: 4,
        notBeforeRenderSample: 0,
        rebufferSamples: 8,
        maxRebufferSamples: 16,
        transitionFadeSamples: 1,
      },
    });

    send(processor, {
      type: 'push_segment_samples',
      segmentId: 'speech-0',
      segmentKind: 'speech',
      phraseIndex: 0,
      samples: new Float32Array([0.2, 0.2, 0.2, 0.2]),
    });
    send(processor, { type: 'segment_end', segmentId: 'speech-0' });
    send(processor, {
      type: 'push_segment_silence',
      segmentId: 'trailing-pause',
      durationSamples: 4,
      minimumFollowingSpeechSamples: 4,
      reason: 'clause',
    });
    send(processor, { type: 'end' });

    const output = new Float32Array(128);
    const keepAlive = processor.process([], [[output]]);

    expect(keepAlive).toBe(false);
    expect(events(processor, 'drained')).toEqual([
      expect.objectContaining({
        buffered_samples: 0,
        buffered_speech_samples: 0,
        segment_timeline_samples: 8,
        semantic_speech_samples: 4,
      }),
    ]);
    expect(events(processor, 'pause_waiting_for_following_speech')).toHaveLength(0);
  });
});
