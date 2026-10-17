import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { omnixApiClient, type JobRecord } from '../../api/client';
import { buildStoryLibraryItems, fullJobOutputText, mergeTrashedStoryLibraryItems } from './storyLibraryModel';
import { SavedStoryDraft, StoryAssetSummary, StorySceneAddition, TrashedStoryLibraryItem, includeMutationJob } from './storyModel';
import { applyStorySceneAdditions, isStorySceneAppendJob } from './storyTextModel';

type StoryLibraryOptions = {
  /** The job just queued or finished, shown before the job list refreshes. */
  createdJob: JobRecord | null;
  savedDraft: SavedStoryDraft | null;
  trashedLibraryItems: TrashedStoryLibraryItem[];
  storySceneAdditions: StorySceneAddition[];
  isNewDraft: boolean;
  selectedLibraryItemId: string | null;
  setSelectedLibraryItemId: (itemId: string | null) => void;
};

/**
 * The story library (the draft, generated stories, saved assets and the
 * trash) and the active story: its job or asset and its text, with the
 * scenes added in this browser.
 */
export function useStoryLibrary({
  createdJob,
  savedDraft,
  trashedLibraryItems,
  storySceneAdditions,
  isNewDraft,
  selectedLibraryItemId,
  setSelectedLibraryItemId,
}: StoryLibraryOptions) {
  const jobsQuery = useQuery({ queryKey: ['platform', 'jobs'], queryFn: () => omnixApiClient.listJobs() });
  const assetsQuery = useQuery({ queryKey: ['platform', 'assets'], queryFn: () => omnixApiClient.listAssets() });
  const queriedStoryJobs = useMemo(() => jobsQuery.data?.jobs.filter((job) => job.module === 'storyteller') ?? [], [jobsQuery.data]);
  const storyJobs = useMemo(
    () => includeMutationJob(queriedStoryJobs, createdJob),
    [queriedStoryJobs, createdJob],
  );
  const completedStoryJobs = storyJobs.filter((job) => job.status === 'completed' && fullJobOutputText(job));
  const libraryStoryJobs = completedStoryJobs.filter((job) =>
    !isStorySceneAppendJob(job) && !storySceneAdditions.some((addition) => addition.sourceJobId === job.id));
  const storyAssets = useMemo(
    () => (assetsQuery.data?.assets.filter((asset) => asset.type === 'story' || asset.type === 'export') ?? []) as StoryAssetSummary[],
    [assetsQuery.data],
  );
  const allLibraryItems = useMemo(
    () => buildStoryLibraryItems(savedDraft, libraryStoryJobs, storyAssets),
    [savedDraft, libraryStoryJobs, storyAssets],
  );
  const trashItems = useMemo(
    () => mergeTrashedStoryLibraryItems(trashedLibraryItems, allLibraryItems),
    [trashedLibraryItems, allLibraryItems],
  );
  const libraryItems = useMemo(
    () => allLibraryItems.filter((item) => !trashItems.some((trashedItem) => trashedItem.id === item.id)),
    [allLibraryItems, trashItems],
  );
  const selectableLibraryItems = useMemo(
    () => [...libraryItems, ...trashItems],
    [libraryItems, trashItems],
  );
  const activeLibraryItem = isNewDraft
    ? null
    : selectableLibraryItems.find((item) => item.id === selectedLibraryItemId) ??
      libraryItems.find((item) => item.source === 'job') ??
      libraryItems.find((item) => item.source === 'draft') ??
      null;
  const activeJob = activeLibraryItem?.jobId
    ? completedStoryJobs.find((job) => job.id === activeLibraryItem.jobId) ?? null
    : null;
  const activeAsset = activeLibraryItem?.assetId
    ? (storyAssets).find((asset) => asset.id === activeLibraryItem.assetId) ?? null
    : null;
  const assetContentQuery = useQuery({
    queryKey: ['platform', 'assets', activeAsset?.id, 'content'],
    queryFn: () => omnixApiClient.getAssetContent(activeAsset?.id ?? ''),
    enabled: Boolean(activeAsset?.id),
    retry: false,
  });
  const activeAssetText = activeAsset && assetContentQuery.data?.asset.id === activeAsset.id
    ? assetContentQuery.data.content
    : null;
  const activeItemSceneAdditions = useMemo(
    () => activeLibraryItem ? storySceneAdditions.filter((addition) => addition.sourceItemId === activeLibraryItem.id) : [],
    [activeLibraryItem, storySceneAdditions],
  );
  const baseActiveStoryText = activeLibraryItem?.content ?? activeAssetText ?? null;
  const activeStoryText = useMemo(
    () => applyStorySceneAdditions(baseActiveStoryText, activeItemSceneAdditions),
    [baseActiveStoryText, activeItemSceneAdditions],
  );

  useEffect(() => {
    if (!selectedLibraryItemId) return;
    if (!selectableLibraryItems.some((item) => item.id === selectedLibraryItemId)) {
      setSelectedLibraryItemId(null);
    }
  }, [selectableLibraryItems, selectedLibraryItemId, setSelectedLibraryItemId]);

  return {
    libraryError: jobsQuery.error ?? assetsQuery.error,
    storyJobs,
    libraryStoryJobs,
    libraryItems,
    trashItems,
    activeLibraryItem,
    activeJob,
    activeAsset,
    assetContentQuery,
    activeItemSceneAdditions,
    activeStoryText,
  };
}
