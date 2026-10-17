import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import { SETTINGS_SCHEMA_VERSION, type SettingsDocument, type SettingsNamespace } from './settingsDocumentTypes';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function cloneSettingsValue<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function mergeKnownSettings<T>(defaults: T, incoming: unknown): T {
  if (Array.isArray(defaults)) {
    return (Array.isArray(incoming) ? cloneSettingsValue(incoming) : cloneSettingsValue(defaults)) as T;
  }
  if (!isRecord(defaults)) {
    return (incoming === undefined ? defaults : incoming) as T;
  }
  if (Object.keys(defaults).length === 0) {
    // An empty default is a free-form map (pronunciations, provider prices):
    // keep the saved entries instead of dropping every key.
    return (isRecord(incoming) ? cloneSettingsValue(incoming) : {}) as T;
  }
  const source = isRecord(incoming) ? incoming : {};
  const result: Record<string, unknown> = {};
  for (const [key, defaultValue] of Object.entries(defaults)) {
    result[key] = mergeKnownSettings(defaultValue, source[key]);
  }
  return result as T;
}

export function migrateSettingsDocument(input: unknown): SettingsDocument {
  const source = isRecord(input) ? input : {};
  const migrated = mergeKnownSettings(DEFAULT_SETTINGS_DOCUMENT, source);
  migrated.schemaVersion = SETTINGS_SCHEMA_VERSION;
  migrated.revision = typeof source.revision === 'string' && source.revision ? source.revision : 'migrated';
  return migrated;
}

export function settingsNamespace<K extends SettingsNamespace>(document: SettingsDocument, key: K): SettingsDocument[K] {
  return cloneSettingsValue(document[key]);
}

export function effectiveModuleSettings<K extends SettingsNamespace>(
  document: SettingsDocument,
  key: K,
  overrides?: Partial<SettingsDocument[K]>,
): SettingsDocument[K] {
  return mergeKnownSettings(document[key], overrides);
}

export function settingsPatch(base: SettingsDocument, draft: SettingsDocument): Partial<SettingsDocument> {
  const patch: Partial<SettingsDocument> = {};
  for (const key of Object.keys(DEFAULT_SETTINGS_DOCUMENT) as Array<keyof SettingsDocument>) {
    if (key === 'revision' || key === 'schemaVersion') continue;
    if (JSON.stringify(base[key]) !== JSON.stringify(draft[key])) {
      (patch as Record<string, unknown>)[key] = cloneSettingsValue(draft[key]);
    }
  }
  return patch;
}
