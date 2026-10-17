export const OMNIX_THEME_PRESETS = [
  {
    id: 'aurora',
    label: 'Aurora',
    description: 'The original violet and cyan Omnix glow.',
    preview: 'linear-gradient(135deg, #7555e8 0%, #2580c8 55%, #22d3ee 100%)',
  },
  {
    id: 'graphite',
    label: 'Graphite',
    description: 'Glossy pearl, silver, and smoke-grey surfaces.',
    preview: 'linear-gradient(135deg, #f8fafc 0%, #9aa5b3 48%, #3f4a57 100%)',
  },
  {
    id: 'liquid-glass',
    label: 'Liquid Glass',
    description: 'Translucent slate-blue glass with soft blur, edge highlights, and glossy depth.',
    preview: 'linear-gradient(135deg, #d9e7f6 0%, #839ab7 36%, #425a79 68%, #14233a 100%)',
  },
  {
    id: 'evergreen',
    label: 'Evergreen',
    description: 'Calm emerald, mint, and deep forest accents.',
    preview: 'linear-gradient(135deg, #8ce7c0 0%, #19a878 52%, #075844 100%)',
  },
] as const;

export type OmnixThemeId = (typeof OMNIX_THEME_PRESETS)[number]['id'];
export type OmnixThemePreset = (typeof OMNIX_THEME_PRESETS)[number];

export const DEFAULT_OMNIX_THEME: OmnixThemeId = 'aurora';

export function resolveOmnixThemeId(value: unknown): OmnixThemeId {
  return OMNIX_THEME_PRESETS.some((theme) => theme.id === value)
    ? value as OmnixThemeId
    : DEFAULT_OMNIX_THEME;
}

export function getOmnixThemePreset(themeId: OmnixThemeId): OmnixThemePreset {
  return OMNIX_THEME_PRESETS.find((theme) => theme.id === themeId) ?? OMNIX_THEME_PRESETS[0];
}
