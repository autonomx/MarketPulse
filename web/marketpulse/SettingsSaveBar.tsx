import { useEffect, useState } from 'react';
import { createSettingsSaveRequest, loadSettingsProfile, saveSettingsProfile, SettingsProfileApiError } from '../src/features/settings/settingsApi';
import { useSettingsProfileContext } from '../src/features/settings/SettingsProfileContext';

/**
 * Saves the settings that wait for Save (AI provider defaults and routing), as Omnix's settings header does, but
 * shows only while there is something to save or to report. Keys and connections save on their own buttons.
 */
export function SettingsSaveBar() {
  const { state, dispatch } = useSettingsProfileContext();
  const [saving, setSaving] = useState(false);
  const dirty = state.dirtyPaths.length;

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    dispatch({ type: 'saving' });
    try {
      await saveSettingsProfile(createSettingsSaveRequest(state.server, state.draft));
      dispatch({ type: 'saved', document: (await loadSettingsProfile()).profile });
    } catch (error) {
      if (error instanceof SettingsProfileApiError && error.status === 409) dispatch({ type: 'conflict', message: error.message });
      else dispatch({ type: 'failed', message: error instanceof Error ? error.message : 'Settings could not be saved.' });
    } finally {
      setSaving(false);
    }
  };

  const failed = state.status === 'error' || state.status === 'conflict';
  if (!dirty && !saving && !failed) return null;
  return (
    <div className="mp-settings-savebar" role="region" aria-label="Unsaved changes">
      <span role={failed ? 'alert' : 'status'}>
        {saving ? 'Saving…' : failed ? state.message : `${dirty} unsaved ${dirty === 1 ? 'change' : 'changes'}`}
      </span>
      <div>
        <button type="button" className="mp-button" onClick={() => dispatch({ type: 'discard' })} disabled={!dirty || saving}>Discard</button>
        <button type="button" className="mp-button mp-button-primary" onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  );
}
