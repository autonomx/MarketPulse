import { expect, it } from 'vitest';
import { toLegacyAssistantPreferences } from './assistantPreferencesBridge';

it('maps the central default assistant personality', () => {
  expect(toLegacyAssistantPreferences({ personalityId: 'omnix-default', customPersonality: '', voiceId: '' }).personalityId).toBe('default');
});
