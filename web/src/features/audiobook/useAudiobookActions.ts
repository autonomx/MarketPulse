import { useQueryClient } from '@tanstack/react-query';
import { type FormEvent } from 'react';
import type { OmnixModuleDefinition } from '../../app/modules';
import { audiobookApi } from './audiobookApi';
import type { ProjectSummary, ChapterSummary } from './audiobookTypes';
import { LibrarySection, deleteAudiobookAsset, deleteAudiobookProject, uploadSource } from './audiobookWorkspaceModel';
import type { useAudiobookState } from './useAudiobookState';
import type { useAudiobookProjectData } from './useAudiobookData';
import type { useAudiobookLibraryData } from './useAudiobookData';

/** Project actions: creating, opening, deleting, previews and library navigation. */
export function useAudiobookProjectActions(ws: { module: OmnixModuleDefinition } & ReturnType<typeof useAudiobookState> & ReturnType<typeof useAudiobookProjectData> & ReturnType<typeof useAudiobookLibraryData>) {
  const queryClient = useQueryClient();
  const {
    assetDeleteConfirmation, author, createClassificationRules, createExcludePageRanges, getInstalledModelRevision,
    language, openSourceUpload, pendingSource, project, selectedChapter, selectedEdit, selectedSpan,
    setAssetDeleteConfirmation, setAuthor, setBusy, setChapterId, setClassificationRules,
    setCreateClassificationRules, setCreateExcludePageRanges, setDeleteConfirmationOpen, setEbookLibraryOpen,
    setError, setExcludePageRanges, setLanguage, setLibrarySection, setMobileRail, setNotice, setPendingSource,
    setProjectAuthor, setProjectId, setProjectSettingsOpen, setProjectTitle, setSelectedAssetId,
    setSelectedPronunciation, setSelectedRemoval, setSelectedSpanId, setSourceIntent, setSourceTerm, setSourceText,
    setSourceUploadProjectId, setSpanEdits, setSpokenTerm, setTitle, setWorkspaceMode, sourceText, title,
  } = ws;

  function capturePronunciation(spanId: string, container: HTMLElement): void {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.anchorNode || !selection.focusNode
      || !container.contains(selection.anchorNode) || !container.contains(selection.focusNode)) return;
    const term = selection.toString().trim();
    if (!term || !selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const before = document.createRange();
    before.selectNodeContents(container);
    before.setEnd(range.startContainer, range.startOffset);
    const leading = selection.toString().length - selection.toString().trimStart().length;
    const start = Array.from(before.toString()).length + Array.from(selection.toString().slice(0, leading)).length;
    setSelectedRemoval({ spanId, start_offset: start, end_offset: start + Array.from(term).length, source_text: term });
    setSelectedPronunciation(null);
    if (!/^\p{L}[\p{L}\p{M}'’\-]*$/u.test(term) || term.length > 128) return;
    const existing = project?.pronunciations?.find((entry) => entry.source_term.toLocaleLowerCase() === term.toLocaleLowerCase());
    setSelectedPronunciation({ spanId });
    setSourceTerm(existing?.source_term ?? term);
    setSpokenTerm(existing?.spoken_term ?? term);
  }

  function changeSpanEdit(changes: Partial<{ speaker_id: string; role: string; delivery: string }>): void {
    if (!selectedSpan || !selectedEdit) return;
    setSpanEdits((previous) => ({ ...previous, [selectedSpan.id]: { ...selectedEdit, ...changes } }));
  }

  async function findSpeakerSpan(speakerId: string): Promise<{ chapterId: string; spanId: string }> {
    if (!project) throw new Error('Open an audiobook project first.');
    for (const chapter of project.chapters) {
      const detail = chapter.id === selectedChapter?.id ? selectedChapter : await queryClient.fetchQuery({
        queryKey: ['audiobook', 'chapter', project.id, chapter.id],
        queryFn: () => audiobookApi.chapter(project.id, chapter.id),
      });
      const span = detail.spans.find((candidate) => candidate.annotation?.speaker_id === speakerId);
      if (span) return { chapterId: chapter.id, spanId: span.id };
    }
    throw new Error('No annotated span uses this speaker yet.');
  }

  async function action(task: () => Promise<unknown>, success: string): Promise<void> {
    setBusy(true); setError(null); setNotice(null);
    try {
      await task();
      await queryClient.invalidateQueries({ queryKey: ['audiobook'] });
      setNotice(success);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  function confirmProjectDeletion(): void {
    if (!project) return;
    void action(async () => {
      await deleteAudiobookProject(project.id);
      setDeleteConfirmationOpen(false);
      setProjectSettingsOpen(false);
      setProjectId(null);
      setChapterId(null);
      setEbookLibraryOpen(true);
      setLibrarySection('library');
    }, 'Audiobook deleted from the library.');
  }

  function confirmAssetDeletion(): void {
    const assetId = assetDeleteConfirmation?.assetId;
    if (!project || !assetDeleteConfirmation || !assetId) return;
    const asset = assetDeleteConfirmation;
    void action(async () => {
      await deleteAudiobookAsset(project.id, assetId);
      setAssetDeleteConfirmation(null);
      setSelectedAssetId('');
    }, `${asset.name} deleted.`);
  }

  function submitProject(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void action(async () => {
      const created = await audiobookApi.createProject({ title, author, language, custom_rules: createClassificationRules.trim() });
      setClassificationRules((current) => ({ ...current, [created.id]: createClassificationRules.trim() }));
      setCreateClassificationRules('');
      setProjectId(created.id);
      setChapterId(null);
      openSourceUpload(created.id);
      const source = pendingSource ?? (sourceText.trim() ? new File([sourceText], 'pasted-story.txt', { type: 'text/plain' }) : null);
      if (source) await uploadSource(created.id, source, createExcludePageRanges);
      setProjectTitle(created.title); setProjectAuthor(created.author); setExcludePageRanges(''); setCreateExcludePageRanges(''); setTitle(''); setAuthor(''); setPendingSource(null); setSourceText(''); setSourceIntent('file'); setLibrarySection('projects'); setMobileRail(null);
    }, pendingSource || sourceText.trim() ? 'Project created and source queued for extraction.' : 'Project created. Upload a source book to begin.');
  }

  function queueSamplePreview(): void {
    if (!project) return;
    void action(async () => {
      if (!project.chapters.length) throw new Error('Add a source book before playing a sample.');
      const modelRevision = await getInstalledModelRevision();
      const orderedChapters = selectedChapter
        ? [
            project.chapters.find((chapter) => chapter.id === selectedChapter.id),
            ...project.chapters.filter((chapter) => chapter.id !== selectedChapter.id),
          ].filter((chapter): chapter is ChapterSummary => Boolean(chapter))
        : project.chapters;
      for (const chapter of orderedChapters) {
        const detail = chapter.id === selectedChapter?.id ? selectedChapter : await queryClient.fetchQuery({
          queryKey: ['audiobook', 'chapter', project.id, chapter.id],
          queryFn: () => audiobookApi.chapter(project.id, chapter.id),
        });
        const span = detail.spans.find(
          (candidate) => Boolean(candidate.speech_plan?.tts_input_text?.trim()),
        );
        if (!span) continue;
        setChapterId(detail.id);
        setSelectedSpanId(span.id);
        await audiobookApi.preview(project.id, {
          chapter_id: detail.id, span_id: span.id, model_revision: modelRevision.trim(),
        });
        return;
      }
      throw new Error('No renderable story speech is available under the current reading policy.');
    }, 'Sample preview queued.');
  }

  function navigateLibrary(section: LibrarySection): void {
    setEbookLibraryOpen(false);
    setLibrarySection(section);
    setProjectSettingsOpen(false);
    setSourceUploadProjectId(null);
    if (section === 'exports') setWorkspaceMode('production');
    else if (section !== 'projects') setWorkspaceMode('review');
    setMobileRail(null);
  }

  function openManuscriptReview(): void {
    navigateLibrary('projects');
    setWorkspaceMode('review');
  }

  function openEbookLibrary(): void {
    setEbookLibraryOpen(true);
    setProjectId(null);
    setChapterId(null);
    setLibrarySection('library');
    setProjectSettingsOpen(false);
    setSourceUploadProjectId(null);
    setMobileRail(null);
  }

  function startNewProject(): void {
    setEbookLibraryOpen(false);
    setProjectId(null);
    setChapterId(null);
    setSelectedSpanId(null);
    setTitle('');
    setAuthor('');
    setLanguage('en');
    setPendingSource(null);
    setSourceText('');
    setSourceIntent('file');
    setCreateExcludePageRanges('');
    setProjectSettingsOpen(false);
    setSourceUploadProjectId(null);
    setWorkspaceMode('review');
    setLibrarySection('projects');
    setError(null);
    setNotice(null);
    window.setTimeout(() => {
      const titleInput = document.getElementById('audiobook-create-title') as HTMLInputElement | null;
      titleInput?.focus();
      document.querySelector('.audiobook-create-page')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    }, 0);
  }

  function openProject(item: ProjectSummary): void {
    setEbookLibraryOpen(false);
    setProjectId(item.id);
    setChapterId(null);
    setProjectTitle(item.title);
    setProjectAuthor(item.author);
    setExcludePageRanges('');
    setError(null);
    setProjectSettingsOpen(false);
    setSourceUploadProjectId(null);
    setLibrarySection('projects');
    setWorkspaceMode('review');
    setMobileRail(null);
  }

  return {
    capturePronunciation, changeSpanEdit, findSpeakerSpan, action, confirmProjectDeletion, confirmAssetDeletion,
    submitProject, queueSamplePreview, navigateLibrary, openManuscriptReview, openEbookLibrary, startNewProject,
    openProject,
  };
}
