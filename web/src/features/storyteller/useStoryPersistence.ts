import { useQueryClient } from '@tanstack/react-query';
import { downloadBlob } from '../../shared/download';
import { libraryAssetId, persistTrashedStoryLibraryItems, upsertTrashedStoryLibraryItem } from './storyLibraryModel';
import { SaveFeedback, SavedStoryDraft, StoryLibrarySection, TrashedStoryLibraryItem, slugify, storyDraftStorageKey, StoryLibraryItem } from './storyModel';
import { formatStoryMarkdown } from './storyTextModel';
import { storyApiClient } from './api/storyClient';

type StoryPersistenceOptions = {
  story: {
    title: string;
    premise: string;
    providerLabel: string;
    wordCount: number;
    chapterCount: number;
    sourceJobId: string | null;
    text: string | null;
  };
  activeLibraryItem: StoryLibraryItem | null;
  activeLibrarySection: StoryLibrarySection;
  setActiveLibrarySection: (section: StoryLibrarySection) => void;
  setSelectedLibraryItemId: (itemId: string | null) => void;
  setIsNewDraft: (isNewDraft: boolean) => void;
  setSaveFeedback: (feedback: SaveFeedback | null) => void;
  trashedLibraryItems: TrashedStoryLibraryItem[];
  trashItems: TrashedStoryLibraryItem[];
  setTrashedLibraryItems: (items: TrashedStoryLibraryItem[]) => void;
  setSavedDraft: (draft: SavedStoryDraft | null) => void;
};

/** Saving the active story (as a shared asset, or locally), exporting it as Markdown, and moving it to the trash. */
export function useStoryPersistence({
  story: {
    title: storyTitle,
    premise,
    providerLabel,
    wordCount,
    chapterCount,
    sourceJobId,
    text: activeStoryText,
  },
  activeLibraryItem,
  activeLibrarySection,
  setActiveLibrarySection,
  setSelectedLibraryItemId,
  setIsNewDraft,
  setSaveFeedback,
  trashedLibraryItems,
  trashItems,
  setTrashedLibraryItems,
  setSavedDraft,
}: StoryPersistenceOptions) {
  const queryClient = useQueryClient();
  const trashActiveStory = () => {
    if (activeLibrarySection === 'trash') {
      setActiveLibrarySection('trash');
      return;
    }

    const item = activeLibraryItem;
    if (!item || trashItems.some((trashedItem) => trashedItem.id === item.id)) {
      setActiveLibrarySection('trash');
      return;
    }

    const nextTrashItems = upsertTrashedStoryLibraryItem(trashedLibraryItems, {
      ...item,
      trashedAt: new Date().toISOString(),
    });
    persistTrashedStoryLibraryItems(nextTrashItems);
    setTrashedLibraryItems(nextTrashItems);
    if (item.source === 'draft') {
      window.localStorage.removeItem(storyDraftStorageKey);
      setSavedDraft(null);
    }
    setIsNewDraft(false);
    setSelectedLibraryItemId(item.id);
    setActiveLibrarySection('trash');
    setSaveFeedback({ kind: 'saved', message: `Moved "${item.title}" to Trash.` });
  };

  const draftForActiveStory = (): SavedStoryDraft => ({
    title: storyTitle,
    premise,
    providerLabel,
    wordCount,
    chapterCount,
    sourceJobId,
    savedAt: new Date().toISOString(),
    content: activeStoryText ?? '',
  });

  const persistLocalDraft = (draft: SavedStoryDraft) => {
    window.localStorage.setItem(storyDraftStorageKey, JSON.stringify(draft));
    setSavedDraft(draft);
  };

  const saveStoryDraft = async () => {
    if (!activeStoryText?.trim()) {
      setSaveFeedback({ kind: 'error', message: 'Generate or select a story version before saving.' });
      return;
    }
    const draft = draftForActiveStory();
    try {
      persistLocalDraft(draft);
      const saved = await storyApiClient.saveStoryAsset({
        title: storyTitle,
        content: activeStoryText,
        premise,
        provider_label: providerLabel,
        word_count: wordCount,
        chapter_count: chapterCount,
        source_job_id: sourceJobId,
        metadata: { source: activeLibraryItem?.source ?? 'workspace' },
      });
      setSaveFeedback({ kind: 'saved', message: `Saved “${storyTitle}” as a shared story asset.` });
      setIsNewDraft(false);
      setActiveLibrarySection('stories');
      setSelectedLibraryItemId(libraryAssetId(saved.asset.id));
      await queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] });
    } catch {
      try {
        persistLocalDraft(draft);
        setActiveLibrarySection('drafts');
        setSelectedLibraryItemId('draft:last');
        setIsNewDraft(false);
        setSaveFeedback({ kind: 'saved', message: `Saved “${storyTitle}” locally.` });
      } catch (error) {
        setSaveFeedback({
          kind: 'error',
          message: error instanceof Error ? error.message : 'Unable to save story.',
        });
      }
    }
  };

  const exportStoryMarkdown = () => {
    if (!activeStoryText?.trim()) {
      setSaveFeedback({ kind: 'error', message: 'Generate or select a story version before exporting.' });
      return;
    }
    try {
      const markdown = formatStoryMarkdown({
        title: storyTitle,
        premise,
        providerLabel,
        wordCount,
        chapterCount,
        sourceJobId,
        text: activeStoryText,
      });
      const filename = `${slugify(storyTitle || 'story')}.md`;
      if (typeof Blob === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
        setSaveFeedback({ kind: 'exported', message: `Prepared Markdown export for ${filename}.` });
        return;
      }
      downloadBlob(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }), filename);
      setSaveFeedback({ kind: 'exported', message: `Exported ${filename}.` });
    } catch (error) {
      setSaveFeedback({ kind: 'error', message: error instanceof Error ? error.message : 'Unable to export story.' });
    }
  };

  return { trashActiveStory, saveStoryDraft, exportStoryMarkdown };
}
