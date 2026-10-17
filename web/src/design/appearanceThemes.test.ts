import { describe, expect, it } from 'vitest';

import {
  getOmnixThemePreset,
  OMNIX_THEME_PRESETS,
  resolveOmnixThemeId,
} from './appearanceThemes';

describe('appearance theme registry', () => {
  it('registers Liquid Glass as a first-class selectable theme', () => {
    expect(resolveOmnixThemeId('liquid-glass')).toBe('liquid-glass');
    expect(getOmnixThemePreset('liquid-glass')).toMatchObject({
      id: 'liquid-glass',
      label: 'Liquid Glass',
    });
    expect(OMNIX_THEME_PRESETS.map((theme) => theme.id)).toContain('liquid-glass');
  });
});
