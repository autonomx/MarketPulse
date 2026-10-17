import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyAppearanceSettings } from './appearanceEffects';

describe('appearance effects', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('applies theme, density, and reduced motion markers', () => {
    applyAppearanceSettings({ mode: 'dark', density: 'compact', reduceMotion: true });
    expect(document.documentElement.dataset.omnixAppearance).toBe('dark');
    expect(document.documentElement.dataset.omnixAppearancePreference).toBe('dark');
    expect(document.documentElement.dataset.omnixDensity).toBe('compact');
    expect(document.documentElement.classList.contains('omnix-reduce-motion')).toBe(true);
  });

  it('resolves system appearance before applying the document marker', () => {
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      media: '(prefers-color-scheme: light)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    });

    applyAppearanceSettings({ mode: 'system', density: 'comfortable', reduceMotion: false });

    expect(document.documentElement.dataset.omnixAppearance).toBe('light');
    expect(document.documentElement.dataset.omnixAppearancePreference).toBe('system');
  });
});
