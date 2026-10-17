export class LiveVoicePreSpeechBuffer {
  private readonly frames: Float32Array[] = [];
  private samples = 0;

  constructor(private readonly maxSamples: number) {
    if (!Number.isFinite(maxSamples) || maxSamples <= 0) {
      throw new Error('Live voice pre-speech buffer requires a positive sample limit.');
    }
  }

  push(frame: Float32Array): void {
    if (frame.length === 0) return;
    const copy = new Float32Array(frame);
    this.frames.push(copy);
    this.samples += copy.length;
    this.trimToCapacity();
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

  private trimToCapacity(): void {
    let overflow = this.samples - this.maxSamples;
    while (overflow > 0 && this.frames.length > 0) {
      const oldest = this.frames[0];
      if (oldest.length <= overflow) {
        this.frames.shift();
        this.samples -= oldest.length;
        overflow -= oldest.length;
        continue;
      }
      this.frames[0] = oldest.slice(overflow);
      this.samples -= overflow;
      overflow = 0;
    }
  }
}
