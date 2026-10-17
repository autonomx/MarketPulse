import type { SettingsDocument } from '../settings';

export interface OutputDefaults {
  stability: number;
  similarity: number;
  style: number;
  speed: number;
  pitch: number;
  volume: number;
}

export const DEFAULT_OUTPUT_SETTINGS: OutputDefaults = {
  stability: 0.75,
  similarity: 0.8,
  style: 0.35,
  speed: 1,
  pitch: 0,
  volume: 0,
};

export function resolveOutputDefaults(overrides: Partial<OutputDefaults> = {}): OutputDefaults {
  return { ...DEFAULT_OUTPUT_SETTINGS, ...overrides };
}

export function profileOutputDefaults(document: SettingsDocument): OutputDefaults {
  const value = document.voice;
  return resolveOutputDefaults({ stability: value.stability, similarity: value.similarity, style: value.style, speed: value.speed, pitch: value.pitch, volume: value.volume });
}
