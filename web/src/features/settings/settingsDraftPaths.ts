import { cloneSettingsValue, migrateSettingsDocument } from './settingsMerge';
import type { SettingsDocument } from './settingsDocumentTypes';

export function setSettingsPath(document: SettingsDocument, path: string, value: unknown): SettingsDocument {
  const keys = path.split('.').filter(Boolean);
  const next = cloneSettingsValue(document) as unknown as Record<string, unknown>;
  let cursor = next;
  for (const key of keys.slice(0, -1)) {
    const child = cursor[key];
    if (!child || typeof child !== 'object' || Array.isArray(child)) cursor[key] = {};
    cursor = cursor[key] as Record<string, unknown>;
  }
  if (keys.length) cursor[keys[keys.length - 1]!] = value;
  return migrateSettingsDocument(next);
}

export function readSettingsPath(document: SettingsDocument, path: string): unknown {
  let value: unknown = document;
  for (const key of path.split('.').filter(Boolean)) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}
