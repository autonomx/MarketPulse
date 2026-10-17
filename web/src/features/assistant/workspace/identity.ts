export type AssistantIdentity = {
  id: string;
  name: string;
  description: string;
  systemPrompt: string;
  voiceId?: string;
  avatarUrl?: string;
  temperature?: number;
  memoryPolicy?: string;
  toolPolicy?: string;
  createdAt: string;
  updatedAt: string;
};

export const DEFAULT_ASSISTANT_IDENTITY_NAMES = [
  'Omnix Default',
  'Architect',
  'Coder',
  'Researcher',
  'Tutor',
  'Design Partner',
  'Creative',
] as const;

export type DefaultAssistantIdentityName = (typeof DEFAULT_ASSISTANT_IDENTITY_NAMES)[number];

export function createAssistantIdentity(identity: AssistantIdentity): AssistantIdentity {
  return { ...identity };
}

export function isDefaultAssistantIdentityName(value: string): value is DefaultAssistantIdentityName {
  return DEFAULT_ASSISTANT_IDENTITY_NAMES.includes(value as DefaultAssistantIdentityName);
}

export function updateAssistantIdentityPrompt(
  identity: AssistantIdentity,
  systemPrompt: string,
  updatedAt: string,
): AssistantIdentity {
  return { ...identity, systemPrompt, updatedAt };
}
