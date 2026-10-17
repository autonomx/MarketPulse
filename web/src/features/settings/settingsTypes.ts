export const SETTINGS_CATEGORY_IDS = [
  'overview',
  'appearance-accessibility',
  'ai-providers',
  'trading-market-data',
  'models-runtime',
  'assistant-chat',
  'voice-audio',
  'storyteller-podcast',
  'rpg',
  'images-speech-input',
  'tools-integrations',
  'jobs-assets-storage',
  'diagnostics-developer',
] as const;

export type SettingsCategoryId = (typeof SETTINGS_CATEGORY_IDS)[number];

export const SETTING_SCOPES = ['global', 'module', 'session', 'job', 'local', 'environment', 'status'] as const;
export type SettingScope = (typeof SETTING_SCOPES)[number];

export const SETTINGS_PERSISTENCE_OWNERS = [
  'settings-api',
  'browser-storage',
  'module-state',
  'session-state',
  'job-payload',
  'integration-api',
  'runtime-api',
  'environment',
] as const;
export type SettingsPersistenceOwner = (typeof SETTINGS_PERSISTENCE_OWNERS)[number];

export const SETTING_AVAILABILITY_STATES = ['available', 'planned', 'unavailable', 'read-only'] as const;
export type SettingAvailability = (typeof SETTING_AVAILABILITY_STATES)[number];

export const SETTING_VALUE_KINDS = [
  'boolean',
  'string',
  'number',
  'select',
  'multi-select',
  'slider',
  'status',
  'action',
  'object',
] as const;
export type SettingValueKind = (typeof SETTING_VALUE_KINDS)[number];

export type SettingOption = {
  label: string;
  value: string | number | boolean;
  description?: string;
};

export type SettingValidationRule = {
  min?: number;
  max?: number;
  step?: number;
  pattern?: string;
  required?: boolean;
  customRuleId?: string;
};

export type SettingDefinition = {
  key: string;
  categoryId: SettingsCategoryId;
  sectionId: string;
  label: string;
  description?: string;
  kind: SettingValueKind;
  defaultValue?: unknown;
  options?: SettingOption[];
  scope: SettingScope;
  persistenceOwner: SettingsPersistenceOwner;
  availability?: SettingAvailability;
  writable?: boolean;
  restartRequired?: boolean;
  appliesTo?: 'immediately' | 'new-sessions' | 'new-jobs' | 'new-sessions-and-jobs' | 'after-restart';
  searchAliases?: string[];
  validation?: SettingValidationRule;
  featureFlag?: string;
};

export type SettingsCategoryDefinition = {
  id: SettingsCategoryId;
  label: string;
  description: string;
  icon: string;
  order: number;
  searchAliases?: string[];
};

export type SettingsRegistryValidationIssue = {
  code:
    | 'duplicate-category-id'
    | 'duplicate-setting-key'
    | 'unknown-category'
    | 'writable-noneditable-scope'
    | 'invalid-persistence-owner'
    | 'invalid-setting-key';
  message: string;
  key?: string;
};
