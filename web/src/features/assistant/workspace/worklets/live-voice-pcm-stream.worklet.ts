// AudioWorkletGlobalScope: plays live voice speech, cue and silence segments in order,
// buffering before start and after underruns, and reports progress on its port.
import { LIVE_VOICE_PCM_WORKLET_NAME, liveVoiceAvatarMouthFrameForRms, type LiveVoiceAvatarMouthFrame } from './names';

declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(name: string, processor: new (options: ProcessorOptions) => AudioWorkletProcessor): void;
declare const sampleRate: number;
declare const currentTime: number | undefined;

type ProcessorOptions = { processorOptions?: Record<string, unknown> };

type SegmentIdentity = {
  segmentId: string;
  phraseIndex: number | null;
  outputId: string | null;
  generationEpoch: number;
  outputOrder: number;
};
type SoundSegment = SegmentIdentity & { segmentKind: 'speech' | 'cue'; samples: Float32Array };
type SilenceSegment = SegmentIdentity & {
  segmentKind: 'silence';
  remainingSamples: number;
  minimumFollowingSpeechSamples: number;
  reason: string;
};
type QueuedSegment = SoundSegment | SilenceSegment;
type ActiveSegment = SegmentIdentity & { segmentKind: QueuedSegment['segmentKind'] };

type PcmStreamMessage = {
  type?: string;
  samples?: Float32Array | ArrayLike<number> | ArrayBuffer;
  segmentId?: unknown;
  segmentKind?: unknown;
  phraseIndex?: unknown;
  outputId?: unknown;
  generationEpoch?: unknown;
  outputOrder?: unknown;
  durationSamples?: unknown;
  minimumFollowingSpeechSamples?: unknown;
  minimumBufferedSpeechSamples?: unknown;
  notBeforeRenderSample?: unknown;
  reason?: unknown;
};

function integer(value: unknown): number | undefined {
  return Number.isInteger(value) ? (value as number) : undefined;
}

class OmnixLiveVoicePcmStreamProcessor extends AudioWorkletProcessor {
  startBufferSamples: number;
  rebufferSamples: number;
  maxRebufferSamples: number;
  currentRebufferSamples: number;
  transitionFadeSamples: number;
  progressIntervalSamples: number;
  avatarEnvelopeIntervalSamples: number;
  avatarEnvelopeSamples = 0;
  avatarEnvelopeSquareSum = 0;
  avatarMouthFrame: LiveVoiceAvatarMouthFrame = 'closed';
  startPolicy: { notBeforeRenderSample: number; minimumBufferedSpeechSamples: number };
  queue: QueuedSegment[] = [];
  endedSegments = new Set<string>();
  terminalSegments = new Set<string>();
  cancelledOutputs = new Set<string>();
  headOffset = 0;
  queuedSamples = 0;
  bufferedSpeechSamples = 0;
  started = false;
  waitingForBuffer = false;
  waitingForFollowingSpeech = false;
  inputEnded = false;
  stopped = false;
  drained = false;
  fadeInRemaining = 0;
  cancellationFade: { remaining: number; total: number; initialSample: number } | null = null;
  lastOutputSample = 0;
  underrunCount = 0;
  renderClockSamples = 0;
  segmentTimelineSamples = 0;
  semanticSpeechSamples = 0;
  lastProgressSamples = 0;
  activeSegment: ActiveSegment | null = null;
  activeSegmentPlayedSamples = 0;

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
    this.avatarEnvelopeIntervalSamples = Math.max(
      128,
      Math.round(Number(settings.avatarEnvelopeIntervalSamples) || sampleRate * 0.02),
    );
    this.startPolicy = {
      notBeforeRenderSample: Math.max(0, Number(settings.notBeforeRenderSample) || 0),
      minimumBufferedSpeechSamples: Math.max(
        1,
        Number(settings.minimumBufferedSpeechSamples) || this.startBufferSamples,
      ),
    };
    this.port.onmessage = (event: MessageEvent<PcmStreamMessage | null>) => this.handleMessage(event.data || {});
  }

  counters(): Record<string, number> {
    return {
      sample_rate: sampleRate,
      // AudioWorklet messages can be delivered to the main thread well after
      // their render quantum under UI contention. Preserve the audio clock so
      // release metrics can recover the actual output time.
      audio_context_time_seconds: typeof currentTime === 'number'
        ? currentTime
        : this.renderClockSamples / sampleRate,
      render_clock_samples: this.renderClockSamples,
      segment_timeline_samples: this.segmentTimelineSamples,
      semantic_speech_samples: this.semanticSpeechSamples,
      played_samples: this.semanticSpeechSamples,
    };
  }

  outputKey(outputId: unknown, generationEpoch: unknown): string {
    return String(outputId || '') + ':' + String(Number(generationEpoch) || 0);
  }

  segmentFields(segment: ActiveSegment | null = this.activeSegment, playedSamples = this.activeSegmentPlayedSamples): Record<string, unknown> {
    if (!segment) return {};
    return {
      segment_id: segment.segmentId,
      segment_kind: segment.segmentKind,
      phrase_index: integer(segment.phraseIndex),
      output_id: segment.outputId || undefined,
      generation_epoch: integer(segment.generationEpoch),
      output_order: integer(segment.outputOrder),
      segment_played_samples: playedSamples,
    };
  }

  emit(type: string, details: Record<string, unknown> = {}): void {
    this.port.postMessage({
      type,
      ...this.counters(),
      ...details,
    });
  }

  reportAvatarPlayback(renderedSamples: number, speechSquareSum: number): void {
    this.avatarEnvelopeSamples += Math.max(0, Number(renderedSamples) || 0);
    this.avatarEnvelopeSquareSum += Math.max(0, Number(speechSquareSum) || 0);
    if (this.avatarEnvelopeSamples < this.avatarEnvelopeIntervalSamples) return;
    const rms = Math.sqrt(
      this.avatarEnvelopeSquareSum / Math.max(1, this.avatarEnvelopeSamples),
    );
    const frame = liveVoiceAvatarMouthFrameForRms(rms);
    this.avatarEnvelopeSamples = 0;
    this.avatarEnvelopeSquareSum = 0;
    if (frame === this.avatarMouthFrame) return;
    this.avatarMouthFrame = frame;
    this.emit('avatar_frame', { frame, rms });
  }

  handleMessage(message: PcmStreamMessage): void {
    if (message.type === 'set_start_policy') {
      this.startPolicy = {
        notBeforeRenderSample: Math.max(0, Number(message.notBeforeRenderSample) || 0),
        minimumBufferedSpeechSamples: Math.max(
          1,
          Number(message.minimumBufferedSpeechSamples) || this.startBufferSamples,
        ),
      };
      this.maybeStartOrResume();
      return;
    }
    if ((message.type === 'push_segment_samples' || message.type === 'push') && message.samples) {
      const samples = message.samples instanceof Float32Array
        ? message.samples
        : new Float32Array(message.samples);
      if (samples.length <= 0) return;
      const segmentKind = message.segmentKind === 'cue' ? 'cue' : 'speech';
      const phraseIndex = integer(message.phraseIndex) ?? -1;
      const segmentId = String(
        message.segmentId || (segmentKind === 'speech' ? 'legacy-phrase-' + phraseIndex : 'legacy-cue'),
      );
      const outputId = message.outputId ? String(message.outputId) : null;
      const generationEpoch = integer(message.generationEpoch) ?? 0;
      const outputOrder = integer(message.outputOrder) ?? -1;
      if (this.terminalSegments.has(segmentId)
        || (outputId && this.cancelledOutputs.has(this.outputKey(outputId, generationEpoch)))) {
        this.emit('late_segment_rejected', {
          segment_id: segmentId,
          segment_kind: segmentKind,
          output_id: outputId || undefined,
          generation_epoch: generationEpoch,
          incoming_samples: samples.length,
        });
        return;
      }
      this.queue.push({ segmentId, segmentKind, phraseIndex, outputId, generationEpoch, outputOrder, samples });
      this.queuedSamples += samples.length;
      if (segmentKind === 'speech') this.bufferedSpeechSamples += samples.length;
      this.emit('buffered', {
        segment_id: segmentId,
        segment_kind: segmentKind,
        phrase_index: phraseIndex,
        output_id: outputId || undefined,
        generation_epoch: generationEpoch,
        output_order: outputOrder,
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
        incoming_samples: samples.length,
        target_samples: this.waitingForBuffer
          ? this.currentRebufferSamples
          : this.startPolicy.minimumBufferedSpeechSamples,
        waiting_for_buffer: this.waitingForBuffer,
        waiting_for_following_speech: this.waitingForFollowingSpeech,
        input_ended: this.inputEnded,
        underrun_count: this.underrunCount,
      });
      this.maybeStartOrResume();
      return;
    }
    if (message.type === 'push_segment_silence') {
      const durationSamples = Math.max(0, Math.round(Number(message.durationSamples) || 0));
      if (durationSamples <= 0) return;
      const segmentId = String(message.segmentId || 'silence-' + this.renderClockSamples);
      const outputId = message.outputId ? String(message.outputId) : null;
      const generationEpoch = integer(message.generationEpoch) ?? 0;
      const outputOrder = integer(message.outputOrder) ?? -1;
      if (this.terminalSegments.has(segmentId)
        || (outputId && this.cancelledOutputs.has(this.outputKey(outputId, generationEpoch)))) {
        this.emit('late_segment_rejected', {
          segment_id: segmentId,
          segment_kind: 'silence',
          output_id: outputId || undefined,
          generation_epoch: generationEpoch,
          incoming_samples: durationSamples,
        });
        return;
      }
      this.queue.push({
        segmentId,
        segmentKind: 'silence',
        phraseIndex: null,
        outputId,
        generationEpoch,
        outputOrder,
        remainingSamples: durationSamples,
        minimumFollowingSpeechSamples: Math.max(
          0,
          Math.round(Number(message.minimumFollowingSpeechSamples) || 0),
        ),
        reason: String(message.reason || 'clause'),
      });
      this.endedSegments.add(segmentId);
      this.queuedSamples += durationSamples;
      this.emit('buffered', {
        segment_id: segmentId,
        segment_kind: 'silence',
        output_id: outputId || undefined,
        generation_epoch: generationEpoch,
        output_order: outputOrder,
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
        incoming_samples: durationSamples,
        minimum_following_speech_samples: Math.max(
          0,
          Math.round(Number(message.minimumFollowingSpeechSamples) || 0),
        ),
        target_samples: this.waitingForBuffer
          ? this.currentRebufferSamples
          : this.startPolicy.minimumBufferedSpeechSamples,
        waiting_for_buffer: this.waitingForBuffer,
        waiting_for_following_speech: this.waitingForFollowingSpeech,
        input_ended: this.inputEnded,
        underrun_count: this.underrunCount,
      });
      this.maybeStartOrResume();
      return;
    }
    if (message.type === 'segment_end') {
      const segmentId = String(message.segmentId || '');
      if (segmentId && !this.terminalSegments.has(segmentId)) this.endedSegments.add(segmentId);
      this.maybeCompleteActiveSegment();
      return;
    }
    if (message.type === 'phrase_end' && integer(message.phraseIndex) !== undefined) {
      this.endedSegments.add('legacy-phrase-' + message.phraseIndex);
      this.maybeCompleteActiveSegment();
      return;
    }
    if (message.type === 'cancel_segment') {
      const segmentId = String(message.segmentId || '');
      if (segmentId) this.cancelMatching((segment) => segment.segmentId === segmentId, String(message.reason || 'cancelled'));
      return;
    }
    if (message.type === 'cancel_output') {
      const outputId = String(message.outputId || '');
      const generationEpoch = Number(message.generationEpoch) || 0;
      if (outputId) {
        this.cancelledOutputs.add(this.outputKey(outputId, generationEpoch));
        this.cancelMatching(
          (segment) => segment.outputId === outputId && segment.generationEpoch === generationEpoch,
          String(message.reason || 'cancelled'),
        );
      }
      return;
    }
    if (message.type === 'cancel_all_after') {
      const outputOrder = Number(message.outputOrder);
      if (Number.isFinite(outputOrder)) {
        this.cancelMatching(
          (segment) => Number.isInteger(segment.outputOrder) && segment.outputOrder > outputOrder,
          String(message.reason || 'cancelled_after'),
        );
      }
      return;
    }
    if (message.type === 'end') {
      this.inputEnded = true;
      if (!this.started && this.bufferedSpeechSamples <= 0 && this.queue.length > 0) {
        this.cancelQueuedSegments('input_ended_without_speech');
      }
      this.emit('input_ended', {
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
        waiting_for_buffer: this.waitingForBuffer,
        waiting_for_following_speech: this.waitingForFollowingSpeech,
        underrun_count: this.underrunCount,
      });
      this.maybeStartOrResume();
      return;
    }
    if (message.type === 'stop') {
      const reason = String(message.reason || 'stopped');
      this.interruptActiveSegment(reason, false);
      this.cancelQueuedSegments(reason);
      this.queuedSamples = 0;
      this.bufferedSpeechSamples = 0;
      this.stopped = true;
      this.emit('stopped', {
        buffered_samples: 0,
        buffered_speech_samples: 0,
        underrun_count: this.underrunCount,
        reason,
      });
    }
  }

  beginFadeIn(): void {
    this.fadeInRemaining = this.transitionFadeSamples;
  }

  maybeStartOrResume(): void {
    const onsetReady = this.renderClockSamples >= this.startPolicy.notBeforeRenderSample;
    const speechReady = this.bufferedSpeechSamples >= this.startPolicy.minimumBufferedSpeechSamples;
    const finalShortInputReady = this.inputEnded && this.bufferedSpeechSamples > 0;
    if (!this.started && onsetReady && (speechReady || finalShortInputReady)) {
      this.started = true;
      this.waitingForBuffer = false;
      this.beginFadeIn();
      this.emit('started', {
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
      });
      return;
    }
    if (
      this.started
      && this.waitingForBuffer
      && (this.bufferedSpeechSamples >= this.currentRebufferSamples || this.inputEnded)
    ) {
      this.waitingForBuffer = false;
      this.beginFadeIn();
      this.emit('resumed', {
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
        target_samples: this.currentRebufferSamples,
        underrun_count: this.underrunCount,
      });
    }
  }

  followingSpeechReady(segment: QueuedSegment): boolean {
    if (segment.segmentKind !== 'silence') return true;
    const minimum = Math.max(0, Number(segment.minimumFollowingSpeechSamples) || 0);
    if (minimum <= 0) return true;
    if (this.bufferedSpeechSamples >= minimum) return true;
    return this.inputEnded;
  }

  beginSegment(segment: QueuedSegment): boolean {
    if (this.activeSegment && this.activeSegment.segmentId === segment.segmentId) return true;
    this.maybeCompleteActiveSegment();
    if (this.activeSegment) return false;
    this.activeSegment = {
      segmentId: segment.segmentId,
      segmentKind: segment.segmentKind,
      phraseIndex: segment.phraseIndex,
      outputId: segment.outputId,
      generationEpoch: segment.generationEpoch,
      outputOrder: segment.outputOrder,
    };
    this.activeSegmentPlayedSamples = 0;
    this.emit('segment_started', this.segmentFields());
    return true;
  }

  queuedForSegment(segmentId: string): boolean {
    return this.queue.some((item) => item.segmentId === segmentId);
  }

  maybeCompleteActiveSegment(): void {
    const segment = this.activeSegment;
    if (!segment) return;
    if (this.queuedForSegment(segment.segmentId) || !this.endedSegments.has(segment.segmentId)) return;
    if (!this.terminalSegments.has(segment.segmentId)) {
      this.terminalSegments.add(segment.segmentId);
      this.emit('segment_completed', this.segmentFields(segment));
    }
    this.endedSegments.delete(segment.segmentId);
    this.activeSegment = null;
    this.activeSegmentPlayedSamples = 0;
  }

  interruptActiveSegment(reason: string, fade = true): void {
    const segment = this.activeSegment;
    if (!segment || this.terminalSegments.has(segment.segmentId)) return;
    this.terminalSegments.add(segment.segmentId);
    this.emit('segment_interrupted', {
      ...this.segmentFields(segment),
      reason,
    });
    if (fade) {
      this.cancellationFade = {
        remaining: this.transitionFadeSamples,
        total: this.transitionFadeSamples,
        initialSample: this.lastOutputSample,
      };
    }
    this.endedSegments.delete(segment.segmentId);
    this.activeSegment = null;
    this.activeSegmentPlayedSamples = 0;
  }

  remainingSamples(segment: QueuedSegment, index: number): number {
    if (segment.segmentKind === 'silence') return Math.max(0, segment.remainingSamples);
    const offset = index === 0 ? this.headOffset : 0;
    return Math.max(0, segment.samples.length - offset);
  }

  cancelMatching(predicate: (segment: ActiveSegment) => boolean, reason: string): void {
    const active = this.activeSegment;
    if (active && predicate(active)) this.interruptActiveSegment(reason, true);
    const seen = new Set<string>();
    let removedSamples = 0;
    let removedSpeechSamples = 0;
    const kept: QueuedSegment[] = [];
    for (let index = 0; index < this.queue.length; index += 1) {
      const segment = this.queue[index];
      if (!predicate(segment)) {
        kept.push(segment);
        continue;
      }
      const remaining = this.remainingSamples(segment, index);
      removedSamples += remaining;
      if (segment.segmentKind === 'speech') removedSpeechSamples += remaining;
      if (!seen.has(segment.segmentId) && !this.terminalSegments.has(segment.segmentId)) {
        seen.add(segment.segmentId);
        this.terminalSegments.add(segment.segmentId);
        this.emit('segment_cancelled', {
          ...this.segmentFields(segment, 0),
          reason,
        });
      }
      this.endedSegments.delete(segment.segmentId);
    }
    const headRemoved = this.queue.length > 0 && kept[0] !== this.queue[0];
    this.queue = kept;
    if (headRemoved) this.headOffset = 0;
    this.queuedSamples = Math.max(0, this.queuedSamples - removedSamples);
    this.bufferedSpeechSamples = Math.max(0, this.bufferedSpeechSamples - removedSpeechSamples);
    this.waitingForFollowingSpeech = false;
    this.emit('targeted_cancellation_completed', {
      reason,
      removed_samples: removedSamples,
      removed_speech_samples: removedSpeechSamples,
      buffered_samples: this.queuedSamples,
      buffered_speech_samples: this.bufferedSpeechSamples,
    });
    this.maybeStartOrResume();
  }

  cancelQueuedSegments(reason: string): void {
    const seen = new Set<string>();
    for (const segment of this.queue) {
      if (seen.has(segment.segmentId) || this.terminalSegments.has(segment.segmentId)) continue;
      seen.add(segment.segmentId);
      this.terminalSegments.add(segment.segmentId);
      this.emit('segment_cancelled', {
        ...this.segmentFields(segment, 0),
        reason,
      });
      this.endedSegments.delete(segment.segmentId);
    }
    this.queue = [];
    this.headOffset = 0;
    this.waitingForFollowingSpeech = false;
  }

  applyFadeIn(channel: Float32Array, written: number): void {
    let index = 0;
    while (index < written && this.fadeInRemaining > 0) {
      const elapsed = this.transitionFadeSamples - this.fadeInRemaining + 1;
      const progress = Math.min(1, elapsed / this.transitionFadeSamples);
      channel[index] *= 0.5 * (1 - Math.cos(Math.PI * progress));
      this.fadeInRemaining -= 1;
      index += 1;
    }
  }

  applyFadeOut(channel: Float32Array, written: number): void {
    const fadeSamples = Math.min(written, this.transitionFadeSamples);
    const start = written - fadeSamples;
    for (let index = 0; index < fadeSamples; index += 1) {
      const progress = (index + 1) / fadeSamples;
      channel[start + index] *= 0.5 * (1 + Math.cos(Math.PI * progress));
    }
  }

  writeCancellationFade(channel: Float32Array): number {
    const fade = this.cancellationFade;
    if (!fade) return 0;
    let written = 0;
    while (written < channel.length && fade.remaining > 0) {
      const progress = 1 - (fade.remaining / fade.total);
      channel[written] = fade.initialSample * 0.5 * (1 + Math.cos(Math.PI * progress));
      fade.remaining -= 1;
      written += 1;
    }
    if (fade.remaining <= 0) {
      this.cancellationFade = null;
      this.lastOutputSample = 0;
    }
    return written;
  }

  enterIdle(): void {
    if (!this.started && !this.waitingForBuffer) return;
    this.started = false;
    this.waitingForBuffer = false;
    this.waitingForFollowingSpeech = false;
    this.currentRebufferSamples = this.rebufferSamples;
    this.lastOutputSample = 0;
    this.emit('idle', {
      buffered_samples: this.queuedSamples,
      buffered_speech_samples: this.bufferedSpeechSamples,
      underrun_count: this.underrunCount,
    });
  }

  beginRebuffering(): void {
    if (this.waitingForBuffer) return;
    this.waitingForBuffer = true;
    this.underrunCount += 1;
    const multiplier = 1 + (Math.max(0, this.underrunCount - 1) * 0.5);
    this.currentRebufferSamples = Math.min(
      this.maxRebufferSamples,
      Math.round(this.rebufferSamples * multiplier),
    );
    this.emit('underrun', {
      buffered_samples: this.queuedSamples,
      buffered_speech_samples: this.bufferedSpeechSamples,
      target_samples: this.currentRebufferSamples,
      underrun_count: this.underrunCount,
      input_ended: this.inputEnded,
    });
  }

  signalDrained(): boolean {
    this.maybeCompleteActiveSegment();
    if (!this.drained) {
      this.drained = true;
      this.emit('drained', {
        buffered_samples: this.queuedSamples,
        buffered_speech_samples: this.bufferedSpeechSamples,
        underrun_count: this.underrunCount,
      });
    }
    return false;
  }

  maybeReportProgress(): void {
    if (this.renderClockSamples - this.lastProgressSamples < this.progressIntervalSamples) return;
    this.lastProgressSamples = this.renderClockSamples;
    this.emit('render_progress', {
      ...this.segmentFields(),
      buffered_samples: this.queuedSamples,
      buffered_speech_samples: this.bufferedSpeechSamples,
      target_samples: this.waitingForBuffer ? this.currentRebufferSamples : 0,
      waiting_for_buffer: this.waitingForBuffer,
      waiting_for_following_speech: this.waitingForFollowingSpeech,
      input_ended: this.inputEnded,
      underrun_count: this.underrunCount,
      current_rebuffer_samples: this.currentRebufferSamples,
    });
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const channel = outputs[0]?.[0];
    if (!channel) return !this.stopped;
    channel.fill(0);
    this.renderClockSamples += channel.length;
    if (this.stopped) {
      this.reportAvatarPlayback(channel.length, 0);
      return false;
    }

    this.maybeStartOrResume();
    let written = this.writeCancellationFade(channel);
    let avatarSpeechSquareSum = 0;
    if ((!this.started || this.waitingForBuffer) && written === 0) {
      this.reportAvatarPlayback(channel.length, 0);
      this.maybeReportProgress();
      if (this.inputEnded && this.queuedSamples === 0) return this.signalDrained();
      return true;
    }

    while (written < channel.length && this.queue.length > 0) {
      const head = this.queue[0];
      if (!this.followingSpeechReady(head)) {
        if (!this.waitingForFollowingSpeech) {
          this.waitingForFollowingSpeech = true;
          this.emit('pause_waiting_for_following_speech', {
            segment_id: head.segmentId,
            segment_kind: head.segmentKind,
            buffered_speech_samples: this.bufferedSpeechSamples,
            minimum_following_speech_samples: head.segmentKind === 'silence' ? head.minimumFollowingSpeechSamples || 0 : 0,
          });
        }
        break;
      }
      if (this.waitingForFollowingSpeech) {
        this.waitingForFollowingSpeech = false;
        this.emit('pause_following_speech_ready', {
          segment_id: head.segmentId,
          segment_kind: head.segmentKind,
          buffered_speech_samples: this.bufferedSpeechSamples,
        });
      }
      if (!this.beginSegment(head)) break;
      const available = head.segmentKind === 'silence'
        ? head.remainingSamples
        : head.samples.length - this.headOffset;
      const take = Math.min(available, channel.length - written);
      if (head.segmentKind !== 'silence') {
        const sampleStart = this.headOffset;
        const sampleEnd = this.headOffset + take;
        const rendered = head.samples.subarray(sampleStart, sampleEnd);
        channel.set(rendered, written);
        if (head.segmentKind === 'speech') {
          for (let index = 0; index < rendered.length; index += 1) {
            avatarSpeechSquareSum += rendered[index] * rendered[index];
          }
        }
        this.headOffset += take;
      } else {
        head.remainingSamples -= take;
      }
      written += take;
      this.queuedSamples = Math.max(0, this.queuedSamples - take);
      this.segmentTimelineSamples += take;
      this.activeSegmentPlayedSamples += take;
      if (head.segmentKind === 'speech') {
        this.bufferedSpeechSamples = Math.max(0, this.bufferedSpeechSamples - take);
        this.semanticSpeechSamples += take;
      }
      const consumed = head.segmentKind === 'silence'
        ? head.remainingSamples <= 0
        : this.headOffset >= head.samples.length;
      if (consumed) {
        this.queue.shift();
        this.headOffset = 0;
        this.maybeCompleteActiveSegment();
      }
    }

    this.applyFadeIn(channel, written);
    if (written > 0) this.lastOutputSample = channel[written - 1];
    this.reportAvatarPlayback(channel.length, avatarSpeechSquareSum);
    if (this.queue.length === 0 && !this.cancellationFade) {
      this.applyFadeOut(channel, written);
      this.maybeCompleteActiveSegment();
      if (this.inputEnded) return this.signalDrained();
      if (this.activeSegment === null && this.queuedSamples === 0) {
        this.enterIdle();
      } else {
        this.beginRebuffering();
      }
    }
    this.maybeReportProgress();
    return true;
  }
}

registerProcessor(LIVE_VOICE_PCM_WORKLET_NAME, OmnixLiveVoicePcmStreamProcessor);
