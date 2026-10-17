import { describe, expect, it } from 'vitest';

import type { LiveVoiceCalibrationRecord } from './live-voice-calibration';
import { resolveDuplexMode, shouldMuteLiveMic } from './live-voice-duplex-gate';

function calibration(changes: Partial<LiveVoiceCalibrationRecord> = {}): LiveVoiceCalibrationRecord {
  return {
    version: 'live-voice-calibration-v1',
    deviceKey: 'device-pair',
    createdAt: Date.now() - 1_000,
    expiresAt: Date.now() + 60_000,
    noiseFloorRms: 0.002,
    playbackRms: 0.04,
    echoGain: 0.2,
    delayMs: 45,
    similarity: 0.9,
    userSpeechSeparation: 2.4,
    confidence: 0.9,
    resolvedMode: 'echo_aware',
    reason: 'calibration_confident',
    ...changes,
  };
}

describe('live voice duplex gate', () => {
  it('keeps automatic mode on the safe fallback without calibration or current device evidence', () => {
    expect(resolveDuplexMode('automatic', true, null, 'device-pair')).toBe('half_duplex');
    expect(resolveDuplexMode('automatic', true, calibration(), null)).toBe('half_duplex');
    expect(shouldMuteLiveMic(true, 'half_duplex')).toBe(true);
    expect(shouldMuteLiveMic(false, 'half_duplex')).toBe(false);
  });

  it('lets Automatic select echo-aware only from valid matching calibration', () => {
    expect(resolveDuplexMode('automatic', true, calibration(), 'device-pair')).toBe('echo_aware');
    expect(resolveDuplexMode('automatic', true, calibration(), 'different-device')).toBe('half_duplex');
    expect(resolveDuplexMode(
      'automatic',
      true,
      calibration({ confidence: 0.4, resolvedMode: 'half_duplex' }),
      'device-pair',
    )).toBe('half_duplex');
    expect(resolveDuplexMode(
      'automatic',
      true,
      calibration({ expiresAt: Date.now() - 1 }),
      'device-pair',
    )).toBe('half_duplex');
  });

  it('keeps microphone capture enabled in explicit echo-aware mode', () => {
    expect(resolveDuplexMode('echo_aware', true, null)).toBe('echo_aware');
    expect(shouldMuteLiveMic(true, 'echo_aware')).toBe(false);
  });

  it('falls back safely when echo-aware support is unavailable', () => {
    expect(resolveDuplexMode('echo_aware', false, calibration(), 'device-pair')).toBe('half_duplex');
    expect(resolveDuplexMode('automatic', false, calibration(), 'device-pair')).toBe('half_duplex');
    expect(resolveDuplexMode('half_duplex')).toBe('half_duplex');
  });
});
