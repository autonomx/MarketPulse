import './StorytellerWorkspace.css';
import './StorytellerSidebar.css';
import './StoryMode.css';
import './StoryThemeThumbnails.css';
import './StoryAudioEnhancer.css';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { omnixApiClient, type JobRecord } from '../../api/client';
import type { OmnixModuleDefinition } from '../../app/modules';
import { WorkspacePanel } from '../../design/primitives';
import { StoryExtraPanels } from './StoryExtraPanels';
import { StorytellerToolbox } from './StorytellerToolbox';
import { publishStorySnapshot } from './storySnapshotStore';
import { StoryControls } from './StoryControls';
import { StoryLibrary } from './StoryLibrary';
import { StoryActionBar, StoryEmptyState, StoryModePanel, StoryModeSwitch, StoryOutline, StoryProjectHeader, StoryText, StoryVersions } from './StoryPanels';
import { fullJobOutputText, libraryJobId, readSavedStoryDraft, readTrashedStoryLibraryItems } from './storyLibraryModel';
import { SaveFeedback, SavedStoryDraft, StoryGenerationRequest, StoryLibrarySection, StoryWorkspaceMode, StorytellerFormValues, TrashedStoryLibraryItem, countWords, latestStoryTitleOverride, llmCapableProviders, providerDisplayName, storyDisplayTitle, styleOptions, jobInputString } from './storyModel';
import { buildStorySceneAddition, deriveStoryOutline, isSceneAppendRequest, lastStoryPage, storyParagraphs, storyTextBlocks, suggestedStoryMoves } from './storyTextModel';
import { useStorySceneAdditions } from './useStorySceneAdditions';
import { useStoryLibrary } from './useStoryLibrary';
import { useStoryPersistence } from './useStoryPersistence';
import { useStoryRequests } from './useStoryRequests';
import { storyJobRequest } from './storyModel';
import { GatewayErrorNotice } from '../../shared/GatewayErrorNotice';

export function StorytellerWorkspace({ module }: { module: OmnixModuleDefinition }) {
  const queryClient = useQueryClient();
  const [workspaceMode, setWorkspaceMode] = useState<StoryWorkspaceMode>('writing');
  const [activeLibrarySection, setActiveLibrarySection] = useState<StoryLibrarySection>('drafts');
  const [selectedTone, setSelectedTone] = useState('Cozy');
  const [writingStyle, setWritingStyle] = useState(styleOptions[0]);
  const [selectedChapter, setSelectedChapter] = useState(1);
  const [selectedLibraryItemId, setSelectedLibraryItemId] = useState<string | null>(null);
  const [isNewDraft, setIsNewDraft] = useState(false);
  const [savedDraft, setSavedDraft] = useState<SavedStoryDraft | null>(() => readSavedStoryDraft());
  const [trashedLibraryItems, setTrashedLibraryItems] = useState<TrashedStoryLibraryItem[]>(() => readTrashedStoryLibraryItems());
  const [saveFeedback, setSaveFeedback] = useState<SaveFeedback | null>(null);
  const { storySceneAdditions, addStorySceneAddition } = useStorySceneAdditions();
  const [storyModeResponse, setStoryModeResponse] = useState('');

  const providersQuery = useQuery({
    queryKey: ['platform', 'providers'],
    queryFn: () => omnixApiClient.listProviders(),
  });
  const { register, handleSubmit, reset, watch, formState: { errors } } = useForm<StorytellerFormValues>({
    defaultValues: { providerId: '', title: '', premise: '' },
  });

  const createJobMutation = useMutation<JobRecord, Error, StoryGenerationRequest>({
    mutationFn: (request) => omnixApiClient.createJob(storyJobRequest(request, { tone: selectedTone, writingStyle, chapter: selectedChapter })),
    onSuccess: async (job, request) => {
      reset({ providerId: request.values.providerId, title: request.values.title, premise: request.values.premise });
      setIsNewDraft(false);
      const completedText = fullJobOutputText(job);
      if (job.status === 'completed' && completedText && isSceneAppendRequest(request)) {
        const addition = buildStorySceneAddition(job, request);
        if (addition) {
          addStorySceneAddition(addition);
          setSelectedLibraryItemId(addition.sourceItemId);
          setSelectedChapter(addition.chapterNumber);
          setActiveLibrarySection(addition.sourceItemId.startsWith('draft:') ? 'drafts' : 'stories');
          setSaveFeedback({ kind: 'saved', message: `Added ${addition.title} to ${addition.storyTitle ?? request.sourceStoryTitle ?? 'the story'}.` });
        }
      } else if (job.status === 'completed' && completedText) {
        setSelectedLibraryItemId(libraryJobId(job.id));
        setActiveLibrarySection('stories');
      }
      if (request.interactionMode === 'story') {
        setStoryModeResponse('');
        setWorkspaceMode('story');
      }
      await queryClient.invalidateQueries({ queryKey: ['platform', 'jobs'] });
      await queryClient.invalidateQueries({ queryKey: ['platform', 'assets'] });
    },
  });

  const watchedTitle = watch('title');
  const watchedPremise = watch('premise');
  const watchedProvider = watch('providerId');
  const storyProviders = useMemo(() => llmCapableProviders(providersQuery.data), [providersQuery.data]);
  const {
    libraryError, storyJobs, libraryStoryJobs, libraryItems, trashItems,
    activeLibraryItem, activeJob, activeAsset, assetContentQuery, activeItemSceneAdditions, activeStoryText,
  } = useStoryLibrary({
    createdJob: createJobMutation.data ?? null,
    savedDraft,
    trashedLibraryItems,
    storySceneAdditions,
    isNewDraft,
    selectedLibraryItemId,
    setSelectedLibraryItemId,
  });
  const storyTitle = storyDisplayTitle(
    latestStoryTitleOverride(activeItemSceneAdditions) || activeLibraryItem?.title || watchedTitle,
    activeStoryText,
  );
  const premise = activeLibraryItem?.source === 'draft'
    ? savedDraft?.premise ?? watchedPremise
    : watchedPremise || jobInputString(activeJob, 'premise') || '';
  const providerLabel = providerDisplayName(
    storyProviders,
    watchedProvider || jobInputString(activeJob, 'provider_id') || '',
    activeLibraryItem?.source === 'draft' ? savedDraft?.providerLabel ?? null : null,
  );
  const sourceJobId = activeJob?.id ?? activeLibraryItem?.jobId ?? null;
  const outline = useMemo(() => deriveStoryOutline(activeStoryText, storyTitle), [activeStoryText, storyTitle]);
  // The tool panels (audio, cast, chapters, document) read the story as it is shown.
  const renderedStoryText = useMemo(() => {
    if (!activeStoryText) return '';
    if (workspaceMode === 'story') {
      const page = lastStoryPage(activeStoryText);
      return page ? storyParagraphs(page).join('\n') : '';
    }
    return storyTextBlocks(activeStoryText, outline).map((block) => block.text).join('\n');
  }, [activeStoryText, outline, workspaceMode]);
  useEffect(() => {
    publishStorySnapshot(storyTitle, renderedStoryText);
  }, [storyTitle, renderedStoryText]);
  const activeChapter = outline.find((chapter) => chapter.number === selectedChapter) ?? outline[0] ?? null;
  const chapterCount = outline.length || Math.max(1, Math.min(12, libraryStoryJobs.length || selectedChapter));
  const wordCount = countWords(activeStoryText ?? watchedPremise ?? '');
  const readingMinutes = Math.max(1, Math.ceil(wordCount / 220));
  const submitStatus = createJobMutation.isPending
    ? 'queueing'
    : createJobMutation.isError
      ? 'error'
      : createJobMutation.data?.status ?? 'ready';
  const canPersistStory = Boolean(activeStoryText?.trim());
  const storyModeChoices = useMemo(() => suggestedStoryMoves(activeStoryText, storyTitle), [activeStoryText, storyTitle]);
  const { submitStoryRequest, submitQuickAction, submitStoryModeMove, addChapterToActiveStory } = useStoryRequests({
    createJobMutation,
    watched: { title: watchedTitle, premise: watchedPremise, providerId: watchedProvider },
    story: { title: storyTitle, premise, text: activeStoryText, sourceJobId, outline },
    activeLibraryItem,
    setSaveFeedback,
    addStorySceneAddition,
    setSelectedChapter,
  });
  const { trashActiveStory, saveStoryDraft, exportStoryMarkdown } = useStoryPersistence({
    story: { title: storyTitle, premise, providerLabel, wordCount, chapterCount, sourceJobId, text: activeStoryText },
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
  });

  useEffect(() => {
    if (outline.length && !outline.some((chapter) => chapter.number === selectedChapter)) {
      setSelectedChapter(outline[0].number);
    }
  }, [outline, selectedChapter]);

  const selectLibraryItem = (itemId: string) => {
    const item = libraryItems.find((entry) => entry.id === itemId);
    setIsNewDraft(false);
    setSelectedLibraryItemId(itemId);
    setSelectedChapter(1);
    setSaveFeedback(null);
    if (item?.source === 'draft') setActiveLibrarySection('drafts');
    if (item?.source === 'job' || item?.source === 'asset') setActiveLibrarySection('stories');
  };

  const startNewDraft = () => {
    reset({ providerId: watchedProvider, title: '', premise: '' });
    setActiveLibrarySection('drafts');
    setIsNewDraft(true);
    setSelectedLibraryItemId(null);
    setSelectedChapter(1);
    setWorkspaceMode('writing');
    setSaveFeedback({ kind: 'saved', message: 'New draft ready. Add a premise to begin.' });
  };

  const selectOutlineTarget = (chapterNumber: number, targetId: string) => {
    setSelectedChapter(chapterNumber);
    scrollToOutlineTarget(chapterNumber, targetId);
  };

  return (
    <WorkspacePanel labelledBy="module-title">
      <h2 id="module-title" className="workspace-module-heading">{module.label}</h2>
      <div className="storyteller-workspace" aria-labelledby="module-title">
        <StoryLibrary
          activeItemId={activeLibraryItem?.id ?? null}
          activeSection={activeLibrarySection}
          items={libraryItems}
          onNewDraft={startNewDraft}
          onSectionChange={setActiveLibrarySection}
          onSelect={selectLibraryItem}
          onTrashActiveItem={trashActiveStory}
          trashItems={trashItems}
        />
        <main className="storyteller-stage">
          <h2 className="storyteller-module-title">{module.label}</h2>
          <GatewayErrorNotice label="Stories and providers" errors={[libraryError, providersQuery.error]} />
          <StoryProjectHeader
            canPersistStory={canPersistStory}
            chapterCount={chapterCount}
            moduleRoute={module.route}
            onExport={exportStoryMarkdown}
            onSave={saveStoryDraft}
            premise={premise}
            providerLabel={providerLabel}
            saveFeedback={saveFeedback}
            title={storyTitle}
            wordCount={wordCount}
          />
          <StorytellerToolbox />
          <StoryExtraPanels />
          <StoryModeSwitch mode={workspaceMode} onChange={setWorkspaceMode} />
          {workspaceMode === 'story' ? (
            <StoryModePanel
              activeAsset={activeAsset}
              activeChapterLabel={activeChapter?.label ?? `Chapter ${selectedChapter}`}
              activeStoryText={activeStoryText}
              assetError={assetContentQuery.error}
              isAssetLoading={assetContentQuery.isLoading || assetContentQuery.isFetching}
              module={module}
              onMove={submitStoryModeMove}
              pending={createJobMutation.isPending}
              response={storyModeResponse}
              setResponse={setStoryModeResponse}
              storyTitle={storyTitle}
              suggestedMoves={storyModeChoices}
            />
          ) : (
            <>
              <div className="storyteller-compose-grid">
                <section className="storyteller-manuscript" aria-label="Story manuscript">
                  <div className="storyteller-manuscript-meta">
                    <span>{activeChapter?.label ?? `Chapter ${selectedChapter}`}</span>
                    <span>{readingMinutes} min read</span>
                  </div>
                  <h2>{activeChapter?.title ?? storyTitle}</h2>
                  <div className="storyteller-flourish" aria-hidden="true"><span /><strong>◇</strong><span /></div>
                  {activeStoryText ? (
                    <StoryText outline={outline} text={activeStoryText} />
                  ) : (
                    <StoryEmptyState
                      activeAsset={activeAsset}
                      assetError={assetContentQuery.error}
                      isAssetLoading={assetContentQuery.isLoading || assetContentQuery.isFetching}
                      module={module}
                      storyTitle={storyTitle}
                    />
                  )}
                </section>
                <StoryControls
                  createJobMutation={createJobMutation}
                  errors={errors}
                  handleSubmit={handleSubmit}
                  latestJob={storyJobs[0] ?? null}
                  onAddChapter={addChapterToActiveStory}
                  onGenerate={(values) => submitStoryRequest(values, 'draft')}
                  providers={storyProviders}
                  register={register}
                  selectedChapter={selectedChapter}
                  selectedTone={selectedTone}
                  setSelectedChapter={setSelectedChapter}
                  setSelectedTone={setSelectedTone}
                  setWritingStyle={setWritingStyle}
                  submitStatus={submitStatus}
                  writingStyle={writingStyle}
                />
              </div>
              <StoryActionBar disabled={createJobMutation.isPending} onAction={submitQuickAction} />
              <StoryVersions
                activeJobId={activeJob?.id ?? null}
                jobs={libraryStoryJobs}
                onSelect={(jobId) => selectLibraryItem(libraryJobId(jobId))}
              />
            </>
          )}
        </main>
        <StoryOutline chapters={outline} selectedChapter={selectedChapter} onAddChapter={addChapterToActiveStory} onSelect={selectOutlineTarget} />
      </div>
    </WorkspacePanel>
  );
}

/** Scrolls the manuscript to an outline entry, or its chapter, or the manuscript itself. */
function scrollToOutlineTarget(chapterNumber: number, targetId: string): void {
  const target = (document.getElementById(targetId) ??
    document.getElementById(`story-chapter-${chapterNumber}`) ??
    document.querySelector('[aria-label="Story manuscript"]')) as
    | (HTMLElement & { scrollIntoView?: (options?: ScrollIntoViewOptions) => void })
    | null;
  target?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
}
