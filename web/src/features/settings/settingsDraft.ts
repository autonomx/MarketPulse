import { cloneSettingsValue, migrateSettingsDocument } from './settingsMerge';
import { readSettingsPath, setSettingsPath } from './settingsDraftPaths';
import type { SettingsDraftAction, SettingsDraftState } from './settingsDraftTypes';
import type { SettingsDocument } from './settingsDocumentTypes';

export function createSettingsDraftState(document: SettingsDocument): SettingsDraftState {
  const server = migrateSettingsDocument(document);
  return { server, draft: cloneSettingsValue(server), dirtyPaths: [], fieldErrors: {}, status: 'idle', message: '' };
}

export function settingsDraftReducer(state: SettingsDraftState, action: SettingsDraftAction): SettingsDraftState {
  if (action.type === 'load' || action.type === 'saved') {
    const server = migrateSettingsDocument(action.document);
    return { server, draft: cloneSettingsValue(server), dirtyPaths: [], fieldErrors: {}, status: action.type === 'saved' ? 'saved' : 'idle', message: action.type === 'saved' ? 'Changes saved.' : '' };
  }
  if (action.type === 'discard') {
    return { ...state, draft: cloneSettingsValue(state.server), dirtyPaths: [], fieldErrors: {}, status: 'idle', message: state.dirtyPaths.length ? 'Changes discarded.' : '' };
  }
  if (action.type === 'update') {
    const draft = setSettingsPath(state.draft, action.path, action.value);
    const dirty = JSON.stringify(readSettingsPath(state.server, action.path)) !== JSON.stringify(readSettingsPath(draft, action.path));
    const dirtyPaths = dirty ? [...new Set([...state.dirtyPaths, action.path])] : state.dirtyPaths.filter((path) => path !== action.path);
    const fieldErrors = { ...state.fieldErrors };
    delete fieldErrors[action.path];
    return { ...state, draft, dirtyPaths, fieldErrors, status: 'idle', message: '' };
  }
  if (action.type === 'saving') return { ...state, status: 'saving', message: '', fieldErrors: {} };
  if (action.type === 'conflict') return { ...state, status: 'conflict', message: action.message };
  return { ...state, status: 'error', message: action.message, fieldErrors: action.fieldErrors ?? {} };
}

export function hasUnsavedSettings(state: SettingsDraftState): boolean {
  return state.dirtyPaths.length > 0;
}
