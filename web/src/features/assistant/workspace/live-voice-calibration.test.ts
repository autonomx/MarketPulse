import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LIVE_VOICE_CALIBRATION_MAX_AGE_MS,
  LIVE_VOICE_CALIBRATION_STORAGE_KEY,
  evaluateLiveVoiceCalibration,
  isLiveVoiceCalibrationValid,
  normalizedCrossCorrelation,
  readLatestLiveVoiceCalibration,
  resolveCalibrationDuplex,
  writeLiveVoiceCalibration,
  type LiveVoiceCalibrationRecord,
} from './live-voice-calibration';

function sine(length: number, cycles = 16, amplitude = 0.5): Float32Array {
  return Float32Array.from({ length }, (_, index) => Math.sin(index / length * Math.PI * 2 * cycles) * amplitude);
}

function delayedEcho(reference: Float32Array, delay: number, gain: number): Float32Array {
  const result = new Float32Array(reference.length + delay);
  for (let index = 0; index < reference.length; index += 1) result[index + delay] = reference[index] * gain;
  return result;
}

function validRecord(changes: Partial<LiveVoiceCalibrationRecord> = {}): LiveVoiceCalibrationRecord {
  return {
    version: 'live-voice-calibration-v1',
    deviceKey: 'device-pair',
    createdAt: 1_000,
    expiresAt: 1_000 + LIVE_VOICE_CALIBRATION_MAX_AGE_MS,
    noiseFloorRms: 0.002,
    playbackRms: 0.03,
    echoGain: 0.2,
    delayMs: 40,
    similarity: 0.9,
    userSpeechSeparation: 2.5,
    confidence: 0.91,
    resolvedMode: 'echo_aware',
    reason: 'calibration_confident',
    ...changes,
  };
}

describe('live voice calibration', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.useRealTimers();
  });

  it('finds a delayed playback reference with normalized correlation', () => {
    const reference = sine(2_000);
    const result = normalizedCrossCorrelation(reference, delayedEcho(reference, 120, 0.25), 300);

    expect(result.similarity).toBeGreaterThan(0.95);
    expect(result.lagSamples).toBeGreaterThanOrEqual(110);
    expect(result.lagSamples).toBeLessThanOrEqual(130);
  });

  it('authorizes echo-aware mode only when echo and user speech are separable', () => {
    const reference = sine(2_400, 19, 0.08);
    const record = evaluateLiveVoiceCalibration({
      reference,
      microphone: delayedEcho(reference, 160, 0.24),
      userSpeech: sine(2_400, 7, 0.18),
      noise: sine(800, 3, 0.002),
      sampleRate: 48_000,
      deviceKey: 'device-pair',
      now: 2_000,
    });

    expect(record.similarity).toBeGreaterThan(0.8);
    expect(record.delayMs).toBeGreaterThan(2);
    expect(record.userSpeechSeparation).toBeGreaterThan(1.35);
    expect(record.confidence).toBeGreaterThanOrEqual(0.7);
    expect(record.resolvedMode).toBe('echo_aware');
    expect(resolveCalibrationDuplex(record, 'device-pair', 2_001).mode).toBe('echo_aware');
  });

  it('fails safely for noisy, expired, or mismatched calibration evidence', () => {
    const noisy = evaluateLiveVoiceCalibration({
      reference: sine(1_200, 10, 0.02),
      microphone: sine(1_200, 5, 0.01),
      userSpeech: sine(1_200, 5, 0.012),
      noise: sine(1_200, 5, 0.11),
      sampleRate: 48_000,
      deviceKey: 'device-pair',
      now: 5_000,
    });
    expect(noisy.resolvedMode).toBe('half_duplex');
    expect(resolveCalibrationDuplex(noisy, 'device-pair', 5_001).mode).toBe('half_duplex');

    const expired = validRecord({ expiresAt: 9_000 });
    expect(isLiveVoiceCalibrationValid(expired, 'device-pair', 9_001)).toBe(false);
    expect(resolveCalibrationDuplex(expired, 'device-pair', 9_001)).toMatchObject({
      mode: 'half_duplex', reason: 'calibration_expired',
    });
    expect(resolveCalibrationDuplex(validRecord(), 'other-device', 2_000)).toMatchObject({
      mode: 'half_duplex', reason: 'calibration_device_mismatch',
    });
  });

  it('persists only bounded numeric evidence and a device hash', () => {
    const record = validRecord();
    writeLiveVoiceCalibration(record);

    expect(readLatestLiveVoiceCalibration()).toEqual(record);
    const stored = window.localStorage.getItem(LIVE_VOICE_CALIBRATION_STORAGE_KEY) || '';
    expect(stored).toContain('device-pair');
    expect(stored).not.toMatch(/transcript|prompt|memory|pcm|audio_data/i);
  });
});
