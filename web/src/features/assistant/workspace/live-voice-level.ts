const LIVE_VOICE_REFERENCE_RMS = 0.12;
const LIVE_VOICE_SMOOTHING = 0.72;

export type LiveVoiceVisualScales = {
  ambientScale: number;
  barScale: number;
  coreScale: number;
  inputScale: number;
};

export function normalizeLiveVoiceLevel(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return 0;
  return clamp(rms / LIVE_VOICE_REFERENCE_RMS, 0, 1);
}

export function smoothLiveVoiceLevel(previousLevel: number, rms: number): number {
  const previous = clamp(Number.isFinite(previousLevel) ? previousLevel : 0, 0, 1);
  const next = normalizeLiveVoiceLevel(rms);
  return previous * LIVE_VOICE_SMOOTHING + next * (1 - LIVE_VOICE_SMOOTHING);
}

export function liveVoiceVisualScales(level: number): LiveVoiceVisualScales {
  const normalized = clamp(Number.isFinite(level) ? level : 0, 0, 1);
  return {
    barScale: 0.22 + normalized * 0.9,
    ambientScale: 0.92 + normalized * 0.24,
    coreScale: 1 + normalized * 0.08,
    inputScale: Math.max(0.08, normalized),
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
