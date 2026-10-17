import { SettingsAdvanced, SettingsField, SettingsSection } from './SettingsPrimitives';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function RpgDefaultsSettings() {
  const { state, dispatch } = useSettingsProfileContext();
  const value = state.draft.rpg;
  const toggle = (path: string, checked: boolean) => dispatch({ type: 'update', path, value: checked });
  return (
    <div className="settings-category-panel">
      <h2>RPG</h2>
      <p>Preferred defaults for newly created campaigns. Existing campaigns are never changed.</p>
      <SettingsSection title="World and difficulty" scope="module">
        <div className="settings-form-grid">
          <SettingsField label="Difficulty"><select value={value.difficulty} onChange={(event) => dispatch({ type: 'update', path: 'rpg.difficulty', value: event.currentTarget.value })}><option value="story">Story</option><option value="normal">Normal</option><option value="harsh">Harsh</option></select></SettingsField>
          <SettingsField label="World activity"><select value={value.worldActivity} onChange={(event) => dispatch({ type: 'update', path: 'rpg.worldActivity', value: event.currentTarget.value })}><option value="quiet">Quiet</option><option value="standard">Standard</option><option value="living_world">Living world</option></select></SettingsField>
          <SettingsField label="Economy pressure"><select value={value.economyPressure} onChange={(event) => dispatch({ type: 'update', path: 'rpg.economyPressure', value: event.currentTarget.value })}><option value="relaxed">Relaxed</option><option value="normal">Normal</option><option value="strict">Strict</option></select></SettingsField>
          <SettingsField label="Combat lethality"><select value={value.combatLethality} onChange={(event) => dispatch({ type: 'update', path: 'rpg.combatLethality', value: event.currentTarget.value })}><option value="safe">Safe</option><option value="normal">Normal</option><option value="deadly">Deadly</option></select></SettingsField>
        </div>
      </SettingsSection>
      <SettingsSection title="Campaign systems" scope="module">
        <div className="settings-check-grid">
          {([['companions','Companions'],['permadeath','Permadeath'],['autosave','Autosave'],['validator','Grounding validator'],['backgroundSoftAudit','Background soft audit'],['llmNarration','LLM narration'],['imageGeneration','Image generation'],['tts','Text to speech'],['stt','Speech input']] as const).map(([key,label]) => <label key={key}><input type="checkbox" checked={value[key]} onChange={(event) => toggle(`rpg.${key}`, event.currentTarget.checked)} />{label}</label>)}
        </div>
      </SettingsSection>
      <SettingsSection title="Hermes assistance" scope="module">
        <SettingsField label="Assist mode"><select value={value.hermesAssistMode} onChange={(event) => dispatch({ type: 'update', path: 'rpg.hermesAssistMode', value: event.currentTarget.value })}><option value="off">Off</option><option value="suggestions_only">Suggestions only</option><option value="review_each_step">Review each step</option><option value="approved_flow">Approved flow</option></select></SettingsField>
      </SettingsSection>
      <SettingsAdvanced label="Advanced task routing"><p>Task-specific provider/model mappings are stored under global routing and never alter simulation truth.</p></SettingsAdvanced>
    </div>
  );
}
