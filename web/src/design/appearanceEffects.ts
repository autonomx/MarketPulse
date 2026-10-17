import { DEFAULT_OMNIX_THEME, resolveOmnixThemeId, type OmnixThemeId } from './appearanceThemes';
import { APPEARANCE_CHANGE_EVENT, emitOmnixEvent } from '../events/bus';

export const MIN_OMNIX_TEXT_SCALE = 80;
export const MAX_OMNIX_TEXT_SCALE = 140;
export const DEFAULT_OMNIX_TEXT_SCALE = 100;
export const OMNIX_TEXT_SCALE_STEP = 5;

export type AppearanceEffectSettings = {
  mode: string;
  theme?: string;
  density: string;
  textScale?: number;
  reduceMotion: boolean;
};

export type OmnixAppearanceMode = 'system' | 'light' | 'dark';
export type ResolvedOmnixAppearanceMode = 'light' | 'dark';

export type OmnixAppearanceChangeDetail = {
  mode: OmnixAppearanceMode;
  resolvedMode: ResolvedOmnixAppearanceMode;
  theme: OmnixThemeId;
  textScale: number;
};

export const OMNIX_APPEARANCE_MODE_STORAGE_KEY = 'omnix.appearance.mode';
export const OMNIX_THEME_STORAGE_KEY = 'omnix.appearance.theme';
export const OMNIX_TEXT_SCALE_STORAGE_KEY = 'omnix.appearance.textScale';

export function normalizeAppearanceMode(mode: unknown): OmnixAppearanceMode {
  return mode === 'light' || mode === 'dark' || mode === 'system' ? mode : 'system';
}

export function normalizeTextScale(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_OMNIX_TEXT_SCALE;
  const clamped = Math.max(MIN_OMNIX_TEXT_SCALE, Math.min(MAX_OMNIX_TEXT_SCALE, numeric));
  return Math.round(clamped / OMNIX_TEXT_SCALE_STEP) * OMNIX_TEXT_SCALE_STEP;
}

export function resolveAppearanceMode(mode: string): ResolvedOmnixAppearanceMode {
  const normalizedMode = normalizeAppearanceMode(mode);
  if (normalizedMode === 'light' || normalizedMode === 'dark') return normalizedMode;
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function loadStoredAppearancePreferences(): {
  mode: OmnixAppearanceMode | null;
  theme: OmnixThemeId | null;
  textScale: number | null;
} {
  if (typeof window === 'undefined') return { mode: null, theme: null, textScale: null };
  try {
    const storedMode = window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY);
    const storedTheme = window.localStorage.getItem(OMNIX_THEME_STORAGE_KEY);
    const storedTextScale = window.localStorage.getItem(OMNIX_TEXT_SCALE_STORAGE_KEY);
    return {
      mode: storedMode === 'system' || storedMode === 'light' || storedMode === 'dark' ? storedMode : null,
      theme: storedTheme ? resolveOmnixThemeId(storedTheme) : null,
      textScale: storedTextScale === null ? null : normalizeTextScale(storedTextScale),
    };
  } catch {
    return { mode: null, theme: null, textScale: null };
  }
}

export function applyAppearanceSettings(settings: AppearanceEffectSettings): OmnixAppearanceChangeDetail {
  const mode = normalizeAppearanceMode(settings.mode);
  const resolvedMode = resolveAppearanceMode(mode);
  const theme = resolveOmnixThemeId(settings.theme ?? DEFAULT_OMNIX_THEME);
  const textScale = normalizeTextScale(settings.textScale);
  if (typeof document !== 'undefined') {
    const root = document.documentElement;
    root.dataset.omnixAppearance = resolvedMode;
    root.dataset.omnixAppearancePreference = mode;
    root.dataset.omnixTheme = theme;
    root.dataset.omnixDensity = settings.density;
    root.dataset.omnixTextScale = String(textScale);
    root.style.fontSize = `${textScale}%`;
    root.style.setProperty('--omnix-text-scale', String(textScale / 100));
    root.classList.toggle('omnix-reduce-motion', settings.reduceMotion);
  }
  return { mode, resolvedMode, theme, textScale };
}

export function commitAppearanceSettings(settings: AppearanceEffectSettings): OmnixAppearanceChangeDetail {
  const detail = applyAppearanceSettings(settings);
  if (typeof window === 'undefined') return detail;
  try {
    window.localStorage.setItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY, detail.mode);
    window.localStorage.setItem(OMNIX_THEME_STORAGE_KEY, detail.theme);
    window.localStorage.setItem(OMNIX_TEXT_SCALE_STORAGE_KEY, String(detail.textScale));
  } catch {
    // Appearance persistence is best-effort in private or locked-down browser contexts.
  }
  emitOmnixEvent(APPEARANCE_CHANGE_EVENT, detail);
  return detail;
}
