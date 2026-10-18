import { commitAppearanceMode, type OmnixAppearanceMode } from '../src/design/appearanceEffects';
import { useAppearanceMode } from '../src/design/useAppearanceMode';
import { SettingsSection } from '../src/features/settings/SettingsPrimitives';

const MODES: { id: OmnixAppearanceMode; label: string }[] = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'system', label: 'System' },
];

/** Light or dark mode, or following the operating system; the title bar's button switches between light and dark. */
export function AppearanceSettings() {
  const { mode } = useAppearanceMode();

  return (
    <SettingsSection title="Theme" description="Saved on this computer. The button beside the window controls switches light and dark too.">
      <div className="mp-settings-choices" role="radiogroup" aria-label="Appearance">
        {MODES.map((item) => (
          <label key={item.id}>
            <input
              type="radio"
              name="mp-appearance-mode"
              value={item.id}
              checked={mode === item.id}
              onChange={() => commitAppearanceMode(item.id)}
            />
            <span>{item.label}</span>
          </label>
        ))}
      </div>
    </SettingsSection>
  );
}
