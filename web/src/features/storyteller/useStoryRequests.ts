import type { UseMutationResult } from '@tanstack/react-query';
import type { StoryLibraryItem, StoryOutlineChapter } from './storyModel';
import { type JobRecord } from '../../api/client';
import { SaveFeedback, StoryActionMode, StoryGenerationRequest, StoryQuickActionMode, StorySceneAddition, StorytellerFormValues, nextChapterNumber, shouldGenerateStoryTitle, storyModeContext } from './storyModel';

type StoryRequestsOptions = {
  createJobMutation: UseMutationResult<JobRecord, Error, StoryGenerationRequest>;
  watched: { title: string; premise: string; providerId: string };
  story: {
    title: string;
    premise: string;
    text: string | null;
    sourceJobId: string | null;
    outline: StoryOutlineChapter[];
  };
  activeLibraryItem: StoryLibraryItem | null;
  setSaveFeedback: (feedback: SaveFeedback | null) => void;
  addStorySceneAddition: (addition: StorySceneAddition) => void;
  setSelectedChapter: (chapter: number) => void;
};

/** What the writer asks for: a draft, a quick action, a story-mode move, or a new chapter. */
export function useStoryRequests({
  createJobMutation,
  watched: { title: watchedTitle, premise: watchedPremise, providerId: watchedProvider },
  story: { title: storyTitle, premise, text: activeStoryText, sourceJobId, outline },
  activeLibraryItem,
  setSaveFeedback,
  addStorySceneAddition,
  setSelectedChapter,
}: StoryRequestsOptions) {
  const requestValues = ({ allowGeneratedTitle = false }: { allowGeneratedTitle?: boolean } = {}): StorytellerFormValues => {
    const shouldGenerateTitle = allowGeneratedTitle && shouldGenerateStoryTitle(watchedTitle, storyTitle);
    return {
      providerId: watchedProvider,
      title: shouldGenerateTitle ? '' : watchedTitle || storyTitle,
      premise: watchedPremise || premise || 'Continue this interactive story.',
    };
  };

  const submitStoryRequest = (values: StorytellerFormValues, action: StoryActionMode) => {
    const appendToActiveStory = action === 'continue' && Boolean(activeLibraryItem?.id && activeStoryText?.trim());
    const generateTitle = appendToActiveStory && shouldGenerateStoryTitle(watchedTitle, storyTitle);
    setSaveFeedback(null);
    createJobMutation.mutate({
      values: appendToActiveStory ? requestValues({ allowGeneratedTitle: true }) : values,
      action,
      sourceText: activeStoryText,
      sourceJobId,
      sourceLibraryItemId: appendToActiveStory ? activeLibraryItem?.id ?? null : null,
      sourceStoryTitle: appendToActiveStory ? storyTitle : null,
      generateTitle,
      interactionMode: 'writing',
    });
  };

  const submitQuickAction = (action: StoryQuickActionMode) => {
    if (!activeStoryText?.trim()) {
      setSaveFeedback({ kind: 'error', message: 'Select or generate a story before using quick actions.' });
      return;
    }
    submitStoryRequest(requestValues({ allowGeneratedTitle: action === 'continue' }), action);
  };

  const submitStoryModeMove = (moveText: string, suggestedChoice: string | null = null) => {
    const response = moveText.trim();
    if (!response) return;
    const generateTitle = shouldGenerateStoryTitle(watchedTitle, storyTitle);
    setSaveFeedback(null);
    createJobMutation.mutate({
      values: requestValues({ allowGeneratedTitle: true }),
      action: 'continue',
      sourceText: storyModeContext(activeStoryText, response),
      sourceJobId,
      sourceLibraryItemId: activeLibraryItem?.id ?? null,
      sourceStoryTitle: storyTitle,
      generateTitle,
      interactionMode: 'story',
      userResponse: response,
      suggestedChoice,
    });
  };

  const addChapterToActiveStory = () => {
    if (!activeLibraryItem?.id || !activeStoryText?.trim()) {
      setSaveFeedback({ kind: 'error', message: 'Select or generate a story before adding a chapter.' });
      return;
    }
    const chapterNumber = nextChapterNumber(outline);
    const createdAt = new Date().toISOString();
    const addition: StorySceneAddition = {
      id: `chapter:${activeLibraryItem.id}:${createdAt}`,
      sourceItemId: activeLibraryItem.id,
      sourceJobId: `local:${createdAt}`,
      chapterNumber,
      sceneNumber: 1,
      title: 'Opening',
      chapterTitle: 'New chapter',
      startsNewChapter: true,
      content: '',
      storyTitle,
      createdAt,
    };
    addStorySceneAddition(addition);
    setSelectedChapter(chapterNumber);
    setSaveFeedback({ kind: 'saved', message: `Added Chapter ${chapterNumber} to ${storyTitle}.` });
  };

  return { submitStoryRequest, submitQuickAction, submitStoryModeMove, addChapterToActiveStory };
}
