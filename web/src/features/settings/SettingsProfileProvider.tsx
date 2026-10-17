import { useEffect, useReducer, useState, type ReactNode } from 'react';
import { loadSettingsProfile } from './settingsApi';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import { createSettingsDraftState, settingsDraftReducer } from './settingsDraft';
import type { SettingsDocument } from './settingsDocumentTypes';
import { loadStoredAppearancePreferences } from '../../design/appearanceEffects';
import { SettingsProfileContext } from './SettingsProfileContext';

function withLocalAppearancePreferences(document: SettingsDocument): SettingsDocument {
  const stored = loadStoredAppearancePreferences();
  if (!stored.mode && !stored.theme && stored.textScale === null) return document;
  return {
    ...document,
    appearance: {
      ...document.appearance,
      mode: stored.mode ?? document.appearance.mode,
      theme: stored.theme ?? document.appearance.theme,
      textScale: stored.textScale ?? document.appearance.textScale,
    },
  };
}

export function SettingsProfileProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    settingsDraftReducer,
    withLocalAppearancePreferences(DEFAULT_SETTINGS_DOCUMENT),
    createSettingsDraftState,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  useEffect(() => {
    let active = true;
    loadSettingsProfile().then((result) => {
      if (active) dispatch({ type: 'load', document: withLocalAppearancePreferences(result.profile) });
    }).catch((error) => {
      if (active) setLoadError(error instanceof Error ? error.message : 'Settings are unavailable.');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);
  return <SettingsProfileContext.Provider value={{ state, dispatch, loading, loadError }}>{children}</SettingsProfileContext.Provider>;
}
