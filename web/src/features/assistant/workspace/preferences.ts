export type WorkspaceAppearance = 'system' | 'light' | 'dark';
export type WorkspaceDensity = 'comfortable' | 'compact';

export type AssistantWorkspacePreferences = {
  appearance: WorkspaceAppearance;
  density: WorkspaceDensity;
  reduceMotion: boolean;
  liveCaptionsEnabled: boolean;
  defaultAssistantId?: string;
};

export const DEFAULT_ASSISTANT_WORKSPACE_PREFERENCES: AssistantWorkspacePreferences = {
  appearance: 'system',
  density: 'comfortable',
  reduceMotion: false,
  liveCaptionsEnabled: true,
};

export function mergeAssistantWorkspacePreferences(
  overrides: Partial<AssistantWorkspacePreferences>,
  defaults: AssistantWorkspacePreferences = DEFAULT_ASSISTANT_WORKSPACE_PREFERENCES,
): AssistantWorkspacePreferences {
  return {
    ...defaults,
    ...overrides,
  };
}

export function shouldAnimateWorkspace(preferences: AssistantWorkspacePreferences): boolean {
  return !preferences.reduceMotion;
}

export function shouldShowLiveCaptions(preferences: AssistantWorkspacePreferences): boolean {
  return preferences.liveCaptionsEnabled;
}
