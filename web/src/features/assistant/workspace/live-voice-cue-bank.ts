import { readLiveVoiceHumanizationFlags } from './live-voice-humanization-flags';

export type LiveVoiceCueId = 'mhm' | 'hmm' | 'inhale' | 'amused_exhale';
export type CueSampleSource = 'voice_asset' | 'procedural_fallback';

export type CueSampleResolution = {
  samples: Float32Array;
  source: CueSampleSource;
  voiceId: string | null;
  sourceSampleRate: number;
  playbackSampleRate: number;
};

export type RegisterVoiceCueSamplesInput = {
  voiceId: string;
  cueId: LiveVoiceCueId;
  variantId: string;
  samples: Float32Array;
  sampleRate: number;
};

type RegisteredVoiceCue = {
  samples: Float32Array;
  sampleRate: number;
};

const VARIANT_COUNT = 4;
const proceduralCache = new Map<string, Float32Array>();
const voiceCueAssets = new Map<string, RegisteredVoiceCue>();

export function cueVariantId(cueId: LiveVoiceCueId, sequence: number): string {
  const bounded = Math.abs(Math.trunc(sequence)) % VARIANT_COUNT;
  return `${cueId}-v${bounded + 1}`;
}

export function cueVariantCount(): number {
  return VARIANT_COUNT;
}

export function registerVoiceCueSamples(input: RegisterVoiceCueSamplesInput): boolean {
  const voiceId = normalizeVoiceId(input.voiceId);
  const sampleRate = normalizeSampleRate(input.sampleRate);
  if (!voiceId || input.samples.length <= 0 || sampleRate <= 0) return false;
  const key = voiceCueKey(voiceId, input.cueId, input.variantId);
  voiceCueAssets.set(key, {
    samples: new Float32Array(input.samples),
    sampleRate,
  });
  return true;
}

export function unregisterVoiceCueSamples(
  voiceId: string,
  cueId?: LiveVoiceCueId,
  variantId?: string,
): number {
  const normalizedVoice = normalizeVoiceId(voiceId);
  if (!normalizedVoice) return 0;
  let removed = 0;
  for (const key of [...voiceCueAssets.keys()]) {
    const [storedVoice, storedCue, storedVariant] = key.split('\u0000');
    if (storedVoice !== normalizedVoice) continue;
    if (cueId && storedCue !== cueId) continue;
    if (variantId && storedVariant !== normalizeVariantId(variantId)) continue;
    voiceCueAssets.delete(key);
    removed += 1;
  }
  return removed;
}

export function hasVoiceCueSamples(
  voiceId: string | null | undefined,
  cueId: LiveVoiceCueId,
  variantId: string,
): boolean {
  const normalizedVoice = normalizeVoiceId(voiceId);
  return Boolean(
    normalizedVoice
    && voiceCueAssets.has(voiceCueKey(normalizedVoice, cueId, variantId)),
  );
}

export function resolveCueSamples(
  cueId: LiveVoiceCueId,
  variantId: string,
  sampleRate: number,
  options: {
    voiceId?: string | null;
    allowProceduralFallback?: boolean;
  } = {},
): CueSampleResolution | null {
  const playbackSampleRate = normalizeSampleRate(sampleRate);
  const voiceId = normalizeVoiceId(options.voiceId);
  if (voiceId) {
    const asset = voiceCueAssets.get(voiceCueKey(voiceId, cueId, variantId));
    if (asset) {
      return {
        samples: resampleFloat32(asset.samples, asset.sampleRate, playbackSampleRate),
        source: 'voice_asset',
        voiceId,
        sourceSampleRate: asset.sampleRate,
        playbackSampleRate,
      };
    }
  }
  const proceduralFallbackAllowed = options.allowProceduralFallback === true
    || readLiveVoiceHumanizationFlags().proceduralCueFallback;
  if (!proceduralFallbackAllowed) return null;
  return {
    samples: getCachedCueSamples(cueId, variantId, playbackSampleRate).slice(),
    source: 'procedural_fallback',
    voiceId,
    sourceSampleRate: playbackSampleRate,
    playbackSampleRate,
  };
}

export function getCachedCueSamples(
  cueId: LiveVoiceCueId,
  variantId: string,
  sampleRate: number,
): Float32Array {
  const rate = normalizeSampleRate(sampleRate);
  const variant = parseVariant(variantId);
  const key = `${cueId}:${variant}:${rate}`;
  const existing = proceduralCache.get(key);
  if (existing) return existing;
  const samples = cueId === 'mhm' || cueId === 'hmm'
    ? createHum(cueId, variant, rate)
    : createBreath(cueId, variant, rate);
  proceduralCache.set(key, samples);
  return samples;
}

export function cloneCueSamples(
  cueId: LiveVoiceCueId,
  variantId: string,
  sampleRate: number,
): Float32Array {
  return getCachedCueSamples(cueId, variantId, sampleRate).slice();
}

export function clearCueSampleCache(): void {
  proceduralCache.clear();
}

export function clearVoiceCueAssets(): void {
  voiceCueAssets.clear();
}

function voiceCueKey(voiceId: string, cueId: LiveVoiceCueId, variantId: string): string {
  return `${voiceId}\u0000${cueId}\u0000${normalizeVariantId(variantId)}`;
}

function normalizeVoiceId(voiceId: string | null | undefined): string {
  return typeof voiceId === 'string' ? voiceId.trim().toLocaleLowerCase().slice(0, 120) : '';
}

function normalizeVariantId(variantId: string): string {
  return variantId.trim().toLocaleLowerCase().slice(0, 64);
}

function normalizeSampleRate(sampleRate: number): number {
  return Math.max(8_000, Math.round(Number.isFinite(sampleRate) ? sampleRate : 24_000));
}

function resampleFloat32(
  input: Float32Array,
  sourceRate: number,
  targetRate: number,
): Float32Array {
  if (sourceRate === targetRate) return new Float32Array(input);
  const outputLength = Math.max(1, Math.round(input.length * targetRate / sourceRate));
  const output = new Float32Array(outputLength);
  const sourceStep = sourceRate / targetRate;
  for (let index = 0; index < outputLength; index += 1) {
    const sourcePosition = Math.min(input.length - 1, index * sourceStep);
    const leftIndex = Math.floor(sourcePosition);
    const rightIndex = Math.min(input.length - 1, leftIndex + 1);
    const fraction = sourcePosition - leftIndex;
    output[index] = input[leftIndex] + ((input[rightIndex] - input[leftIndex]) * fraction);
  }
  return output;
}

function createHum(cueId: 'mhm' | 'hmm', variant: number, sampleRate: number): Float32Array {
  const durationMs = cueId === 'mhm' ? 300 + variant * 22 : 390 + variant * 28;
  const length = Math.max(1, Math.round(sampleRate * durationMs / 1_000));
  const output = new Float32Array(length);
  const base = (cueId === 'mhm' ? 142 : 128) + variant * 3;
  const split = cueId === 'mhm' ? 0.48 : 1;
  for (let index = 0; index < length; index += 1) {
    const progress = index / Math.max(1, length - 1);
    const attack = Math.min(1, progress / 0.12);
    const release = Math.min(1, (1 - progress) / 0.2);
    const envelope = smoothStep(Math.min(attack, release));
    const contour = cueId === 'mhm' && progress > split ? 1.08 : 1;
    const wobble = 1 + Math.sin(progress * Math.PI * 2.1 + variant) * 0.012;
    const phase = Math.PI * 2 * base * contour * wobble * index / sampleRate;
    const nasal = Math.sin(phase) * 0.105
      + Math.sin(phase * 2.02 + 0.4) * 0.032
      + Math.sin(phase * 3.01 + 0.8) * 0.014;
    output[index] = nasal * envelope;
  }
  return output;
}

function createBreath(
  cueId: 'inhale' | 'amused_exhale',
  variant: number,
  sampleRate: number,
): Float32Array {
  const durationMs = cueId === 'inhale' ? 250 + variant * 24 : 330 + variant * 30;
  const length = Math.max(1, Math.round(sampleRate * durationMs / 1_000));
  const output = new Float32Array(length);
  const random = seededRandom((variant + 1) * (cueId === 'inhale' ? 7_919 : 10_007));
  let lowPassed = 0;
  let previous = 0;
  for (let index = 0; index < length; index += 1) {
    const progress = index / Math.max(1, length - 1);
    const raw = random() * 2 - 1;
    lowPassed += (raw - lowPassed) * (cueId === 'inhale' ? 0.22 : 0.14);
    const highPassed = lowPassed - previous * 0.82;
    previous = lowPassed;
    const shape = cueId === 'inhale'
      ? Math.sin(Math.PI * progress) ** 1.4
      : Math.sin(Math.PI * Math.min(1, progress * 1.08)) ** 1.15;
    const amusedPulse = cueId === 'amused_exhale'
      ? 0.78 + Math.sin(progress * Math.PI * 5 + variant) * 0.12
      : 1;
    output[index] = highPassed * shape * amusedPulse * (cueId === 'inhale' ? 0.085 : 0.075);
  }
  return output;
}

function parseVariant(variantId: string): number {
  const match = /-v(\d+)$/i.exec(variantId);
  const parsed = match ? Number(match[1]) - 1 : 0;
  return Math.max(0, Math.min(VARIANT_COUNT - 1, Number.isFinite(parsed) ? parsed : 0));
}

function smoothStep(value: number): number {
  const bounded = Math.max(0, Math.min(1, value));
  return bounded * bounded * (3 - 2 * bounded);
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state * 1_664_525 + 1_013_904_223) >>> 0;
    return state / 0x1_0000_0000;
  };
}
