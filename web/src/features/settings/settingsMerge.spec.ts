import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import { effectiveModuleSettings, migrateSettingsDocument, settingsNamespace, settingsPatch } from './settingsMerge';

describe('settings merge helpers', () => {
  it('merges known values and drops unknown keys', () => {
    const migrated = migrateSettingsDocument({ revision: 'legacy', voice: { speed: 1.2, unknown: true } });
    expect(migrated.revision).toBe('legacy');
    expect(migrated.voice.speed).toBe(1.2);
    expect(migrated.voice).not.toHaveProperty('unknown');
    expect(migrated.global.providers.llm).toBe(DEFAULT_SETTINGS_DOCUMENT.global.providers.llm);
  });

  it('keeps the entries of free-form maps', () => {
    const migrated = migrateSettingsDocument({
      agentRuns: { defaultMaxCostUsd: 2, providerPrices: { openrouter: { inputUsdPerMillion: 1, outputUsdPerMillion: 4 } } },
      storyteller: { pronunciation: { Nika: 'NEE-kah' } },
    });
    expect(migrated.agentRuns.providerPrices.openrouter?.outputUsdPerMillion).toBe(4);
    expect(migrated.agentRuns.defaultMaxCostUsd).toBe(2);
    expect(migrated.agentRuns.defaultMaxOutputTokens).toBeNull();
    expect(migrated.storyteller.pronunciation).toEqual({ Nika: 'NEE-kah' });
    expect(migrateSettingsDocument({ agentRuns: { providerPrices: 'bad' } }).agentRuns.providerPrices).toEqual({});
  });

  it('falls back safely for malformed input', () => {
    expect(migrateSettingsDocument({ voice: 'bad' }).voice).toEqual(DEFAULT_SETTINGS_DOCUMENT.voice);
  });

  it('clones namespaces and merges overrides', () => {
    const document = migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT);
    const appearance = settingsNamespace(document, 'appearance');
    appearance.mode = 'dark';
    expect(document.appearance.mode).toBe('system');
    expect(effectiveModuleSettings(document, 'voice', { speed: 1.1 }).speed).toBe(1.1);
  });

  it('creates a namespace patch', () => {
    const base = migrateSettingsDocument(DEFAULT_SETTINGS_DOCUMENT);
    const draft = migrateSettingsDocument({ ...base, assistant: { ...base.assistant, autoSpeakReplies: true } });
    expect(settingsPatch(base, draft)).toEqual({ assistant: draft.assistant });
  });
});
