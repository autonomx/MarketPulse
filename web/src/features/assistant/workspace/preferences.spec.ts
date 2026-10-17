import { describe, expect, it } from 'vitest';
import {
  DEFAULT_ASSISTANT_WORKSPACE_PREFERENCES,
  mergeAssistantWorkspacePreferences,
  shouldAnimateWorkspace,
  shouldShowLiveCaptions,
} from './preferences';

describe('assistant workspace preferences', () => {
  it('merges user overrides over stable defaults', () => {
    expect(mergeAssistantWorkspacePreferences({ density: 'compact', reduceMotion: true })).toEqual({
      ...DEFAULT_ASSISTANT_WORKSPACE_PREFERENCES,
      density: 'compact',
      reduceMotion: true,
    });
  });

  it('derives motion and caption behavior', () => {
    const preferences = mergeAssistantWorkspacePreferences({ reduceMotion: true, liveCaptionsEnabled: false });
    expect(shouldAnimateWorkspace(preferences)).toBe(false);
    expect(shouldShowLiveCaptions(preferences)).toBe(false);
  });
});
