export type WaveformSimilarity = {
  similarity: number | null;
  lagSamples: number | null;
  lagMs: number | null;
  comparedSamples: number;
  alignedPlaybackRms: number | null;
  alignedMicrophoneRms: number | null;
  estimatedEchoGain: number | null;
  residualRms: number | null;
  residualRatio: number | null;
};

export class BoundedWaveformReference {
  readonly maximumSamples: number;
  private values = new Float32Array(0);

  constructor(maximumSamples = 48_000) {
    this.maximumSamples = Math.max(256, Math.round(maximumSamples));
  }

  append(samples: Float32Array): void {
    if (!samples.length) return;
    const incoming = samples.length > this.maximumSamples
      ? samples.slice(samples.length - this.maximumSamples)
      : samples;
    const keep = Math.min(this.values.length, this.maximumSamples - incoming.length);
    const next = new Float32Array(keep + incoming.length);
    if (keep) next.set(this.values.subarray(this.values.length - keep), 0);
    next.set(incoming, keep);
    this.values = next;
  }

  snapshot(): Float32Array {
    return this.values.slice();
  }

  clear(): void {
    this.values = new Float32Array(0);
  }
}

export function pcm16ToFloat32Reference(samples: Int16Array): Float32Array {
  const result = new Float32Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    result[index] = Math.max(-1, Math.min(1, samples[index] / 32768));
  }
  return result;
}

export function resampleWaveform(
  samples: Float32Array,
  sourceRate: number,
  targetRate: number,
): Float32Array {
  if (!samples.length) return new Float32Array(0);
  const from = Math.max(1, Math.round(sourceRate));
  const to = Math.max(1, Math.round(targetRate));
  if (from === to) return samples.slice();
  const outputLength = Math.max(1, Math.round(samples.length * to / from));
  const output = new Float32Array(outputLength);
  const scale = (samples.length - 1) / Math.max(1, outputLength - 1);
  for (let index = 0; index < outputLength; index += 1) {
    const position = index * scale;
    const left = Math.floor(position);
    const right = Math.min(samples.length - 1, left + 1);
    const fraction = position - left;
    output[index] = samples[left] * (1 - fraction) + samples[right] * fraction;
  }
  return output;
}

export function compareRecentWaveforms(
  playbackHistory: Float32Array,
  microphoneInput: Float32Array,
  sampleRate: number,
  maxLagMs = 900,
): WaveformSimilarity {
  if (!playbackHistory.length || microphoneInput.length < 16) return emptySimilarity();
  const minimumOverlap = Math.min(
    microphoneInput.length,
    Math.max(16, Math.min(256, Math.ceil(microphoneInput.length * 0.6))),
  );
  const lagLimit = Math.min(
    playbackHistory.length - minimumOverlap,
    Math.max(0, Math.round(sampleRate * maxLagMs / 1_000)),
  );
  if (lagLimit < 0) return emptySimilarity();
  let bestSimilarity = -1;
  let bestLag = 0;
  let bestCount = 0;
  let bestDot = 0;
  let bestPlaybackEnergy = 0;
  let bestMicrophoneEnergy = 0;

  // Keep single-sample lag precision, but bound each candidate to at most ~256
  // waveform points so a 900 ms echo search does not create a main-thread stall.
  for (let lag = 0; lag <= lagLimit; lag += 1) {
    const playbackEnd = playbackHistory.length - lag;
    const compared = Math.min(microphoneInput.length, playbackEnd);
    if (compared < minimumOverlap) continue;
    const playbackStart = playbackEnd - compared;
    const microphoneStart = microphoneInput.length - compared;
    const stride = Math.max(1, Math.ceil(compared / 256));
    let dot = 0;
    let playbackEnergy = 0;
    let microphoneEnergy = 0;
    let count = 0;
    for (let offset = 0; offset < compared; offset += stride) {
      const playback = playbackHistory[playbackStart + offset];
      const microphone = microphoneInput[microphoneStart + offset];
      dot += playback * microphone;
      playbackEnergy += playback * playback;
      microphoneEnergy += microphone * microphone;
      count += 1;
    }
    const denominator = Math.sqrt(playbackEnergy * microphoneEnergy);
    const similarity = denominator > 0 ? Math.abs(dot / denominator) : 0;
    if (similarity > bestSimilarity) {
      bestSimilarity = similarity;
      bestLag = lag;
      bestCount = count;
      bestDot = dot;
      bestPlaybackEnergy = playbackEnergy;
      bestMicrophoneEnergy = microphoneEnergy;
    }
  }

  if (bestSimilarity < 0 || bestCount <= 0) return emptySimilarity();
  const alignedPlaybackRms = Math.sqrt(bestPlaybackEnergy / bestCount);
  const alignedMicrophoneRms = Math.sqrt(bestMicrophoneEnergy / bestCount);
  const estimatedEchoGain = bestPlaybackEnergy > 1e-9
    ? bestDot / bestPlaybackEnergy
    : null;
  const residualEnergy = estimatedEchoGain === null
    ? bestMicrophoneEnergy
    : Math.max(
      0,
      bestMicrophoneEnergy
        - 2 * estimatedEchoGain * bestDot
        + estimatedEchoGain * estimatedEchoGain * bestPlaybackEnergy,
    );
  const residualRms = Math.sqrt(residualEnergy / bestCount);
  const residualRatio = alignedMicrophoneRms > 1e-6
    ? clamp01(residualRms / alignedMicrophoneRms)
    : null;

  return {
    similarity: clamp01(bestSimilarity),
    lagSamples: bestLag,
    lagMs: bestLag * 1_000 / Math.max(1, sampleRate),
    comparedSamples: bestCount,
    alignedPlaybackRms,
    alignedMicrophoneRms,
    estimatedEchoGain,
    residualRms,
    residualRatio,
  };
}

function emptySimilarity(): WaveformSimilarity {
  return {
    similarity: null,
    lagSamples: null,
    lagMs: null,
    comparedSamples: 0,
    alignedPlaybackRms: null,
    alignedMicrophoneRms: null,
    estimatedEchoGain: null,
    residualRms: null,
    residualRatio: null,
  };
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}
