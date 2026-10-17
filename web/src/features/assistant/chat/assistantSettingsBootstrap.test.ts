import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from '../../settings';
import { loadSettingsProfile } from '../../settings';
import { bootstrapCentralAssistantSettings } from './assistantSettingsBootstrap';
import { fixture } from '../../../test/fixture';

vi.mock('../../settings/settingsApi', () => ({ loadSettingsProfile: vi.fn() }));

const storageKey = 'omnix.chatbot.assistantSettings';
const mockedLoadSettingsProfile = vi.mocked(loadSettingsProfile);

describe('bootstrapCentralAssistantSettings', () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedLoadSettingsProfile.mockReset();
  });

  it('seeds personality and voice from the central settings profile', async () => {
    mockedLoadSettingsProfile.mockResolvedValue({
      legacy: fixture({ success: true, provider: 'lmstudio', audio_provider_tts: '', audio_provider_stt: '' }),
      profile: {
        ...DEFAULT_SETTINGS_DOCUMENT,
        assistant: {
          ...DEFAULT_SETTINGS_DOCUMENT.assistant,
          personalityId: 'technical',
          customPersonality: 'Explain implementation details.',
          voiceId: 'voice:maya',
        },
      },
    });

    await bootstrapCentralAssistantSettings();

    expect(JSON.parse(window.localStorage.getItem(storageKey) ?? '{}')).toEqual({
      voiceId: 'voice:maya',
      personalityId: 'technical',
      customPersonality: 'Explain implementation details.',
      liveVoiceSensitivity: 55,
    });
  });

  it('preserves an existing browser override without loading central defaults', async () => {
    const existing = { voiceId: 'voice:custom', personalityId: 'creative', customPersonality: '', liveVoiceSensitivity: 72 };
    window.localStorage.setItem(storageKey, JSON.stringify(existing));

    await bootstrapCentralAssistantSettings();

    expect(mockedLoadSettingsProfile).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem(storageKey) ?? '{}')).toEqual(existing);
  });

  it('maps the central omnix default personality to the chatbot default id', async () => {
    mockedLoadSettingsProfile.mockResolvedValue({
      legacy: fixture({ success: true, provider: 'lmstudio', audio_provider_tts: '', audio_provider_stt: '' }),
      profile: DEFAULT_SETTINGS_DOCUMENT,
    });

    await bootstrapCentralAssistantSettings();

    expect(JSON.parse(window.localStorage.getItem(storageKey) ?? '{}').personalityId).toBe('default');
  });
});
