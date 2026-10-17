import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

// Project states once classification has finished, and once quote review is behind the book.
const CLASSIFIED_STATES = new Set(['review_required', 'ready_to_render', 'rendering', 'mastering', 'ready_to_export', 'exported']);
const PAST_QUOTE_REVIEW_STATES = new Set(['analyzing', ...CLASSIFIED_STATES]);
const ACTIVE_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running', 'cancel_requested']);

/** Something still standing between a phase and its completion; `open` goes where it is done. */
export type ProductionTodo = { label: string; open?: () => void };

export type ProductionPhase = {
  title: string;
  detail: string;
  status: string;
  complete: boolean;
  action: string;
  open: () => void;
  todos: ProductionTodo[];
};

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`;

/** The six production phases of the open project, from adding the book to exporting it. */
export function productionPhases(ws: AudiobookWorkspaceModel): ProductionPhase[] {
  const {
    assignedVoiceCount, canRender, castableSpeakers, completedRenderChapters, extractionRunning, failedRenderJobs,
    navigateLibrary, openManuscriptReview, openSourceUpload, pausedRenderJobs, project, queuedRenderJobs,
    reclassificationRunning, renderChapterCount, renderProgressPercent, rulesNeedExtraction, runningRenderJobs,
  } = ws;
  if (!project) return [];
  const hasSource = Boolean(project.current_source_revision_id);
  const openSource = () => openSourceUpload(project.id);
  const openCast = () => navigateLibrary('characters');
  const openProduction = () => navigateLibrary('exports');
  // Quote review is done once the book has moved on to classification; classification is done
  // once it has finished and every speaker it was unsure about has been confirmed.
  const quotesReviewed = hasSource && !extractionRunning && PAST_QUOTE_REVIEW_STATES.has(project.state);
  const textClassified = quotesReviewed && !reclassificationRunning && CLASSIFIED_STATES.has(project.state) && project.review_issues.length === 0;
  const unassignedVoices = castableSpeakers.length - assignedVoiceCount;
  const rendered = renderChapterCount > 0 && completedRenderChapters === renderChapterCount;
  const latestExport = project.export_jobs[0];

  const quoteTodos: ProductionTodo[] = !hasSource ? [{ label: 'Add a book in Phase 1', open: openSource }]
    : extractionRunning ? [{ label: 'Wait for quote extraction to finish' }]
      : rulesNeedExtraction ? [{ label: 'Rules changed: extract quotes again' }]
        : [{ label: 'Review the extracted quote spans', open: openManuscriptReview }];

  const classifyTodos: ProductionTodo[] = [];
  if (reclassificationRunning) classifyTodos.push({ label: 'Wait for text classification to finish' });
  else if (!quotesReviewed || !CLASSIFIED_STATES.has(project.state)) classifyTodos.push({ label: 'Classify text once the quote spans are reviewed' });
  if (project.review_issues.length) {
    classifyTodos.push({ label: `Resolve ${plural(project.review_issues.length, 'speaker review issue')}`, open: () => navigateLibrary('review') });
  }

  const castTodos: ProductionTodo[] = !castableSpeakers.length ? [{ label: 'Classify text to find the speakers' }]
    : [{ label: `Assign voices to ${plural(unassignedVoices, 'speaker')}`, open: openCast }];

  const renderTodos: ProductionTodo[] = [];
  if (failedRenderJobs) renderTodos.push({ label: `Retry ${plural(failedRenderJobs, 'failed chapter')}`, open: openProduction });
  if (pausedRenderJobs) renderTodos.push({ label: `Resume ${plural(pausedRenderJobs, 'paused chapter')}`, open: openProduction });
  if (runningRenderJobs + queuedRenderJobs) renderTodos.push({ label: `Rendering: ${completedRenderChapters} / ${renderChapterCount} chapters done`, open: openProduction });
  if (!renderTodos.length) {
    const blockers = project.render_readiness?.blockers.length ?? 0;
    renderTodos.push(canRender ? { label: 'Render the book', open: openProduction }
      : blockers ? { label: `Resolve ${plural(blockers, 'render requirement')}`, open: openProduction }
        : { label: 'Finish the earlier phases first' });
  }

  const exportTodos: ProductionTodo[] = latestExport && ['failed', 'dead_letter'].includes(latestExport.status)
    ? [{ label: 'The last export failed: export again', open: openProduction }]
    : latestExport && ACTIVE_STATUSES.has(latestExport.status) ? [{ label: 'Wait for the export to finish', open: openProduction }]
      : project.state === 'ready_to_export' ? [{ label: 'Export the audiobook', open: openProduction }]
        : [{ label: 'Render every chapter first', open: openProduction }];

  return [
    { title: 'Add book', detail: 'Upload or choose a book; set title, author, and reading mode.', status: hasSource ? 'Source attached' : 'Source needed', complete: hasSource, action: 'Open source', open: openSource, todos: [{ label: 'Upload or choose a book', open: openSource }] },
    { title: 'Extract quotes', detail: 'Detect quote spans, then review them and remove unwanted spoken text.', status: extractionRunning ? 'Extracting quotes' : quotesReviewed ? 'Quotes reviewed' : hasSource ? 'Ready for quote review' : 'Add a book first', complete: quotesReviewed, action: 'Review spans', open: openManuscriptReview, todos: quoteTodos },
    { title: 'Classify text', detail: 'Review quote spans first. Start classification yourself to assign speakers.', status: reclassificationRunning ? 'Classifying' : project.state === 'extracted' ? 'Waiting for quote review' : project.review_issues.length ? `${project.review_issues.length} issues to review` : `${project.speakers.length} speakers found`, complete: textClassified, action: 'Review speakers', open: openManuscriptReview, todos: classifyTodos },
    { title: 'Cast and direct', detail: 'Assign voices and adjust the delivery or emotion of dialogue.', status: `${assignedVoiceCount} / ${castableSpeakers.length} voices assigned`, complete: castableSpeakers.length > 0 && unassignedVoices === 0, action: 'Manage cast', open: openCast, todos: castTodos },
    { title: 'Preview and render', detail: 'Listen to a sample, then generate the chapter audio.', status: `${renderProgressPercent}% rendered`, complete: rendered, action: 'Open render', open: openProduction, todos: renderTodos },
    { title: 'Export', detail: 'Choose a format and download the finished audiobook.', status: project.state === 'exported' ? 'Exported' : ['ready_to_export', 'exported'].includes(project.state) ? 'Ready to export' : 'After rendering', complete: project.state === 'exported', action: 'Open exports', open: openProduction, todos: exportTodos },
  ];
}
