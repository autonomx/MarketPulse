import { applyAppearanceSettings, DEFAULT_OMNIX_TEXT_SCALE, loadStoredAppearancePreferences } from '../src/design/appearanceEffects';
import { DEFAULT_OMNIX_THEME } from '../src/design/appearanceThemes';

/**
 * Applies the stored appearance (dark by default) before the first render, as Omnix's shell does on entry, so the
 * trading workspace gets the same theme variables without Omnix's workstation shell.
 */
export function applyStoredAppearance(): void {
  const stored = loadStoredAppearancePreferences();
  applyAppearanceSettings({
    mode: stored.mode ?? 'dark',
    theme: stored.theme ?? DEFAULT_OMNIX_THEME,
    density: 'comfortable',
    textScale: stored.textScale ?? DEFAULT_OMNIX_TEXT_SCALE,
    reduceMotion: false,
  });
}
