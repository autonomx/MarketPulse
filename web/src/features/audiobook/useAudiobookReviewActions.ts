import { useQueryClient } from '@tanstack/react-query';
import { type FormEvent } from 'react';
import type { OmnixModuleDefinition } from '../../app/modules';
import { audiobookApi } from './audiobookApi';
import type { ChapterSummary, Speaker } from './audiobookTypes';
import { WorkspaceAsset, WorkspaceDocument, base, renderJobLabel, uploadCover, uploadSource } from './audiobookWorkspaceModel';
import type { useAudiobookState } from './useAudiobookState';
import type { useAudiobookProjectData } from './useAudiobookData';
import type { useAudiobookLibraryData } from './useAudiobookData';
import type { useAudiobookProjectActions } from './useAudiobookActions';

/** Review and production actions: chapters, documents, speakers, pronunciations, classification and rendering. */
export function useAudiobookReviewActions(ws: { module: OmnixModuleDefinition } & ReturnType<typeof useAudiobookState> & ReturnType<typeof useAudiobookProjectData> & ReturnType<typeof useAudiobookLibraryData> & ReturnType<typeof useAudiobookProjectActions>) {
  const queryClient = useQueryClient();
  const {
    action, classificationRules, excludePageRanges, exportsQuery, getInstalledModelRevision, isPolicySkippedChapter,
    navigateLibrary, project, projectId, reclassificationJob, selectedChapter, setChapterId,
    setExcludePageRanges, setSelectedPronunciation, setSelectedSpanId, setSourceTerm, setSpeakerName, setSpokenTerm,
    sourceTerm, speakerName, spokenTerm,
  } = ws;

  function chapterStatus(chapter: ChapterSummary): string {
    const render = project?.render_jobs.find((job) => job.chapter_id === chapter.id);
    if (project && isPolicySkippedChapter(chapter.id)) return 'Skipped';
    const renderLabel = renderJobLabel(render, project?.state ?? '');
    if (renderLabel === 'Completed') return 'Rendered';
    if (['Queued', 'Rendering', 'Retrying', 'Waiting', 'Cancelling', 'Paused'].includes(renderLabel)) return renderLabel === 'Queued' ? 'Queued' : 'In Progress';
    if (['Failed', 'Canceled', 'Stale'].includes(renderLabel)) return 'Needs attention';
    if (project?.review_issues.some((issue) => issue.chapter_id === chapter.id)) return 'In Review';
    return 'Draft';
  }

  function workspaceAssetList(): WorkspaceAsset[] {
    if (!project) return [];
    const cover = project.cover_asset_id ? [{
      id: `cover-${project.cover_asset_id}`, name: 'Project cover', type: 'Cover Art' as const,
      detail: 'Cover image', status: 'Used' as const, chapters: 'Current project cover',
      href: `${base}/projects/${encodeURIComponent(project.id)}/cover`, image: true, format: 'Image',
      assetId: project.cover_asset_id, deletable: true, createdAt: project.cover_created_at,
    }] : [];
    const source = project.current_source_revision_id ? [{
      id: `source-${project.current_source_revision_id}`, name: project.source_filename || `${project.title} (Manuscript)`, type: 'Document' as const,
      detail: `Manuscript · ${(project.source_format || 'source').toUpperCase()}`, status: 'Used' as const, chapters: `All ${project.chapters.length} chapters`,
      href: `${base}/projects/${encodeURIComponent(project.id)}/source/download`, format: (project.source_format || 'Source').toUpperCase(), deletable: false, createdAt: project.source_created_at, sizeBytes: project.source_size_bytes,
    }] : [];
    const exports = (exportsQuery.data?.exports ?? []).map((item) => ({
      id: `export-${item.id}`, name: `audiobook.${item.format}`, type: 'Audio' as const,
      detail: `${item.format.toUpperCase()} · Export`, status: 'Ready' as const, chapters: 'Entire book',
      href: `${base}/projects/${encodeURIComponent(project.id)}/exports/${encodeURIComponent(item.id)}/download`, format: item.format.toUpperCase(),
      assetId: item.asset_id, deletable: true, createdAt: item.asset_created_at ?? item.created_at, sizeBytes: item.byte_size,
    }));
    return [...cover, ...source, ...exports];
  }

  function workspaceDocumentList(): WorkspaceDocument[] {
    if (!project) return [];
    const preview = selectedChapter?.canonical_text || 'Open this document to preview the canonical manuscript.';
    return [
      ...(project.current_source_revision_id ? [{ id: 'manuscript', title: project.source_filename || `${project.title} (Manuscript)`, type: 'Manuscript', lastEdited: 'Current source', author: project.author || 'Unknown author', linked: `All (${project.chapters.length})`, status: 'Final' as const, preview, href: `${base}/projects/${encodeURIComponent(project.id)}/source/download` }] : []),
      ...(exportsQuery.data?.exports ?? []).map((item) => ({ id: `export-${item.id}`, title: `${item.format.toUpperCase()} Export Report`, type: 'Export Notes', lastEdited: new Date(item.created_at).toLocaleString(), author: project.author || 'Project export', linked: 'Entire book', status: 'Final' as const, preview: `Manifest ${item.manifest_hash}`, href: `${base}/projects/${encodeURIComponent(project.id)}/exports/${encodeURIComponent(item.id)}/report` })),
    ];
  }

  async function queueChapterPreview(chapter: ChapterSummary): Promise<void> {
    if (!project) return;
    const modelRevision = await getInstalledModelRevision();
    const detail = chapter.id === selectedChapter?.id ? selectedChapter : await queryClient.fetchQuery({
      queryKey: ['audiobook', 'chapter', project.id, chapter.id],
      queryFn: () => audiobookApi.chapter(project.id, chapter.id),
    });
    const span = detail.spans.find(
      (candidate) => Boolean(candidate.speech_plan?.tts_input_text?.trim()),
    );
    if (!span) {
      throw new Error('This chapter has no renderable speech under the current reading policy.');
    }
    await audiobookApi.preview(project.id, {
      chapter_id: chapter.id, span_id: span.id, model_revision: modelRevision.trim(),
    });
    setChapterId(chapter.id); setSelectedSpanId(span.id);
  }

  function pauseReclassification(): void {
    if (!project || !reclassificationJob) return;
    void action(
      () => audiobookApi.job(project!.id, reclassificationJob.id, 'pause'),
      'Text classification pause requested.',
    );
  }

  function resumeReclassification(): void {
    if (!project || !reclassificationJob) return;
    void action(
      () => audiobookApi.job(project!.id, reclassificationJob.id, 'resume'),
      'Text classification resumed.',
    );
  }

  function cancelReclassification(): void {
    if (!project || !reclassificationJob) return;
    void action(
      () => audiobookApi.job(project!.id, reclassificationJob.id, 'cancel'),
      'Text classification canceled.',
    );
  }

  function pauseAllRenderJobs(): void {
    if (!project) return;
    void action(() => audiobookApi.render(project.id, 'pause'), 'Render queue paused.');
  }

  function resumeAllRenderJobs(): void {
    if (!project) return;
    void action(() => audiobookApi.render(project.id, 'resume'), 'Render queue resumed.');
  }

  function stopAllRenderJobs(): void {
    if (!project) return;
    void action(() => audiobookApi.render(project.id, 'stop'), 'Render queue stopped.');
  }

  function startChapter(chapter: ChapterSummary): void {
    setChapterId(chapter.id);
    navigateLibrary('projects');
  }

  function importProjectFile(file: File): void {
    if (!project) return;
    const image = /^image\/(jpeg|png)$/.test(file.type) || /\.(jpe?g|png)$/i.test(file.name);
    void action(async () => {
      if (image) await uploadCover(project.id, file);
      else await uploadSource(project.id, file, excludePageRanges, classificationRules[project.id] ?? project.classification_rules ?? '');
      setExcludePageRanges('');
    }, image ? 'Cover saved.' : 'Document queued for extraction.');
  }

  function openDocument(document: WorkspaceDocument): void {
    if (document.id.startsWith('export-')) navigateLibrary('exports');
    else navigateLibrary('projects');
  }

  function submitSpeaker(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!projectId || !speakerName.trim()) return;
    void action(async () => {
      await audiobookApi.createSpeaker(projectId, { canonical_name: speakerName.trim() });
      setSpeakerName('');
    }, 'Speaker added.');
  }

  function confirmProposedSpeaker(speaker: Speaker): void {
    if (!projectId) return;
    void action(
      () => audiobookApi.createSpeaker(projectId, {
        canonical_name: speaker.canonical_name,
      }),
      `${speaker.canonical_name} added to the cast. Review spans are now assigned to this speaker.`,
    );
  }

  /** Confirms every detected speaker, one after another; any confirmed before a failure stay confirmed. */
  function confirmAllProposedSpeakers(speakers: readonly Speaker[]): void {
    if (!projectId || speakers.length === 0) return;
    void action(async () => {
      for (const speaker of speakers) {
        await audiobookApi.createSpeaker(projectId, { canonical_name: speaker.canonical_name });
      }
    }, `${speakers.length} detected speaker${speakers.length === 1 ? '' : 's'} added to the cast. Review spans are now assigned to them.`);
  }

  function rejectProposedSpeaker(speaker: Speaker): void {
    if (!projectId) return;
    void action(
      () => audiobookApi.rejectSpeaker(projectId, speaker.id),
      `${speaker.canonical_name} dismissed from the detected cast.`,
    );
  }

  function submitPronunciation(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!projectId || !sourceTerm.trim() || !spokenTerm.trim()) return;
    void action(async () => {
      await audiobookApi.setPronunciation(projectId, { source_term: sourceTerm.trim(), spoken_term: spokenTerm.trim() });
      setSourceTerm(''); setSpokenTerm(''); setSelectedPronunciation(null);
    }, 'Pronunciation saved. Affected speech plans will use the new form.');
  }

  return {
    chapterStatus, workspaceAssetList, workspaceDocumentList, queueChapterPreview, pauseReclassification,
    resumeReclassification, cancelReclassification, pauseAllRenderJobs, resumeAllRenderJobs, stopAllRenderJobs,
    startChapter, importProjectFile, openDocument, submitSpeaker, confirmProposedSpeaker, confirmAllProposedSpeakers, rejectProposedSpeaker,
    submitPronunciation,
  };
}
