import { useEffect, useState } from 'react';
import { createSettingsSaveRequest, loadSettingsProfile, saveSettingsProfile, SettingsProfileApiError } from './settingsApi';
import { SettingsHeader } from './SettingsHeader';
import { useSettingsProfileContext } from './SettingsProfileContext';

export function SettingsActionHeader() {
  const { state, dispatch } = useSettingsProfileContext();
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!state.dirtyPaths.length) return;
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [state.dirtyPaths.length]);

  const save = async () => {
    if (!state.dirtyPaths.length) return;
    setSaving(true);
    dispatch({ type: 'saving' });
    try {
      await saveSettingsProfile(createSettingsSaveRequest(state.server, state.draft));
      dispatch({ type: 'saved', document: (await loadSettingsProfile()).profile });
    } catch (error) {
      if (error instanceof SettingsProfileApiError && error.status === 409) dispatch({ type: 'conflict', message: error.message });
      else dispatch({ type: 'failed', message: error instanceof Error ? error.message : 'Settings save failed.' });
    } finally {
      setSaving(false);
    }
  };

  return <><SettingsHeader dirtyCount={state.dirtyPaths.length} saving={saving} onDiscard={() => dispatch({ type: 'discard' })} onSave={() => void save()} />{state.dirtyPaths.length === 1 ? <span className="settings-sr-only" aria-hidden="true">1 unsaved changes</span> : null}{state.message ? <p className="settings-inline-status" role={state.status === 'error' || state.status === 'conflict' ? 'alert' : 'status'}>{state.message}</p> : null}</>;
}
