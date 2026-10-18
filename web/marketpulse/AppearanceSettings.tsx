import { commitAppearanceMode, type OmnixAppearanceMode } from '../src/design/appearanceEffects';
import { useAppearanceMode } from '../src/design/useAppearanceMode';
import { SettingsSection } from '../src/features/settings/SettingsPrimitives';
import { DEFAULT_TEXT_SIZE, MAX_TEXT_SIZE, MIN_TEXT_SIZE, saveTextSize, TEXT_SIZE_STEP, textSizeAvailable, useTextSize } from './textSize';

const MODES: { id: OmnixAppearanceMode; label: string }[] = [
  { id: 'dark', label: 'Dark' },
  { id: 'light', label: 'Light' },
  { id: 'system', label: 'System' },
];

/**
 * Light or dark mode, or following the operating system (the title bar's button switches between light and dark), and
 * the text size of everything in MarketPulse.
 */
export function AppearanceSettings() {
  const { mode } = useAppearanceMode();
  const textSize = useTextSize();

  return (
    <>
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
      <SettingsSection title="Text size" description="All text in MarketPulse, the charts included. Saved on this computer.">
        {textSizeAvailable() ? null : (
          <p className="mp-settings-note">In a browser, use the browser&apos;s own zoom (Ctrl + and Ctrl −) instead.</p>
        )}
        <div className="mp-text-size">
          <button type="button" className="mp-button" aria-label="Smaller text" disabled={textSize <= MIN_TEXT_SIZE}
            onClick={() => saveTextSize(textSize - TEXT_SIZE_STEP)}>A−</button>
          <input
            type="range"
            aria-label="Text size"
            aria-valuetext={`${textSize}%`}
            min={MIN_TEXT_SIZE}
            max={MAX_TEXT_SIZE}
            step={TEXT_SIZE_STEP}
            value={textSize}
            onChange={(event) => saveTextSize(Number(event.currentTarget.value))}
          />
          <button type="button" className="mp-button" aria-label="Larger text" disabled={textSize >= MAX_TEXT_SIZE}
            onClick={() => saveTextSize(textSize + TEXT_SIZE_STEP)}>A+</button>
          <output aria-live="polite">{textSize}%</output>
          <button type="button" className="mp-button" disabled={textSize === DEFAULT_TEXT_SIZE}
            onClick={() => saveTextSize(DEFAULT_TEXT_SIZE)}>Reset</button>
        </div>
      </SettingsSection>
    </>
  );
}
