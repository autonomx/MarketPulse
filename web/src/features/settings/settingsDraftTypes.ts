import type { SettingsDocument } from './settingsDocumentTypes';

export type SettingsDraftStatus = 'idle' | 'saving' | 'saved' | 'error' | 'conflict';

export type SettingsDraftState = {
  server: SettingsDocument;
  draft: SettingsDocument;
  dirtyPaths: string[];
  fieldErrors: Record<string, string>;
  status: SettingsDraftStatus;
  message: string;
};

export type SettingsDraftAction =
  | { type: 'load'; document: SettingsDocument }
  | { type: 'update'; path: string; value: unknown }
  | { type: 'discard' }
  | { type: 'saving' }
  | { type: 'saved'; document: SettingsDocument }
  | { type: 'failed'; message: string; fieldErrors?: Record<string, string> }
  | { type: 'conflict'; message: string };
