// AudioWorkletGlobalScope: plays streamed assistant TTS PCM continuously,
// buffering before start and after underruns, and reports progress on its port.
import { ASSISTANT_PCM_STREAM_WORKLET_NAME } from './names';

declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: new (options: ProcessorOptions) => AudioWorkletProcessor): void;
declare const sampleRate: number;

type ProcessorOptions = { processorOptions?: Record<string, unknown> };
type PcmStreamMessage = { type?: string; samples?: Float32Array | ArrayLike<number> | ArrayBuffer };

class OmnixAssistantPcmStreamProcessor extends AudioWorkletProcessor {
  startBufferSamples: number;
  rebufferSamples: number;
  maxRebufferSamples: number;
  currentRebufferSamples: number;
  transitionFadeSamples: number;
  progressIntervalSamples: number;
  queue: Float32Array[] = [];
  headOffset = 0;
  queuedSamples = 0;
  started = false;
  waitingForBuffer = false;
  inputEnded = false;
  stopped = false;
  drained = false;
  fadeInRemaining = 0;
  underrunCount = 0;
  renderClockSamples = 0;
  playedSamples = 0;
  lastProgressSamples = 0;

  constructor(options: ProcessorOptions) {
    super();
    const settings = options.processorOptions || {};
    this.startBufferSamples = Math.max(1, Number(settings.startBufferSamples) || sampleRate * 0.4);
    this.rebufferSamples = Math.max(1, Number(settings.rebufferSamples) || sampleRate * 0.75);
    this.maxRebufferSamples = Math.max(
      this.rebufferSamples,
      Number(settings.maxRebufferSamples) || sampleRate * 1.5,
    );
    this.currentRebufferSamples = this.rebufferSamples;
    this.transitionFadeSamples = Math.max(
      1,
      Number(settings.transitionFadeSamples) || Math.round(sampleRate * 0.008),
    );
    this.progressIntervalSamples = Math.max(128, Math.round(sampleRate * 0.5));
    this.port.onmessage = (event: MessageEvent<PcmStreamMessage | null>) => {
      const message = event.data || {};
      if (message.type === 'push' && message.samples) {
        const samples = message.samples instanceof Float32Array
          ? message.samples
          : new Float32Array(message.samples);
        if (samples.length > 0) {
          this.queue.push(samples);
          this.queuedSamples += samples.length;
          this.port.postMessage({
            type: 'buffered',
            buffered_samples: this.queuedSamples,
            incoming_samples: samples.length,
            target_samples: this.waitingForBuffer ? this.currentRebufferSamples : this.startBufferSamples,
            waiting_for_buffer: this.waitingForBuffer,
            input_ended: this.inputEnded,
            underrun_count: this.underrunCount,
            render_clock_samples: this.renderClockSamples,
            played_samples: this.playedSamples,
          });
          this.maybeStartOrResume();
        }
        return;
      }
      if (message.type === 'end') {
        this.inputEnded = true;
        this.port.postMessage({
          type: 'input_ended',
          buffered_samples: this.queuedSamples,
          waiting_for_buffer: this.waitingForBuffer,
          underrun_count: this.underrunCount,
          render_clock_samples: this.renderClockSamples,
          played_samples: this.playedSamples,
        });
        this.maybeStartOrResume();
        return;
      }
      if (message.type === 'stop') {
        this.stopped = true;
        this.port.postMessage({
          type: 'stopped',
          buffered_samples: this.queuedSamples,
          render_clock_samples: this.renderClockSamples,
          played_samples: this.playedSamples,
          underrun_count: this.underrunCount,
        });
      }
    };
  }

  beginFadeIn(): void {
    this.fadeInRemaining = this.transitionFadeSamples;
  }

  maybeStartOrResume(): void {
    if (!this.started && (this.queuedSamples >= this.startBufferSamples || (this.inputEnded && this.queuedSamples > 0))) {
      this.started = true;
      this.waitingForBuffer = false;
      this.beginFadeIn();
      this.port.postMessage({
        type: 'started',
        buffered_samples: this.queuedSamples,
        render_clock_samples: this.renderClockSamples,
        played_samples: this.playedSamples,
      });
      return;
    }
    if (
      this.started
      && this.waitingForBuffer
      && (this.queuedSamples >= this.currentRebufferSamples || this.inputEnded)
    ) {
      this.waitingForBuffer = false;
      this.beginFadeIn();
      this.port.postMessage({
        type: 'resumed',
        buffered_samples: this.queuedSamples,
        target_samples: this.currentRebufferSamples,
        underrun_count: this.underrunCount,
        render_clock_samples: this.renderClockSamples,
        played_samples: this.playedSamples,
      });
    }
  }

  applyFadeIn(channel: Float32Array, written: number): void {
    let index = 0;
    while (index < written && this.fadeInRemaining > 0) {
      const elapsed = this.transitionFadeSamples - this.fadeInRemaining + 1;
      const progress = Math.min(1, elapsed / this.transitionFadeSamples);
      const gain = 0.5 * (1 - Math.cos(Math.PI * progress));
      channel[index] *= gain;
      this.fadeInRemaining -= 1;
      index += 1;
    }
  }

  applyFadeOut(channel: Float32Array, written: number): void {
    const fadeSamples = Math.min(written, this.transitionFadeSamples);
    const start = written - fadeSamples;
    for (let index = 0; index < fadeSamples; index += 1) {
      const progress = (index + 1) / fadeSamples;
      const gain = 0.5 * (1 + Math.cos(Math.PI * progress));
      channel[start + index] *= gain;
    }
  }

  beginRebuffering(): void {
    this.waitingForBuffer = true;
    this.underrunCount += 1;
    const multiplier = 1 + (Math.max(0, this.underrunCount - 1) * 0.5);
    this.currentRebufferSamples = Math.min(
      this.maxRebufferSamples,
      Math.round(this.rebufferSamples * multiplier),
    );
    this.port.postMessage({
      type: 'underrun',
      buffered_samples: this.queuedSamples,
      target_samples: this.currentRebufferSamples,
      underrun_count: this.underrunCount,
      render_clock_samples: this.renderClockSamples,
      played_samples: this.playedSamples,
      input_ended: this.inputEnded,
    });
  }

  signalDrained(): boolean {
    if (!this.drained) {
      this.drained = true;
      this.port.postMessage({
        type: 'drained',
        buffered_samples: this.queuedSamples,
        underrun_count: this.underrunCount,
        render_clock_samples: this.renderClockSamples,
        played_samples: this.playedSamples,
      });
    }
    return false;
  }

  maybeReportProgress(): void {
    if (this.renderClockSamples - this.lastProgressSamples < this.progressIntervalSamples) return;
    this.lastProgressSamples = this.renderClockSamples;
    this.port.postMessage({
      type: 'render_progress',
      buffered_samples: this.queuedSamples,
      target_samples: this.waitingForBuffer ? this.currentRebufferSamples : 0,
      waiting_for_buffer: this.waitingForBuffer,
      input_ended: this.inputEnded,
      underrun_count: this.underrunCount,
      current_rebuffer_samples: this.currentRebufferSamples,
      render_clock_samples: this.renderClockSamples,
      played_samples: this.playedSamples,
    });
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const channel = outputs[0]?.[0];
    if (!channel) return !this.stopped;
    channel.fill(0);
    this.renderClockSamples += channel.length;
    if (this.stopped) return false;

    this.maybeStartOrResume();
    if (!this.started || this.waitingForBuffer) {
      this.maybeReportProgress();
      if (this.inputEnded && this.queuedSamples === 0) return this.signalDrained();
      return true;
    }

    let written = 0;
    while (written < channel.length && this.queue.length > 0) {
      const head = this.queue[0];
      const available = head.length - this.headOffset;
      const take = Math.min(available, channel.length - written);
      channel.set(head.subarray(this.headOffset, this.headOffset + take), written);
      written += take;
      this.headOffset += take;
      this.queuedSamples -= take;
      if (this.headOffset >= head.length) {
        this.queue.shift();
        this.headOffset = 0;
      }
    }

    this.playedSamples += written;
    this.applyFadeIn(channel, written);
    if (this.queuedSamples === 0) {
      this.applyFadeOut(channel, written);
      if (this.inputEnded) return this.signalDrained();
      this.beginRebuffering();
    }
    this.maybeReportProgress();
    return true;
  }
}

registerProcessor(ASSISTANT_PCM_STREAM_WORKLET_NAME, OmnixAssistantPcmStreamProcessor);
