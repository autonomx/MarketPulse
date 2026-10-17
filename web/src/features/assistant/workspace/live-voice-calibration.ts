import { emitOmnixEvent, LIVE_VOICE_CALIBRATION_UPDATED_EVENT } from '../../../events/bus';
export const LIVE_VOICE_CALIBRATION_VERSION = 'live-voice-calibration-v1';
export const LIVE_VOICE_CALIBRATION_STORAGE_KEY = 'omnix.liveVoice.calibration.v1';
export const LIVE_VOICE_CALIBRATION_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1_000;

export type LiveVoiceCalibrationRecord = {
  version: typeof LIVE_VOICE_CALIBRATION_VERSION;
  deviceKey: string;
  createdAt: number;
  expiresAt: number;
  noiseFloorRms: number;
  playbackRms: number;
  echoGain: number;
  delayMs: number;
  similarity: number;
  userSpeechSeparation: number;
  confidence: number;
  resolvedMode: 'half_duplex' | 'echo_aware';
  reason: string;
};

export type CalibrationEvaluationInput = {
  reference: Float32Array;
  microphone: Float32Array;
  userSpeech: Float32Array;
  noise: Float32Array;
  sampleRate: number;
  deviceKey: string;
  now?: number;
};

export type CalibrationResolution = {
  mode: 'half_duplex' | 'echo_aware';
  confidence: number;
  reason: string;
};

export function calculateFloatRms(samples: Float32Array): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (const sample of samples) sum += sample * sample;
  return Math.sqrt(sum / samples.length);
}

export function normalizedCrossCorrelation(
  referenceInput: Float32Array,
  microphoneInput: Float32Array,
  maxLagSamples: number,
): { similarity: number; lagSamples: number } {
  const reference = boundedDownsample(referenceInput);
  const microphone = boundedDownsample(microphoneInput);
  if (!reference.length || !microphone.length) return { similarity: 0, lagSamples: 0 };
  const scale = referenceInput.length / Math.max(1, reference.length);
  const boundedLag = Math.max(0, Math.min(
    Math.round(maxLagSamples / Math.max(1, scale)),
    microphone.length - 1,
  ));
  let best = 0;
  let bestLag = 0;
  for (let lag = 0; lag <= boundedLag; lag += 1) {
    const count = Math.min(reference.length, microphone.length - lag);
    if (count < 16) continue;
    let dot = 0;
    let referenceEnergy = 0;
    let microphoneEnergy = 0;
    for (let index = 0; index < count; index += 1) {
      const left = reference[index];
      const right = microphone[index + lag];
      dot += left * right;
      referenceEnergy += left * left;
      microphoneEnergy += right * right;
    }
    const denominator = Math.sqrt(referenceEnergy * microphoneEnergy);
    const similarity = denominator > 0 ? Math.abs(dot / denominator) : 0;
    if (similarity > best) {
      best = similarity;
      bestLag = lag;
    }
  }
  return { similarity: clamp01(best), lagSamples: Math.round(bestLag * scale) };
}

export function evaluateLiveVoiceCalibration(input: CalibrationEvaluationInput): LiveVoiceCalibrationRecord {
  const sampleRate = Math.max(8_000, input.sampleRate || 48_000);
  const referenceRms = calculateFloatRms(input.reference);
  const microphoneRms = calculateFloatRms(input.microphone);
  const noiseFloorRms = calculateFloatRms(input.noise);
  const userSpeechRms = calculateFloatRms(input.userSpeech);
  const correlation = normalizedCrossCorrelation(
    input.reference,
    input.microphone,
    Math.round(sampleRate * 0.30),
  );
  const echoGain = referenceRms > 0 ? microphoneRms / referenceRms : 0;
  const echoEnvelope = Math.max(noiseFloorRms, microphoneRms * Math.max(0.1, correlation.similarity));
  const userSpeechSeparation = echoEnvelope > 0 ? userSpeechRms / echoEnvelope : 0;
  const similarityScore = clamp01((correlation.similarity - 0.20) / 0.55);
  const separationScore = clamp01((userSpeechSeparation - 1.0) / 1.5);
  const noiseScore = clamp01(1 - noiseFloorRms / 0.08);
  const referenceScore = clamp01(referenceRms / 0.025);
  const confidence = clamp01(
    similarityScore * 0.35 + separationScore * 0.35 + noiseScore * 0.20 + referenceScore * 0.10,
  );
  const echoAware = confidence >= 0.70
    && correlation.similarity >= 0.42
    && userSpeechSeparation >= 1.35
    && referenceRms >= 0.01;
  const now = input.now ?? Date.now();
  return {
    version: LIVE_VOICE_CALIBRATION_VERSION,
    deviceKey: input.deviceKey,
    createdAt: now,
    expiresAt: now + LIVE_VOICE_CALIBRATION_MAX_AGE_MS,
    noiseFloorRms,
    playbackRms: referenceRms,
    echoGain,
    delayMs: correlation.lagSamples / sampleRate * 1_000,
    similarity: correlation.similarity,
    userSpeechSeparation,
    confidence,
    resolvedMode: echoAware ? 'echo_aware' : 'half_duplex',
    reason: echoAware ? 'calibration_confident' : calibrationFallbackReason({
      confidence,
      similarity: correlation.similarity,
      userSpeechSeparation,
      referenceRms,
      noiseFloorRms,
    }),
  };
}

export function isLiveVoiceCalibrationValid(
  record: LiveVoiceCalibrationRecord | null,
  deviceKey?: string | null,
  now = Date.now(),
): boolean {
  return Boolean(
    record
    && record.version === LIVE_VOICE_CALIBRATION_VERSION
    && record.expiresAt > now
    && (!deviceKey || record.deviceKey === deviceKey)
    && Number.isFinite(record.confidence),
  );
}

export function resolveCalibrationDuplex(
  record: LiveVoiceCalibrationRecord | null,
  deviceKey?: string | null,
  now = Date.now(),
): CalibrationResolution {
  if (!record) return { mode: 'half_duplex', confidence: 0, reason: 'calibration_missing' };
  if (!isLiveVoiceCalibrationValid(record, deviceKey, now)) {
    return {
      mode: 'half_duplex',
      confidence: clamp01(record.confidence),
      reason: record.expiresAt <= now ? 'calibration_expired' : 'calibration_device_mismatch',
    };
  }
  if (record.resolvedMode !== 'echo_aware' || record.confidence < 0.70) {
    return { mode: 'half_duplex', confidence: record.confidence, reason: record.reason || 'calibration_low_confidence' };
  }
  return { mode: 'echo_aware', confidence: record.confidence, reason: 'calibration_confident' };
}

export function readLatestLiveVoiceCalibration(): LiveVoiceCalibrationRecord | null {
  if (typeof window === 'undefined') return null;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(LIVE_VOICE_CALIBRATION_STORAGE_KEY) || 'null');
    if (!parsed || parsed.version !== LIVE_VOICE_CALIBRATION_VERSION) return null;
    return parsed as LiveVoiceCalibrationRecord;
  } catch {
    return null;
  }
}

export function writeLiveVoiceCalibration(record: LiveVoiceCalibrationRecord): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(LIVE_VOICE_CALIBRATION_STORAGE_KEY, JSON.stringify(record));
  emitOmnixEvent(LIVE_VOICE_CALIBRATION_UPDATED_EVENT, record);
}

export async function runBrowserLiveVoiceCalibration(
  onStage?: (stage: 'noise' | 'echo' | 'speech' | 'complete') => void,
): Promise<LiveVoiceCalibrationRecord> {
  if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
    throw new Error('Microphone calibration is not supported in this browser.');
  }
  const AudioContextCtor = window.AudioContext
    || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) throw new Error('Web Audio calibration is unavailable.');
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
  });
  const context = new AudioContextCtor();
  try {
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 2048;
    source.connect(analyser);

    onStage?.('noise');
    const noise = await collectAnalyserSamples(analyser, 450);
    const reference = buildCalibrationChirp(context.sampleRate, 1_000);
    const buffer = context.createBuffer(1, reference.length, context.sampleRate);
    buffer.getChannelData(0).set(reference);
    const playback = context.createBufferSource();
    playback.buffer = buffer;
    playback.connect(context.destination);

    onStage?.('echo');
    playback.start();
    const microphone = await collectAnalyserSamples(analyser, 1_350);
    await wait(250);
    onStage?.('speech');
    const userSpeech = await collectAnalyserSamples(analyser, 1_100);
    const deviceKey = await currentDeviceKey(stream);
    const record = evaluateLiveVoiceCalibration({
      reference,
      microphone,
      userSpeech,
      noise,
      sampleRate: context.sampleRate,
      deviceKey,
    });
    writeLiveVoiceCalibration(record);
    onStage?.('complete');
    return record;
  } finally {
    for (const track of stream.getTracks()) track.stop();
    await context.close().catch(() => undefined);
  }
}

function buildCalibrationChirp(sampleRate: number, durationMs: number): Float32Array {
  const length = Math.max(1, Math.round(sampleRate * durationMs / 1_000));
  const samples = new Float32Array(length);
  for (let index = 0; index < length; index += 1) {
    const progress = index / length;
    const frequency = 420 + progress * 1_180;
    const envelope = Math.sin(Math.PI * progress) ** 2;
    samples[index] = Math.sin(2 * Math.PI * frequency * index / sampleRate) * envelope * 0.08;
  }
  return samples;
}

async function collectAnalyserSamples(analyser: AnalyserNode, durationMs: number): Promise<Float32Array> {
  const chunks: Float32Array[] = [];
  const started = performance.now();
  while (performance.now() - started < durationMs) {
    const frame = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(frame);
    chunks.push(frame);
    await wait(20);
  }
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

async function currentDeviceKey(stream: MediaStream): Promise<string> {
  const input = stream.getAudioTracks()[0]?.getSettings().deviceId || 'default-input';
  let output = 'default-output';
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    output = devices.find((device) => device.kind === 'audiooutput')?.deviceId || output;
  } catch {
    // Device enumeration is optional; default hashes still isolate the fallback calibration.
  }
  return stableHash(`${input}|${output}`);
}

async function stableHash(value: string): Promise<string> {
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `fallback-${(hash >>> 0).toString(16).padStart(8, '0')}`;
}

function boundedDownsample(samples: Float32Array, maximum = 4_096): Float32Array {
  if (samples.length <= maximum) return samples;
  const result = new Float32Array(maximum);
  const stride = samples.length / maximum;
  for (let index = 0; index < maximum; index += 1) {
    result[index] = samples[Math.min(samples.length - 1, Math.floor(index * stride))];
  }
  return result;
}

function calibrationFallbackReason(values: {
  confidence: number;
  similarity: number;
  userSpeechSeparation: number;
  referenceRms: number;
  noiseFloorRms: number;
}): string {
  if (values.referenceRms < 0.01) return 'playback_reference_too_quiet';
  if (values.noiseFloorRms > 0.08) return 'environment_too_noisy';
  if (values.similarity < 0.42) return 'echo_reference_not_detected';
  if (values.userSpeechSeparation < 1.35) return 'user_speech_not_separable';
  if (values.confidence < 0.70) return 'calibration_low_confidence';
  return 'calibration_not_eligible';
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
}

function wait(durationMs: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, durationMs));
}
