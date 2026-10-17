import { loadSettingsProfile } from '../../settings';

const ASSISTANT_SETTINGS_STORAGE_KEY = 'omnix.chatbot.assistantSettings';
const PERSONALITY_IDS = new Set(['default', 'concise', 'coach', 'technical', 'creative', 'custom']);

type StoredAssistantSettings = {
  voiceId: string;
  personalityId: string;
  customPersonality: string;
  liveVoiceSensitivity: number;
};

function normalizedPersonalityId(value: string): string {
  if (value === 'omnix-default') return 'default';
  return PERSONALITY_IDS.has(value) ? value : 'default';
}

export async function bootstrapCentralAssistantSettings(): Promise<void> {
  if (typeof window === 'undefined') return;
  try {
    if (window.localStorage.getItem(ASSISTANT_SETTINGS_STORAGE_KEY)) return;
    const { profile } = await loadSettingsProfile();
    const settings: StoredAssistantSettings = {
      voiceId: profile.assistant.voiceId,
      personalityId: normalizedPersonalityId(profile.assistant.personalityId),
      customPersonality: profile.assistant.customPersonality,
      liveVoiceSensitivity: 55,
    };
    window.localStorage.setItem(ASSISTANT_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Startup remains available when settings or browser storage is unavailable.
  }
}
