import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import type { OmnixModuleDefinition } from '../../app/modules';
import { audiobookApi } from './audiobookApi';
import type { ProjectSummary } from './audiobookTypes';
import { ACTIVE_RENDER_STATUSES, QUEUED_RENDER_STATUSES, RUNNING_RENDER_STATUSES, libraryCardStatus, pipelineJobProgress, pipelineJobProgressPercent, renderJobProgress } from './audiobookWorkspaceModel';
import type { useAudiobookState } from './useAudiobookState';

/** Projects, the open project, voices, exports and the source library, with render progress. */
export function useAudiobookProjectData(ws: { module: OmnixModuleDefinition } & ReturnType<typeof useAudiobookState>) {
  const queryClient = useQueryClient();
  const {
    busy, ebookLibraryOpen, projectId, projectSettingsOpen, selectedVoiceId, setProjectSettingsOpen,
    setSelectedVoiceId, setSourceLibraryOpen, setSourceUploadProjectId, sourceUploadProjectId,
  } = ws;

  const projectsQuery = useQuery({
    queryKey: ['audiobook', 'projects'],
    queryFn: async () => {
      const projects: ProjectSummary[] = [];
      let page: ProjectSummary[];
      do {
        ({ projects: page } = await audiobookApi.projects(projects.length));
        projects.push(...page);
      } while (page.length === 100);
      return { projects };
    },
    refetchInterval: 5000,
  });

  const libraryProjectQueries = useQueries({
    queries: (ebookLibraryOpen ? (projectsQuery.data?.projects ?? []) : []).map((item) => ({
      queryKey: ['audiobook', 'project', item.id],
      queryFn: () => audiobookApi.project(item.id),
      staleTime: 5_000,
    })),
  });

  const projectQuery = useQuery({
    queryKey: ['audiobook', 'project', projectId],
    queryFn: () => audiobookApi.project(projectId!),
    enabled: Boolean(projectId), refetchInterval: 3000,
  });

  const voicesQuery = useQuery({
    queryKey: ['audiobook', 'voices'],
    queryFn: () => audiobookApi.voices(),
    enabled: Boolean(projectId),
  });

  const modelQuery = useQuery({
    queryKey: ['audiobook', 'model'],
    queryFn: () => audiobookApi.currentModel(),
    staleTime: 30_000,
    enabled: false,
  });

  const exportsQuery = useQuery({
    queryKey: ['audiobook', 'exports', projectId],
    queryFn: () => audiobookApi.exports(projectId!),
    enabled: Boolean(projectId), refetchInterval: 5000,
  });

  useEffect(() => {
    if (!selectedVoiceId && voicesQuery.data?.voices[0]) setSelectedVoiceId(voicesQuery.data.voices[0].id);
  }, [selectedVoiceId, setSelectedVoiceId, voicesQuery.data?.voices]);

  const sourceLibraryQuery = useQuery({
    queryKey: ['audiobook', 'source-library'],
    queryFn: () => audiobookApi.sourceLibrary(),
    enabled: false,
  });

  function openSourceLibrary(): void {
    setSourceLibraryOpen(true);
    void sourceLibraryQuery.refetch();
  }

  const project = projectId ? projectQuery.data : undefined;

  useEffect(() => {
    if (busy || !projectSettingsOpen || project?.id !== sourceUploadProjectId) return;
    const sourceHeading = document.getElementById('audiobook-source-settings-title');
    sourceHeading?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    sourceHeading?.focus();
    setSourceUploadProjectId(null);
  }, [busy, project?.id, projectSettingsOpen, setSourceUploadProjectId, sourceUploadProjectId]);

  function openSourceUpload(targetProjectId: string): void {
    setProjectSettingsOpen(true);
    setSourceUploadProjectId(targetProjectId);
  }

  const renderJobs = project?.render_jobs ?? [];

  const renderChapterIds = new Set(
    renderJobs.map((job) => job.chapter_id).filter((chapterId): chapterId is string => Boolean(chapterId)),
  );

  const renderChapterCount = renderJobs.length > 0 ? renderChapterIds.size : (project?.chapters.length ?? 0);

  const skippedRenderChapterCount = project && renderJobs.length > 0
    ? Math.max(0, project.chapters.length - renderChapterCount)
    : 0;

  const completedRenderChapters = project
    ? renderJobs.length === 0 && ['ready_to_export', 'exported'].includes(project.state)
      ? project.chapters.length
      : renderJobs.filter((job) => job.status === 'completed').length
    : 0;

  const renderProgressPercent = renderJobs.length > 0
    ? Math.round(renderJobs.reduce(
      (total, job) => total + renderJobProgress(job, project?.state ?? ''),
      0,
    ) / renderJobs.length)
    : project && ['ready_to_export', 'exported'].includes(project.state) && project.chapters.length > 0
      ? 100
      : 0;

  const isPolicySkippedChapter = (chapterId: string): boolean =>
    renderJobs.length > 0 && !renderChapterIds.has(chapterId);

  const queuedRenderJobs = renderJobs.filter((job) => QUEUED_RENDER_STATUSES.has(job.status)).length;
  const runningRenderJobs = renderJobs.filter((job) => RUNNING_RENDER_STATUSES.has(job.status)).length;
  const pausedRenderJobs = renderJobs.filter((job) => job.status === 'paused').length;
  const failedRenderJobs = renderJobs.filter((job) => ['failed', 'dead_letter'].includes(job.status)).length;
  const retryableRenderJobs = renderJobs.filter((job) => job.can_retry).length;
  const cacheHits = renderJobs.reduce((total, job) => total + (job.progress?.cache_hits ?? 0), 0);
  const proposedSpeakers = project?.speakers.filter((speaker) => speaker.status === 'proposed') ?? [];
  const castableSpeakers = project?.speakers ?? [];
  const assignedVoiceCount = castableSpeakers.filter((speaker) => speaker.casting !== null).length;
  const castLabel = castableSpeakers.length ? `${castableSpeakers.length} cast members` : 'No cast detected';

  const renderStateLabel = failedRenderJobs > 0
    ? 'Needs attention'
    : project && renderChapterCount > 0 && completedRenderChapters === renderChapterCount
      ? 'Complete'
      : project?.state === 'ready_to_render' && renderJobs.length === 0
        ? 'Not started'
        : project?.state === 'rendering'
          ? (pausedRenderJobs > 0 && runningRenderJobs === 0 ? 'Paused' : 'Rendering')
          : project?.state === 'mastering'
            ? 'Mastering'
            : 'Waiting';

  const selectedVoice = voicesQuery.data?.voices.find((voice) => voice.id === selectedVoiceId) ?? voicesQuery.data?.voices[0];
  const modelRevision = modelQuery.data?.model_revision ?? '';

  async function getInstalledModelRevision(): Promise<string> {
    const identity = await queryClient.fetchQuery({
      queryKey: ['audiobook', 'model'],
      queryFn: () => audiobookApi.currentModel(),
      staleTime: 30_000,
    });
    return identity.model_revision;
  }

  const libraryProjects = (projectsQuery.data?.projects ?? []).map((item, index) => libraryProjectQueries[index]?.data ?? item);

  return {
    projectsQuery, libraryProjectQueries, projectQuery, voicesQuery, modelQuery, exportsQuery, sourceLibraryQuery,
    openSourceLibrary, project, openSourceUpload, renderJobs, renderChapterIds, renderChapterCount,
    skippedRenderChapterCount, completedRenderChapters, renderProgressPercent, isPolicySkippedChapter,
    queuedRenderJobs, runningRenderJobs, pausedRenderJobs, failedRenderJobs, retryableRenderJobs, cacheHits,
    proposedSpeakers, castableSpeakers, assignedVoiceCount, castLabel, renderStateLabel, selectedVoice,
    modelRevision, getInstalledModelRevision, libraryProjects,
  };
}

/** The filtered library, the open chapter and its spans, and classification readiness. */
export function useAudiobookLibraryData(ws: { module: OmnixModuleDefinition } & ReturnType<typeof useAudiobookState> & ReturnType<typeof useAudiobookProjectData>) {
  const queryClient = useQueryClient();
  const {
    chapterId, classificationRules, ebookLibraryFilter, ebookLibraryFormat, ebookLibrarySearch, ebookLibrarySort,
    libraryProjects, project, projectId, selectedLibraryProjectId, selectedSpanId, setExpandedSpanId,
    setProjectAuthor, setProjectTitle, setSelectedPronunciation, setSpeakerFilter, spanEdits, speakerFilter,
  } = ws;

  const filteredEbookProjects = [...libraryProjects]
    .filter((item) => {
      const search = ebookLibrarySearch.trim().toLowerCase();
      const matchesSearch = !search || `${item.title} ${item.author} ${item.language} ${item.source_format ?? ''}`.toLowerCase().includes(search);
      const status = libraryCardStatus(item);
      const matchesFormat = ebookLibraryFormat === 'all' || item.source_format === ebookLibraryFormat;
      const matchesFilter = ebookLibraryFilter === 'all'
        || (ebookLibraryFilter === 'ready' && status === 'Ready to Export')
        || (ebookLibraryFilter === 'ready-to-render' && status === 'Ready to Render')
        || (ebookLibraryFilter === 'completed' && status === 'Completed')
        || (ebookLibraryFilter === 'in-progress' && status === 'In Production')
        || (ebookLibraryFilter === 'review' && status === 'In Review')
        || (ebookLibraryFilter === 'attention' && status === 'Needs Attention')
        || (ebookLibraryFilter === 'draft' && status === 'Draft');
      return matchesSearch && matchesFormat && matchesFilter;
    })
    .sort((left, right) => ebookLibrarySort === 'title'
      ? left.title.localeCompare(right.title)
      : ebookLibrarySort === 'author'
        ? left.author.localeCompare(right.author)
        : 0);

  const selectedLibraryProject = filteredEbookProjects.find((item) => item.id === selectedLibraryProjectId)
    ?? filteredEbookProjects[0]
    ?? null;

  const libraryInProductionCount = libraryProjects.filter((item) => libraryCardStatus(item) === 'In Production').length;
  const libraryReadyCount = libraryProjects.filter((item) => libraryCardStatus(item) === 'Ready to Export').length;
  const libraryRuntimeSeconds = libraryProjects.reduce((total, item) => total + (item.estimated_runtime_seconds ?? 0), 0);
  const libraryStorageBytes = libraryProjects.reduce((total, item) => total + (item.source_size_bytes ?? 0), 0);

  useEffect(() => {
    if (!project) return;
    setProjectTitle(project.title);
    setProjectAuthor(project.author);
    // Copies the title and author into the form when they change, not on every poll of the project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.title, project?.author]);

  useEffect(() => {
    const rail = document.getElementById('audiobook-library-rail');
    if (rail) rail.scrollTop = 0;
  }, [projectId]);

  const activeChapterId = project?.chapters.find((chapter) => chapter.id === chapterId)?.id ?? project?.chapters[0]?.id ?? null;

  useEffect(() => {
    setExpandedSpanId(null);
    setSpeakerFilter('all');
    setSelectedPronunciation(null);
  }, [projectId, activeChapterId, setExpandedSpanId, setSpeakerFilter, setSelectedPronunciation]);

  const chapterQuery = useQuery({
    queryKey: ['audiobook', 'chapter', projectId, activeChapterId],
    queryFn: () => audiobookApi.chapter(projectId!, activeChapterId!),
    enabled: Boolean(projectId && activeChapterId),
  });

  const chapterContentRevision = JSON.stringify([
    project?.current_source_revision_id,
    project?.state,
    project?.pipeline_jobs?.filter((job) => ['audiobook.ingest', 'audiobook.analyze'].includes(job.type ?? ''))
      .map((job) => [job.id, job.status]),
  ]);

  const previousChapterRevision = useRef<{ projectId: string; revision: string } | null>(null);

  useEffect(() => {
    if (!project?.id) return;
    const previous = previousChapterRevision.current;
    previousChapterRevision.current = { projectId: project.id, revision: chapterContentRevision };
    if (previous?.projectId === project.id && previous.revision !== chapterContentRevision) {
      // Project polling publishes background changes; chapter data has its own cache.
      // Invalidate inactive chapters too, so opening one shows the latest assignments.
      void queryClient.invalidateQueries({ queryKey: ['audiobook', 'chapter', project.id] });
    }
  }, [project?.id, chapterContentRevision, queryClient]);

  const selectedChapter = chapterQuery.data;
  const selectedSpan = selectedChapter?.spans.find((span) => span.id === selectedSpanId) ?? selectedChapter?.spans[0];

  const canRender = project?.render_readiness?.ready ?? (project?.state === 'ready_to_render' ||
    (project?.state === 'rendering' && project.render_jobs.length > 0 &&
      project.render_jobs.every((job) => ['completed', 'failed', 'canceled'].includes(job.status))));

  const latestPipelineJob = project?.pipeline_jobs?.[0];

  const reclassificationJob = project?.pipeline_jobs?.find((job) =>
    job.type === 'audiobook.analyze');

  const extractionJob = project?.pipeline_jobs?.find((job) => job.type === 'audiobook.ingest');
  const extractionRunning = Boolean(extractionJob && ACTIVE_RENDER_STATUSES.has(extractionJob.status));

  const rulesNeedExtraction = Boolean(project &&
    (classificationRules[project.id] ?? project.classification_rules ?? '').trim() !==
    (project.quote_extraction_rules ?? project.classification_rules ?? ''));

  const reclassificationRunning = Boolean(
    reclassificationJob && ACTIVE_RENDER_STATUSES.has(reclassificationJob.status),
  );

  const classificationBlockedReason = extractionRunning
    ? 'Wait for quote extraction to finish, then open Span review and review the quotes before classifying.'
    : reclassificationRunning
      ? 'Classification is already in progress. Check its progress controls; resume it if paused or cancel it before starting again.'
      : !project?.current_source_revision_id
        ? 'Open Book source and add a book, then review the extracted quote spans before classifying.'
        : rulesNeedExtraction
          ? 'Rules changed. Open Book source, click Extract quotes, and review the new spans before classifying.'
          : null;

  const reclassificationProgress = pipelineJobProgress(reclassificationJob);
  const latestPipelineProgress = pipelineJobProgressPercent(latestPipelineJob);

  const failedPipelineJob = latestPipelineJob &&
    ['failed', 'canceled', 'stale', 'dead_letter'].includes(latestPipelineJob.status) &&
    latestPipelineJob.can_retry !== false ? latestPipelineJob : null;

  const selectedEdit = selectedSpan && (spanEdits[selectedSpan.id] ?? {
    speaker_id: selectedSpan.annotation?.speaker_id ?? '',
    role: selectedSpan.annotation?.role ?? selectedSpan.structural_kind,
    delivery: selectedSpan.annotation?.delivery ?? '',
  });

  const visibleSpans = selectedChapter?.spans.filter((span) =>
    speakerFilter === 'all' || (speakerFilter === 'unassigned'
      ? !span.annotation?.speaker_id
      : span.annotation?.speaker_id === speakerFilter)) ?? [];

  return {
    filteredEbookProjects, selectedLibraryProject, libraryInProductionCount, libraryReadyCount,
    libraryRuntimeSeconds, libraryStorageBytes, activeChapterId, chapterQuery, chapterContentRevision,
    previousChapterRevision, selectedChapter, selectedSpan, canRender, latestPipelineJob, reclassificationJob,
    extractionJob, extractionRunning, rulesNeedExtraction, reclassificationRunning, classificationBlockedReason,
    reclassificationProgress, latestPipelineProgress, failedPipelineJob, selectedEdit, visibleSpans,
  };
}
