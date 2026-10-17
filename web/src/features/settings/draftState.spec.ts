import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import { createSettingsDraftState, hasUnsavedSettings, settingsDraftReducer } from './settingsDraft';
import { migrateSettingsDocument } from './settingsMerge';

describe('settings draft state', () => {
  it('tracks and reverts a field change', () => {
    const initial = createSettingsDraftState(migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT));
    const changed = settingsDraftReducer(initial, { type: 'update', path: 'appearance.mode', value: 'dark' });
    expect(changed.draft.appearance.mode).toBe('dark');
    expect(hasUnsavedSettings(changed)).toBe(true);
    const reverted = settingsDraftReducer(changed, { type: 'update', path: 'appearance.mode', value: 'system' });
    expect(hasUnsavedSettings(reverted)).toBe(false);
  });

  it('reports save and discard feedback', () => {
    const initial = createSettingsDraftState(migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT));
    const changed = settingsDraftReducer(initial, { type: 'update', path: 'appearance.mode', value: 'dark' });
    const discarded = settingsDraftReducer(changed, { type: 'discard' });
    expect(discarded.message).toBe('Changes discarded.');

    const saved = settingsDraftReducer(changed, { type: 'saved', document: DEFAULT_SETTINGS_DOCUMENT });
    expect(saved.message).toBe('Changes saved.');
    expect(hasUnsavedSettings(saved)).toBe(false);
  });
});
