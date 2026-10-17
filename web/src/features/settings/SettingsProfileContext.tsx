import { createContext, useContext, type Dispatch } from 'react';
import type { SettingsDraftAction, SettingsDraftState } from './settingsDraftTypes';

export type SettingsProfileContextValue = {
  state: SettingsDraftState;
  dispatch: Dispatch<SettingsDraftAction>;
  loading: boolean;
  loadError: string;
  saving?: boolean;
  save?: () => Promise<void>;
  reload?: () => Promise<void>;
};

export const SettingsProfileContext = createContext<SettingsProfileContextValue | null>(null);

export function useSettingsProfileContext(): SettingsProfileContextValue {
  const value = useContext(SettingsProfileContext);
  if (!value) throw new Error('SettingsProfileContext is unavailable.');
  return value;
}
