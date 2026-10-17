import { describe, expect, it } from 'vitest';
import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import {
  assistantChatDefaults,
  effectiveTaskRoute,
  imageGenerationDefaults,
  podcastDefaults,
  rpgCampaignDefaults,
  speechInputDefaults,
  storytellerDefaults,
  voiceStudioDefaults,
} from './moduleDefaults';

describe('module default adapters', () => {
  it('maps central image and speech preferences', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      image: { ...DEFAULT_SETTINGS_DOCUMENT.image, width: 1024, height: 640 },
      stt: { ...DEFAULT_SETTINGS_DOCUMENT.stt, language: 'en', alignment: false, saveTranscript: false },
    };
    expect(imageGenerationDefaults(document)).toMatchObject({ width: 1024, height: 640, providerId: 'image:flux_klein' });
    expect(speechInputDefaults(document)).toEqual({ providerId: 'parakeet', language: 'en', alignment: false, saveTranscript: false });
  });

  it('uses flux as the image provider when older profiles have no image default', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      global: {
        ...DEFAULT_SETTINGS_DOCUMENT.global,
        providers: { ...DEFAULT_SETTINGS_DOCUMENT.global.providers, image: '' },
      },
    };
    expect(imageGenerationDefaults(document).providerId).toBe('image:flux_klein');
  });

  it('maps assistant, storyteller, and podcast defaults', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      global: {
        ...DEFAULT_SETTINGS_DOCUMENT.global,
        providers: { ...DEFAULT_SETTINGS_DOCUMENT.global.providers, llm: 'cerebras' },
        models: { ...DEFAULT_SETTINGS_DOCUMENT.global.models, chat: 'chat-model', quality: 'quality-model' },
      },
      assistant: { ...DEFAULT_SETTINGS_DOCUMENT.assistant, personalityId: 'technical', voiceId: 'voice:maya' },
      storyteller: { ...DEFAULT_SETTINGS_DOCUMENT.storyteller, tone: 'Noir', writingStyle: 'Sparse' },
      podcast: { ...DEFAULT_SETTINGS_DOCUMENT.podcast, format: 'debate', durationMinutes: 30, stability: 0.6 },
    };
    expect(assistantChatDefaults(document)).toMatchObject({ providerId: 'cerebras', modelId: 'chat-model', personalityId: 'technical', voiceId: 'voice:maya' });
    expect(storytellerDefaults(document)).toMatchObject({ providerId: 'cerebras', modelId: 'quality-model', tone: 'Noir', writingStyle: 'Sparse' });
    expect(podcastDefaults(document)).toMatchObject({ providerId: 'cerebras', modelId: 'quality-model', format: 'debate', durationMinutes: 30, stability: 0.6 });
  });

  it('uses task routing overrides ahead of module and global defaults', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      global: {
        ...DEFAULT_SETTINGS_DOCUMENT.global,
        routing: {
          ...DEFAULT_SETTINGS_DOCUMENT.global.routing,
          taskOverrides: { 'story.generate': { providerId: 'openrouter', modelId: 'story-model' } },
        },
      },
    };
    expect(effectiveTaskRoute(document, 'story.generate', 'storyteller', 'cerebras', 'fallback-model', 'quality')).toEqual({
      providerId: 'openrouter',
      modelId: 'story-model',
      fallbackBehavior: 'next-available',
    });
  });

  it('maps every voice field and isolates RPG campaign defaults', () => {
    const document = {
      ...DEFAULT_SETTINGS_DOCUMENT,
      voice: {
        ...DEFAULT_SETTINGS_DOCUMENT.voice,
        language: 'French',
        speed: 1.2,
        pitch: 0.1,
        volume: -2,
        effects: ['Compression'],
        cloningLanguage: 'French',
        cloningQuality: 'Studio',
      },
      rpg: {
        ...DEFAULT_SETTINGS_DOCUMENT.rpg,
        difficulty: 'harsh' as const,
        worldActivity: 'living_world' as const,
        campaignDefaults: { genre: 'noir' },
      },
    };
    expect(voiceStudioDefaults(document)).toMatchObject({ language: 'French', speed: 1.2, pitch: 0.1, volume: -2, effects: ['Compression'], cloningLanguage: 'French', cloningQuality: 'Studio' });
    const campaign = rpgCampaignDefaults(document);
    expect(campaign).toMatchObject({ difficulty: 'harsh', worldActivity: 'living_world', campaignDefaults: { genre: 'noir' } });
    campaign.campaignDefaults.genre = 'changed';
    expect(document.rpg.campaignDefaults.genre).toBe('noir');
  });
});
