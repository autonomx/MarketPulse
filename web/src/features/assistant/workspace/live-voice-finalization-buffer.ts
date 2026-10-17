export type FinalizationAudioBufferPushResult = {
  accepted: boolean;
  bufferedSamples: number;
  maxSamples: number;
};

export class FinalizationAudioBuffer {
  private readonly frames: Float32Array[] = [];
  private samples = 0;

  constructor(private readonly maxSamples: number) {
    if (!Number.isFinite(maxSamples) || maxSamples <= 0) {
      throw new Error('Finalization audio buffer requires a positive sample limit.');
    }
  }

  push(frame: Float32Array): FinalizationAudioBufferPushResult {
    if (frame.length === 0) {
      return { accepted: true, bufferedSamples: this.samples, maxSamples: this.maxSamples };
    }
    if (this.samples + frame.length > this.maxSamples) {
      return { accepted: false, bufferedSamples: this.samples, maxSamples: this.maxSamples };
    }
    const copy = new Float32Array(frame);
    this.frames.push(copy);
    this.samples += copy.length;
    return { accepted: true, bufferedSamples: this.samples, maxSamples: this.maxSamples };
  }

  drain(): Float32Array[] {
    const drained = this.frames.splice(0, this.frames.length);
    this.samples = 0;
    return drained;
  }

  clear(): void {
    this.frames.length = 0;
    this.samples = 0;
  }

  get bufferedSamples(): number {
    return this.samples;
  }

  get capacitySamples(): number {
    return this.maxSamples;
  }
}
