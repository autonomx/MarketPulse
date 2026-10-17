import { useEffect } from 'react';
import { OMNIX_THEME_PRESETS } from '../../design/appearanceThemes';
import {
  commitAppearanceSettings,
  DEFAULT_OMNIX_TEXT_SCALE,
  MAX_OMNIX_TEXT_SCALE,
  MIN_OMNIX_TEXT_SCALE,
  OMNIX_TEXT_SCALE_STEP,
  normalizeTextScale,
} from '../../design/appearanceEffects';
import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';
import './AppearanceTextScale.css';

export function AppearanceSettings() {
  const { state, dispatch } = useSettingsProfileContext();
  const value = state.draft.appearance;
  useEffect(() => { commitAppearanceSettings(value); }, [value]);

  const updateTextScale = (next: number) => {
    dispatch({ type: 'update', path: 'appearance.textScale', value: normalizeTextScale(next) });
  };

  return (
    <div className="settings-category-panel">
      <div className="settings-category-title-row">
        <p className="eyebrow">Settings category</p>
        <h2>Appearance & Accessibility</h2>
        <p>Choose an Omnix palette, light level, text size, density, motion, and caption preferences.</p>
      </div>
      <SettingsSection title="Theme palette" scope="local">
        <div className="settings-theme-grid" role="radiogroup" aria-label="Omnix theme palette">
          {OMNIX_THEME_PRESETS.map((theme) => {
            const selected = value.theme === theme.id;
            return (
              <button
                key={theme.id}
                type="button"
                role="radio"
                aria-checked={selected}
                className={selected ? 'settings-theme-card active' : 'settings-theme-card'}
                onClick={() => dispatch({ type: 'update', path: 'appearance.theme', value: theme.id })}
              >
                <span className="settings-theme-preview" style={{ background: theme.preview }} aria-hidden="true">
                  <i /><i /><i />
                </span>
                <span className="settings-theme-copy">
                  <strong>{theme.label}</strong>
                  <small>{theme.description}</small>
                </span>
                <span className="settings-theme-check" aria-hidden="true">✓</span>
              </button>
            );
          })}
        </div>
      </SettingsSection>
      <SettingsSection title="Display" scope="local">
        <div className="settings-form-grid">
          <SettingsField label="Light level">
            <select value={value.mode} onChange={(event) => dispatch({ type: 'update', path: 'appearance.mode', value: event.currentTarget.value })}>
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </SettingsField>
          <SettingsField label="Density">
            <select value={value.density} onChange={(event) => dispatch({ type: 'update', path: 'appearance.density', value: event.currentTarget.value })}>
              <option value="comfortable">Comfortable</option>
              <option value="compact">Compact</option>
            </select>
          </SettingsField>
          <SettingsField label="Text size">
            <div className="settings-text-scale-control">
              <button
                type="button"
                aria-label="Decrease app text size"
                title="Decrease app text size"
                disabled={value.textScale <= MIN_OMNIX_TEXT_SCALE}
                onClick={() => updateTextScale(value.textScale - OMNIX_TEXT_SCALE_STEP)}
              >
                A−
              </button>
              <input
                id="omnix-text-scale"
                type="range"
                min={MIN_OMNIX_TEXT_SCALE}
                max={MAX_OMNIX_TEXT_SCALE}
                step={OMNIX_TEXT_SCALE_STEP}
                value={value.textScale}
                aria-label="App text size"
                aria-valuetext={`${value.textScale}%`}
                onChange={(event) => updateTextScale(Number(event.currentTarget.value))}
              />
              <button
                type="button"
                aria-label="Increase app text size"
                title="Increase app text size"
                disabled={value.textScale >= MAX_OMNIX_TEXT_SCALE}
                onClick={() => updateTextScale(value.textScale + OMNIX_TEXT_SCALE_STEP)}
              >
                A+
              </button>
              <output htmlFor="omnix-text-scale">{value.textScale}%</output>
              <button
                type="button"
                className="settings-text-scale-reset"
                disabled={value.textScale === DEFAULT_OMNIX_TEXT_SCALE}
                onClick={() => updateTextScale(DEFAULT_OMNIX_TEXT_SCALE)}
              >
                Reset
              </button>
            </div>
          </SettingsField>
        </div>
        <p className="settings-text-scale-help">Changes apply immediately across Omnix and are saved on this device. 100% is the default browser text scale.</p>
      </SettingsSection>
      <SettingsSection title="Accessibility" scope="local">
        <div className="settings-toggle-list">
          <label><input type="checkbox" checked={value.reduceMotion} onChange={(event) => dispatch({ type: 'update', path: 'appearance.reduceMotion', value: event.currentTarget.checked })} /><span>Reduce motion</span></label>
          <label><input type="checkbox" checked={value.liveCaptions} onChange={(event) => dispatch({ type: 'update', path: 'appearance.liveCaptions', value: event.currentTarget.checked })} /><span>Live captions</span></label>
        </div>
      </SettingsSection>
    </div>
  );
}
