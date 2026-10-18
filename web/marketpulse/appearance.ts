import { useEffect } from 'react';
import { useMantineColorScheme } from '@mantine/core';
import {
  applyAppearanceSettings, commitAppearanceMode, currentAppearanceMode, DEFAULT_OMNIX_TEXT_SCALE, loadStoredAppearancePreferences,
  type OmnixAppearanceChangeDetail, type ResolvedOmnixAppearanceMode,
} from '../src/design/appearanceEffects';
import { DEFAULT_OMNIX_THEME } from '../src/design/appearanceThemes';
import { APPEARANCE_CHANGE_EVENT } from '../src/events/bus';

/**
 * Applies the stored appearance (dark by default) before the first render, as Omnix's shell does on entry, so the
 * trading workspace gets the same theme variables without Omnix's workstation shell. Returns the light level shown.
 */
export function applyStoredAppearance(): ResolvedOmnixAppearanceMode {
  const stored = loadStoredAppearancePreferences();
  return applyAppearanceSettings({
    mode: stored.mode ?? 'dark',
    theme: stored.theme ?? DEFAULT_OMNIX_THEME,
    density: 'comfortable',
    textScale: stored.textScale ?? DEFAULT_OMNIX_TEXT_SCALE,
    reduceMotion: false,
  }).resolvedMode;
}

/**
 * Keeps Mantine's colour scheme on the page's light level as it changes (title bar, settings), and follows the
 * operating system while the mode is "System". Omnix's shell does both itself; MarketPulse has no shell.
 */
export function AppearanceFollower() {
  const { setColorScheme } = useMantineColorScheme();

  useEffect(() => {
    setColorScheme(currentAppearanceMode().resolvedMode);
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<OmnixAppearanceChangeDetail>).detail;
      if (detail) setColorScheme(detail.resolvedMode);
    };
    window.addEventListener(APPEARANCE_CHANGE_EVENT, sync);
    return () => window.removeEventListener(APPEARANCE_CHANGE_EVENT, sync);
  }, [setColorScheme]);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia('(prefers-color-scheme: light)');
    const follow = () => {
      if (currentAppearanceMode().mode === 'system') commitAppearanceMode('system');
    };
    query.addEventListener('change', follow);
    return () => query.removeEventListener('change', follow);
  }, []);

  return null;
}
