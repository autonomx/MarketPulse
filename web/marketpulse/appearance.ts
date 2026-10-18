import { useEffect } from 'react';
import { useMantineColorScheme } from '@mantine/core';
import {
  applyAppearanceSettings, commitAppearanceMode, currentAppearanceMode, DEFAULT_OMNIX_TEXT_SCALE, loadStoredAppearancePreferences,
  type OmnixAppearanceChangeDetail, type ResolvedOmnixAppearanceMode,
} from '../src/design/appearanceEffects';
import { DEFAULT_OMNIX_THEME } from '../src/design/appearanceThemes';
import { APPEARANCE_CHANGE_EVENT } from '../src/events/bus';
import { applyTextSize, followTextSizeMenu, loadTextSize, useTextSize } from './textSize';

/**
 * Applies the stored appearance (dark by default) and text size before the first render, as Omnix's shell does on
 * entry, so the trading workspace gets the same theme variables without Omnix's workstation shell. Returns the light
 * level shown. Omnix's text scale (a root font size) stays at 100%: MarketPulse's text size zooms the whole page.
 */
export function applyStoredAppearance(): ResolvedOmnixAppearanceMode {
  const stored = loadStoredAppearancePreferences();
  applyTextSize(loadTextSize());
  followTextSizeMenu();
  return applyAppearanceSettings({
    mode: stored.mode ?? 'dark',
    theme: stored.theme ?? DEFAULT_OMNIX_THEME,
    density: 'comfortable',
    textScale: DEFAULT_OMNIX_TEXT_SCALE,
    reduceMotion: false,
  }).resolvedMode;
}

/**
 * Keeps Mantine's colour scheme on the page's light level as it changes (title bar, settings), and follows the
 * operating system while the mode is "System". Omnix's shell does both itself; MarketPulse has no shell.
 */
export function AppearanceFollower() {
  const { setColorScheme } = useMantineColorScheme();
  // Every window follows a text size saved in another one.
  useTextSize();

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
