import type { OmnixModuleDefinition } from '../../app/modules';
import { useAudiobookState } from './useAudiobookState';
import { useAudiobookProjectData, useAudiobookLibraryData } from './useAudiobookData';
import { useAudiobookProjectActions } from './useAudiobookActions';
import { useAudiobookReviewActions } from './useAudiobookReviewActions';

/** The audiobook workspace's state, data and actions, composed from its hooks (WP-9.5). */
export function useAudiobookWorkspace(module: OmnixModuleDefinition) {
  const state = useAudiobookState();
  const projectData = useAudiobookProjectData({ module, ...state });
  const libraryData = useAudiobookLibraryData({ module, ...state, ...projectData });
  const projectActions = useAudiobookProjectActions({ module, ...state, ...projectData, ...libraryData });
  const reviewActions = useAudiobookReviewActions({ module, ...state, ...projectData, ...libraryData, ...projectActions });
  return { module, ...state, ...projectData, ...libraryData, ...projectActions, ...reviewActions };
}

export type AudiobookWorkspaceModel = ReturnType<typeof useAudiobookWorkspace>;
