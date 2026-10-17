import type { ProviderFacadePayload } from '../../api/client';
import { modelOptions } from './providerOptions';
import { SettingsAdvanced, SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function ProviderRoutingSection({ payload }: { payload?: ProviderFacadePayload }) {
  const { state, dispatch } = useSettingsProfileContext();
  const models = state.draft.global.models;
  const options = modelOptions(payload, state.draft.global.providers.llm);
  const field = (path: string, label: string, value: string) => (
    <SettingsField label={label}>
      <select value={value} onChange={(event) => dispatch({ type: 'update', path, value: event.currentTarget.value })}>
        <option value="">Provider default</option>
        {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </SettingsField>
  );
  return (
    <SettingsSection title="Routing & fallback" description="Assign model roles for new work." scope="global">
      <div className="settings-form-grid">
        {field('global.models.fast', 'Fast model', models.fast)}
        {field('global.models.quality', 'Quality model', models.quality)}
        {field('global.models.background', 'Background model', models.background)}
        <SettingsField label="Fallback behavior">
          <select value={state.draft.global.routing.fallbackBehavior} onChange={(event) => dispatch({ type: 'update', path: 'global.routing.fallbackBehavior', value: event.currentTarget.value })}>
            <option value="next-available">Use next available provider</option>
            <option value="fail">Stop and report failure</option>
          </select>
        </SettingsField>
      </div>
      <SettingsAdvanced label="Advanced task routing"><p>Task overrides are configured in the RPG routing phase.</p></SettingsAdvanced>
    </SettingsSection>
  );
}
