import { SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function ModelSettings() {
  const { state, dispatch } = useSettingsProfileContext();
  const models = state.draft.global.models;
  return <div><h2>Models & Runtime</h2><SettingsSection title="Model roles" scope="global"><div className="settings-form-grid"><SettingsField label="Chat model"><input value={models.chat} onChange={(event) => dispatch({ type: 'update', path: 'global.models.chat', value: event.currentTarget.value })} /></SettingsField><SettingsField label="Fast model"><input value={models.fast} onChange={(event) => dispatch({ type: 'update', path: 'global.models.fast', value: event.currentTarget.value })} /></SettingsField></div></SettingsSection></div>;
}
