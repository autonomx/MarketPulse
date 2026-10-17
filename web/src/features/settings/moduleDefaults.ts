import { DEFAULT_SETTINGS_DOCUMENT } from './settingsDefaults';
import type { SettingsDocument } from './settingsDocumentTypes';

const DEFAULT_IMAGE_PROVIDER_ID = 'image:flux_klein';

type ModelTier = keyof SettingsDocument['global']['models'];

export function effectiveTaskRoute(
  document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT,
  taskId: string,
  moduleId: string,
  moduleProviderId = '',
  moduleModelId = '',
  modelTier: ModelTier = 'chat',
) {
  const taskOverrides = document.global.routing.taskOverrides;
  const override = taskOverrides[taskId] ?? taskOverrides[`${moduleId}:${taskId}`] ?? taskOverrides[moduleId];
  return {
    providerId: override?.providerId || moduleProviderId || document.global.providers.llm,
    modelId: override?.modelId || moduleModelId || document.global.models[modelTier] || document.global.models.chat,
    fallbackBehavior: document.global.routing.fallbackBehavior,
  };
}

export function assistantChatDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  const route = effectiveTaskRoute(document, 'chat.generate', 'chatbot');
  return {
    ...route,
    personalityId: document.assistant.personalityId,
    customPersonality: document.assistant.customPersonality,
    voiceId: document.assistant.voiceId,
    autoSpeakReplies: document.assistant.autoSpeakReplies,
    speechLanguage: document.assistant.speechLanguage,
    streamingAudio: document.assistant.streamingAudio,
  };
}

export function storytellerDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  return {
    ...effectiveTaskRoute(
      document,
      'story.generate',
      'storyteller',
      document.storyteller.providerId,
      document.storyteller.modelId,
      'quality',
    ),
    tone: document.storyteller.tone,
    writingStyle: document.storyteller.writingStyle,
    readSpeed: document.storyteller.readSpeed,
    pauseParagraphMs: document.storyteller.pauseParagraphMs,
    pauseChapterMs: document.storyteller.pauseChapterMs,
    readChapterTitles: document.storyteller.readChapterTitles,
    readStylePreset: document.storyteller.readStylePreset,
    pronunciation: { ...document.storyteller.pronunciation },
  };
}

export function podcastDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  return {
    ...effectiveTaskRoute(
      document,
      'podcast.script',
      'podcast',
      document.podcast.providerId,
      document.podcast.modelId,
      'quality',
    ),
    format: document.podcast.format,
    durationMinutes: document.podcast.durationMinutes,
    tone: document.podcast.tone,
    language: document.podcast.language,
    generationStyle: document.podcast.generationStyle,
    autoplay: document.podcast.autoplay,
    playbackRate: document.podcast.playbackRate,
    stability: document.podcast.stability,
    similarity: document.podcast.similarity,
    effects: [...document.podcast.effects],
  };
}

export function imageGenerationDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  return {
    providerId: document.global.providers.image || DEFAULT_IMAGE_PROVIDER_ID,
    width: document.image.width,
    height: document.image.height,
    // Explicit loading should stay resident across generations. Users can still
    // opt into per-request unloading from Advanced Options when they need VRAM.
    unloadAfterGeneration: false,
  };
}

export function speechInputDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  return {
    providerId: document.global.providers.stt,
    language: document.stt.language,
    alignment: document.stt.alignment,
    saveTranscript: document.stt.saveTranscript,
  };
}

export function voiceStudioDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT) {
  return {
    providerId: document.global.providers.tts,
    sttProviderId: document.global.providers.stt,
    voiceCloningProviderId: document.global.providers.voiceCloning || document.global.providers.tts,
    language: document.voice.language,
    stability: document.voice.stability,
    similarity: document.voice.similarity,
    style: document.voice.style,
    speed: document.voice.speed,
    pitch: document.voice.pitch,
    volume: document.voice.volume,
    effects: [...document.voice.effects],
    streaming: document.voice.streaming,
    cloningLanguage: document.voice.cloningLanguage,
    cloningQuality: document.voice.cloningQuality,
  };
}

export function rpgCampaignDefaults(document: SettingsDocument = DEFAULT_SETTINGS_DOCUMENT): SettingsDocument['rpg'] {
  return { ...document.rpg, campaignDefaults: { ...document.rpg.campaignDefaults } };
}
