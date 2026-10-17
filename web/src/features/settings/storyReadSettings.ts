export interface StoryReadSettings {
  pronunciationDictionary: string;
  pauseAfterParagraphMs: number;
  pauseAfterChapterMs: number;
  readChapterTitles: boolean;
  speed: number;
  stylePreset: string;
}

const STORY_READ_SETTINGS_KEY = 'omnix.storyteller.readSettings';

export const defaultStoryReadSettings: StoryReadSettings = {
  pronunciationDictionary: '',
  pauseAfterParagraphMs: 500,
  pauseAfterChapterMs: 1_200,
  readChapterTitles: true,
  speed: 1,
  stylePreset: 'Dramatic audiobook',
};

export function loadStoryReadSettings(): StoryReadSettings {
  try {
    const raw = window.localStorage.getItem(STORY_READ_SETTINGS_KEY);
    if (!raw) return defaultStoryReadSettings;
    return { ...defaultStoryReadSettings, ...JSON.parse(raw) as Partial<StoryReadSettings> };
  } catch {
    return defaultStoryReadSettings;
  }
}

export function saveStoryReadSettings(settings: StoryReadSettings): void {
  try {
    window.localStorage.setItem(STORY_READ_SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Local settings persistence is best-effort.
  }
}

export function storyReadSettingsPayload(settings: StoryReadSettings) {
  return {
    pronunciation_dictionary: settings.pronunciationDictionary,
    pause_after_paragraph_ms: settings.pauseAfterParagraphMs,
    pause_after_chapter_ms: settings.pauseAfterChapterMs,
    read_chapter_titles: settings.readChapterTitles,
    speed: settings.speed,
    style_preset: settings.stylePreset,
  };
}
