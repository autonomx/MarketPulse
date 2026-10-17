// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { migrateSettingsDocument } from '../features/settings/settingsMerge';
import { commitAppearanceSettings, loadStoredAppearancePreferences, normalizeTextScale, OMNIX_APPEARANCE_MODE_STORAGE_KEY, OMNIX_TEXT_SCALE_STORAGE_KEY, OMNIX_THEME_STORAGE_KEY } from './appearanceEffects';
import { APPEARANCE_CHANGE_EVENT } from '../events/bus';

describe('appearance theme effects', () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-omnix-appearance');
    document.documentElement.removeAttribute('data-omnix-appearance-preference');
    document.documentElement.removeAttribute('data-omnix-theme');
    document.documentElement.removeAttribute('data-omnix-density');
    document.documentElement.removeAttribute('data-omnix-text-scale');
    document.documentElement.style.removeProperty('font-size');
    document.documentElement.style.removeProperty('--omnix-text-scale');
    document.documentElement.classList.remove('omnix-reduce-motion');
  });

  it('applies, persists, and announces appearance and text scale', () => {
    const listener = vi.fn();
    window.addEventListener(APPEARANCE_CHANGE_EVENT, listener);

    const detail = commitAppearanceSettings({
      mode: 'light',
      theme: 'graphite',
      density: 'compact',
      textScale: 115,
      reduceMotion: true,
    });

    expect(detail).toEqual({ mode: 'light', resolvedMode: 'light', theme: 'graphite', textScale: 115 });
    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(document.documentElement.dataset.omnixTheme).toBe('graphite');
    expect(document.documentElement.dataset.omnixDensity).toBe('compact');
    expect(document.documentElement.dataset.omnixTextScale).toBe('115');
    expect(document.documentElement.style.fontSize).toBe('115%');
    expect(document.documentElement.style.getPropertyValue('--omnix-text-scale')).toBe('1.15');
    expect(document.documentElement.classList.contains('omnix-reduce-motion')).toBe(true);
    expect(window.localStorage.getItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY)).toBe('light');
    expect(window.localStorage.getItem(OMNIX_THEME_STORAGE_KEY)).toBe('graphite');
    expect(window.localStorage.getItem(OMNIX_TEXT_SCALE_STORAGE_KEY)).toBe('115');
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(APPEARANCE_CHANGE_EVENT, listener);
  });

  it('loads stored mode, theme, and text scale preferences', () => {
    window.localStorage.setItem(OMNIX_APPEARANCE_MODE_STORAGE_KEY, 'dark');
    window.localStorage.setItem(OMNIX_THEME_STORAGE_KEY, 'evergreen');
    window.localStorage.setItem(OMNIX_TEXT_SCALE_STORAGE_KEY, '125');

    expect(loadStoredAppearancePreferences()).toEqual({ mode: 'dark', theme: 'evergreen', textScale: 125 });
  });

  it('applies and persists the Liquid Glass preset in both appearance modes', () => {
    const dark = commitAppearanceSettings({
      mode: 'dark',
      theme: 'liquid-glass',
      density: 'comfortable',
      textScale: 100,
      reduceMotion: false,
    });

    expect(dark.theme).toBe('liquid-glass');
    expect(document.documentElement.dataset.omnixTheme).toBe('liquid-glass');
    expect(document.documentElement.dataset.omnixAppearance).toBe('dark');
    expect(window.localStorage.getItem(OMNIX_THEME_STORAGE_KEY)).toBe('liquid-glass');

    const light = commitAppearanceSettings({
      mode: 'light',
      theme: 'liquid-glass',
      density: 'comfortable',
      textScale: 100,
      reduceMotion: false,
    });

    expect(light).toMatchObject({ theme: 'liquid-glass', resolvedMode: 'light' });
    expect(document.documentElement.dataset.omnixTheme).toBe('liquid-glass');
    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
  });

  it('clamps and snaps text scaling to the supported accessibility range', () => {
    expect(normalizeTextScale(76)).toBe(80);
    expect(normalizeTextScale(112)).toBe(110);
    expect(normalizeTextScale(113)).toBe(115);
    expect(normalizeTextScale(146)).toBe(140);
    expect(normalizeTextScale('not-a-number')).toBe(100);
  });

  it('falls back to Aurora and 100 percent text when an older profile has neither', () => {
    const migrated = migrateSettingsDocument({ appearance: { mode: 'light' } });

    expect(migrated.appearance.mode).toBe('light');
    expect(migrated.appearance.theme).toBe('aurora');
    expect(migrated.appearance.textScale).toBe(100);
  });
});
