export const CHATBOT_ASSISTANT_STORAGE_KEY = 'omnix.chatbot.assistantSettings';

export type AssistantPreferenceValue = {
  personalityId: string;
  customPersonality: string;
  voiceId: string;
};

export function toLegacyAssistantPreferences(value: AssistantPreferenceValue) {
  return {
    personalityId: value.personalityId === 'omnix-default' ? 'default' : value.personalityId,
    customPersonality: value.customPersonality,
    voiceId: value.voiceId,
  };
}

export function syncAssistantPreferences(value: AssistantPreferenceValue, storage?: Pick<Storage, 'setItem'>): void {
  const target = storage ?? (typeof window !== 'undefined' ? window.localStorage : undefined);
  target?.setItem(CHATBOT_ASSISTANT_STORAGE_KEY, JSON.stringify(toLegacyAssistantPreferences(value)));
}
