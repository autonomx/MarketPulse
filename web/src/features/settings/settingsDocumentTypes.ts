import type { OmnixThemeId } from '../../design/appearanceThemes';

export const SETTINGS_SCHEMA_VERSION = 1 as const;

export type ResearchMode = 'disabled' | 'quick' | 'deep';
export type ResearchProvider = 'duckduckgo' | 'brave' | 'tavily' | 'playwright';
export type DesktopCompanionRolloutStage = 'disabled' | 'shadow' | 'text' | 'speech';
export type ProviderDefaults = { llm: string; tts: string; stt: string; image: string; voiceCloning: string };
export type ModelDefaults = { chat: string; fast: string; quality: string; background: string; embedding: string; imagePrompt: string };
export type RoutingDefaults = { fallbackBehavior: 'next-available' | 'fail'; taskOverrides: Record<string, { providerId: string; modelId: string }> };
export type ProviderConfigs = {
  lmstudio: { baseUrl: string; model: string; direct: boolean };
  openrouter: { apiKey: string; model: string; contextSize: number; thinkingBudget: number };
  cerebras: { apiKey: string; model: string };
  chatgptCodex: { model: string; reasoningEffort: string; fastMode: boolean; codexPath: string; transport: string };
  claudeCli: { model: string; effort: string; claudePath: string };
  llamacpp: { baseUrl: string; model: string; downloadLocation: string; autoStart: boolean };
  fasterQwen3Tts: { modelName: string; modelDir: string; device: string; dtype: string; chunkSize: number; nonStreamingMode: boolean };
  parakeet: { baseUrl: string };
  fluxKlein: { enabled: boolean; repoId: string; localDir: string; device: string; torchDtype: string; preferLocalFiles: boolean; allowRepoFallback: boolean };
};

export type AssistantSettings = {
  personalityId: string;
  customPersonality: string;
  voiceId: string;
  autoSpeakReplies: boolean;
  speechLanguage: string;
  streamingAudio: boolean;
  researchDefaultMode: ResearchMode;
  researchProvider: ResearchProvider;
  researchProviderFallbacks: ResearchProvider[];
  researchMaxResults: number;
  researchMaxSteps: number;
  researchMaxQueries: number;
  researchMaxSources: number;
  researchMaxExtracts: number;
  researchSearchCacheTtlSeconds: number;
  researchExtractionCacheTtlSeconds: number;
  researchRawRetentionDays: number;
  researchManifestRetentionDays: number;
  researchShowDiagnostics: boolean;
  researchDeepEnabled: boolean;
  researchHermesPlannerEnabled: boolean;
  desktopCompanionEnabled: boolean;
  desktopCompanionRolloutStage: DesktopCompanionRolloutStage;
  desktopCompanionVisionModelId: string;
  desktopCompanionRemoteVisionAllowed: boolean;
  desktopCompanionShowDiagnostics: boolean;
  desktopCompanionBackgroundCallsPerMinute: number;
  desktopCompanionMinimumObservationIntervalMs: number;
  desktopCompanionObservationTimeoutMs: number;
  desktopCompanionObservationTtlMs: number;
  desktopCompanionCommentaryCooldownMs: number;
  desktopCompanionMinimumChangeConfidence: number;
};

export type AgentProviderPrice = { inputUsdPerMillion: number; outputUsdPerMillion: number };

/** Defaults for agent runs started without their own limits; null means unlimited. */
export type AgentRunSettings = {
  defaultMaxOutputTokens: number | null;
  defaultMaxCostUsd: number | null;
  providerPrices: Record<string, AgentProviderPrice>;
};

export type SettingsDocument = {
  schemaVersion: number;
  revision: string;
  global: { providers: ProviderDefaults; models: ModelDefaults; routing: RoutingDefaults };
  providerConfigs: ProviderConfigs;
  appearance: { mode: 'system' | 'light' | 'dark'; theme: OmnixThemeId; density: 'comfortable' | 'compact'; textScale: number; reduceMotion: boolean; liveCaptions: boolean };
  assistant: AssistantSettings;
  agentRuns: AgentRunSettings;
  voice: { language: string; stability: number; similarity: number; style: number; speed: number; pitch: number; volume: number; effects: string[]; streaming: boolean; cloningLanguage: string; cloningQuality: string };
  storyteller: { providerId: string; modelId: string; tone: string; writingStyle: string; readSpeed: number; pauseParagraphMs: number; pauseChapterMs: number; readChapterTitles: boolean; readStylePreset: string; pronunciation: Record<string, string> };
  podcast: { providerId: string; modelId: string; format: string; durationMinutes: number; tone: string; language: string; generationStyle: string; autoplay: boolean; playbackRate: number; stability: number; similarity: number; effects: string[] };
  rpg: { difficulty: 'story' | 'normal' | 'harsh'; worldActivity: 'quiet' | 'standard' | 'living_world'; economyPressure: 'relaxed' | 'normal' | 'strict'; combatLethality: 'safe' | 'normal' | 'deadly'; companions: boolean; permadeath: boolean; autosave: boolean; validator: boolean; backgroundSoftAudit: boolean; llmNarration: boolean; imageGeneration: boolean; tts: boolean; stt: boolean; campaignDefaults: Record<string, unknown>; hermesAssistMode: string };
  image: { width: number; height: number; aspectRatio: string; portraitPreset: string; scenePreset: string; unloadAfterGeneration: boolean };
  stt: { language: string; alignment: boolean; saveTranscript: boolean; microphoneDeviceId: string; noiseSuppression: boolean; echoCancellation: boolean };
  storage: { saveOutputByDefault: boolean; retentionDays: number; temporaryAssetCleanup: boolean };
};

export type SettingsNamespace = Exclude<keyof SettingsDocument, 'schemaVersion' | 'revision'>;
