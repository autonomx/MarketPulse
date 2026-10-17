import { describe, expect, it } from 'vitest';
import { INITIAL_SETTINGS_REGISTRY, SETTINGS_CATEGORIES, isSettingsCategoryId, settingsByCategory, sortedSettingsCategories, validateSettingsRegistry } from './settingsRegistry';
import type { SettingDefinition, SettingsCategoryDefinition } from './settingsTypes';

describe('settings registry', () => {
  it('defines the complete category navigation in stable order', () => {
    const categories = sortedSettingsCategories();
    expect(categories).toHaveLength(13);
    expect(categories[0]?.id).toBe('overview');
    expect(categories.at(-1)?.id).toBe('diagnostics-developer');
    expect(categories.every((category, index) => index === 0 || category.order >= categories[index - 1]!.order)).toBe(true);
  });

  it('accepts the built-in registry', () => {
    expect(validateSettingsRegistry()).toEqual([]);
    expect(settingsByCategory('ai-providers').map((setting) => setting.key)).toContain('global.providers.llm');
    expect(settingsByCategory('trading-market-data').map((setting) => setting.key)).toContain('trading.marketData.coinmarketcap');
    expect(isSettingsCategoryId('voice-audio')).toBe(true);
    expect(isSettingsCategoryId('billing')).toBe(false);
  });

  it('rejects duplicate keys and unknown categories', () => {
    const duplicate = { ...INITIAL_SETTINGS_REGISTRY[0]! };
    const unknown = {
      ...INITIAL_SETTINGS_REGISTRY[0]!,
      key: 'global.providers.unknown',
      categoryId: 'not-real',
    } as unknown as SettingDefinition;

    expect(validateSettingsRegistry(SETTINGS_CATEGORIES, [...INITIAL_SETTINGS_REGISTRY, duplicate, unknown]).map((issue) => issue.code)).toEqual(
      expect.arrayContaining(['duplicate-setting-key', 'unknown-category']),
    );
  });

  it('rejects duplicate category identifiers', () => {
    const categories: SettingsCategoryDefinition[] = [...SETTINGS_CATEGORIES, { ...SETTINGS_CATEGORIES[0]! }];
    expect(validateSettingsRegistry(categories, INITIAL_SETTINGS_REGISTRY).map((issue) => issue.code)).toContain('duplicate-category-id');
  });

  it('prevents environment and status values from becoming writable settings', () => {
    const invalidEnvironment: SettingDefinition = {
      ...INITIAL_SETTINGS_REGISTRY.find((setting) => setting.scope === 'environment')!,
      writable: true,
    };
    const invalidStatus: SettingDefinition = {
      ...INITIAL_SETTINGS_REGISTRY.find((setting) => setting.scope === 'status')!,
      writable: true,
      persistenceOwner: 'settings-api',
    };

    const codes = validateSettingsRegistry(SETTINGS_CATEGORIES, [invalidEnvironment, invalidStatus]).map((issue) => issue.code);
    expect(codes).toContain('writable-noneditable-scope');
    expect(codes).toContain('invalid-persistence-owner');
  });
});
