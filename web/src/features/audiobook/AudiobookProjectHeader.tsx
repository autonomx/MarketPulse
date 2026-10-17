import { LibrarySection, base, extractQuotes, formatCount, formatDuration, importLibrarySource, reclassifyAudiobook, saveClassificationRules, setAudiobookReadingMode, sourceAccept, updateProjectMetadata, uploadCover, uploadSource } from './audiobookWorkspaceModel';
import { useState, type ReactNode } from 'react';
import { productionPhases, type ProductionPhase } from './audiobookProductionPhases';
import type { AudiobookJobView } from './audiobookTypes';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

const EXTRACTING_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running']);
const FAILED_STATUSES = new Set(['failed', 'dead_letter', 'stale']);

/** The extraction to show: one that is running, else the newest still waiting. */
function activeExtraction(jobs: AudiobookJobView[] | undefined): AudiobookJobView | undefined {
  const ingests = (jobs ?? []).filter((job) => job.type === 'audiobook.ingest');
  return ingests.find((job) => job.status === 'running' || job.status === 'leased')
    ?? ingests.find((job) => EXTRACTING_STATUSES.has(job.status));
}

/** What the book source is doing right now: the upload, then the server's chapter extraction. */
function SourceProgress({ uploadingName, extraction, failed }: { uploadingName: string | null; extraction?: AudiobookJobView; failed?: AudiobookJobView }) {
  if (uploadingName) {
    return <div className="audiobook-source-progress" role="status" aria-live="polite">
      <span>Uploading {uploadingName}…</span>
      <progress aria-label="Book upload progress" max={100} />
    </div>;
  }
  if (extraction) {
    const started = extraction.status === 'running' || extraction.status === 'leased';
    const total = Number(extraction.progress?.total);
    const current = Number(extraction.progress?.current);
    const percent = started && total > 0 && Number.isFinite(current) ? Math.round((current / total) * 100) : null;
    return <div className="audiobook-source-progress" role="status" aria-live="polite">
      <span>{started ? `Extracting chapters · ${extraction.progress?.message || 'starting'}` : 'Book received · waiting to extract chapters'}</span>
      <progress aria-label="Chapter extraction progress" max={100} {...(percent === null ? {} : { value: percent })} />
      <small>Large books can take a few minutes. The book appears here when extraction finishes.</small>
    </div>;
  }
  if (failed) {
    return <p className="audiobook-source-progress error" role="alert">Chapter extraction failed{failed.error?.message ? `: ${failed.error.message}` : '.'} Upload the book again to retry.</p>;
  }
  return null;
}

/** One production phase in the book source editor: green once complete, else what is left to do. */
function PhaseRow({ number, phase, description, note, children }: { number: number; phase: ProductionPhase; description: string; note?: string; children: ReactNode }) {
  const title = `Phase ${number}: ${phase.title}`;
  return <li className={`audiobook-source-reclassify${phase.complete ? ' complete' : ''}`}>
    <div>
      <div className="audiobook-source-phase-title"><strong>{title}</strong>{phase.complete && <span className="audiobook-source-state attached">Complete</span>}</div>
      <p>{description}</p>
      {note && <p>{note}</p>}
      {!phase.complete && phase.todos.length > 0 && <ul className="audiobook-source-phase-todos" aria-label={`${title} to do`}>
        {phase.todos.map((todo) => <li key={todo.label}>{todo.open ? <button type="button" onClick={todo.open}>{todo.label} →</button> : <span>{todo.label}</span>}</li>)}
      </ul>}
    </div>
    {children}
  </li>;
}

/** Phases 2 to 6 below the book source, each with its own next action. */
function ProductionPhaseRows({ ws, phases }: { ws: AudiobookWorkspaceModel; phases: ProductionPhase[] }) {
  const { action, busy, classificationRules, extractionRunning, project, reclassificationRunning, rulesNeedExtraction } = ws;
  if (!project) return null;
  const rules = classificationRules[project.id] ?? project.classification_rules ?? '';
  return (
    <ol className="audiobook-source-phases" aria-label="Production phases">
      <PhaseRow number={2} phase={phases[1]} description="Extract quote spans using the rules above, then review the spans. Classification does not start automatically.">
        <button type="button" disabled={busy || extractionRunning || reclassificationRunning || !project.current_source_revision_id}
          onClick={() => void action(() => extractQuotes(project.id, rules), 'Quote extraction queued. Review quote spans before classifying text.')}>
          {extractionRunning ? 'Extracting quotes…' : 'Extract quotes'}
        </button>
      </PhaseRow>
      <PhaseRow number={3} phase={phases[2]} description="Review the extracted quote spans first, including unwanted text, then classify text to assign speakers."
        note={rulesNeedExtraction ? 'Rules changed. Extract quotes again and review the new spans before classifying.' : undefined}>
        <button type="button" disabled={busy || extractionRunning || reclassificationRunning || rulesNeedExtraction || !project.current_source_revision_id}
          onClick={() => void action(() => reclassifyAudiobook(project.id, rules), 'Text classification queued.')}>
          {reclassificationRunning ? 'Classifying…' : 'Classify text'}
        </button>
      </PhaseRow>
      <PhaseRow number={4} phase={phases[3]} description="Assign a voice to every speaker, then adjust the delivery or emotion of dialogue.">
        <button type="button" onClick={phases[3].open}>{phases[3].action}</button>
      </PhaseRow>
      <PhaseRow number={5} phase={phases[4]} description="Listen to a sample, then render the audio for every chapter.">
        <button type="button" onClick={phases[4].open}>{phases[4].action}</button>
      </PhaseRow>
      <PhaseRow number={6} phase={phases[5]} description="Choose a format and export the finished audiobook.">
        <button type="button" onClick={phases[5].open}>{phases[5].action}</button>
      </PhaseRow>
    </ol>
  );
}

/** The open project's header, production path and settings. */
export function AudiobookProjectHeader({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    action, busy, classificationRules, completedRenderChapters, excludePageRanges, librarySection, navigateLibrary,
    openSourceLibrary, project, projectAuthor, projectSettingsOpen, projectTitle, queueSamplePreview,
    reclassificationRunning, renderChapterCount, renderProgressPercent, setClassificationRules,
    setDeleteConfirmationOpen, setExcludePageRanges, setProjectAuthor, setProjectSettingsOpen, setProjectTitle,
    setSourceLibraryFilename, setSourceLibraryOpen, sourceLibraryFilename, sourceLibraryOpen, sourceLibraryQuery,
  } = ws;
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  if (!project) return null;
  const extraction = activeExtraction(project.pipeline_jobs);
  const newestIngest = project.pipeline_jobs?.find((job) => job.type === 'audiobook.ingest');
  const failedExtraction = !project.current_source_revision_id && newestIngest && FAILED_STATUSES.has(newestIngest.status) ? newestIngest : undefined;
  const sourceBusy = Boolean(uploadingName || extraction);
  const sourceState = uploadingName ? 'Uploading' : extraction ? 'Extracting' : project.current_source_revision_id ? 'Source attached' : 'Source needed';
  const phases = productionPhases(ws);
  return (
    <>
      <nav className="audiobook-breadcrumb" aria-label="Audiobook breadcrumb"><button type="button" onClick={() => navigateLibrary('projects')}>Audiobook</button><span>›</span><strong>{project.title}</strong>{librarySection !== 'projects' && <><span>›</span><strong>{({ library: 'Library', books: 'Books', chapters: 'Chapters', assets: 'Assets', documents: 'Documents', characters: 'Cast & Voice Tools', voices: 'Voices', pronunciations: 'Pronunciations', exports: 'Render & Export', projects: 'Projects' } as Record<LibrarySection, string>)[librarySection]}</strong></>}</nav>
      <header className="audiobook-card audiobook-project-header" id="audiobook-book">
        <div className="audiobook-project-overview">
          {project.cover_asset_id && <img className="audiobook-cover" src={`${base}/projects/${encodeURIComponent(project.id)}/cover`} alt={`Cover of ${project.title}`} />}
          <div className="audiobook-header-copy"><p className="eyebrow">Audiobook project · {project.state.replaceAll('_', ' ')}</p><h1>{project.title}</h1><p>A local-first audiobook production workspace.</p>
            <div className="audiobook-tag-row">{project.source_format && <span>{project.source_format.toUpperCase()} source</span>}<span>{project.chapters.length} chapters</span></div>
            <p className="audiobook-project-stats"><span className="audiobook-project-stats-sentence">{formatCount(project.word_count)} words</span><strong>{formatCount(project.word_count)}</strong><small>WORDS</small><strong>{project.chapters.length}</strong><small>CHAPTERS</small><strong>{project.speakers.length}</strong><small>SPEAKERS</small><strong>{formatDuration(project.estimated_runtime_seconds)}</strong><small>EST. RUNTIME</small></p>
          </div>
        </div>
        <div className="audiobook-project-header-side">
          <div className="audiobook-header-action-buttons"><button type="button" aria-expanded={projectSettingsOpen} onClick={() => setProjectSettingsOpen((open) => !open)}>{projectSettingsOpen ? 'Close editor' : 'Edit project'}</button><button type="button" className="audiobook-primary-action" disabled={busy} onClick={queueSamplePreview}>Play sample</button><button type="button" aria-label="More project actions" onClick={() => setProjectSettingsOpen(true)}>...</button></div>
          <div className={`audiobook-status-meter${project.review_issues.length ? ' has-issues' : ''}`}><div><p className="eyebrow">Project status</p><strong>{project.review_issues.length ? 'Review required' : project.state.replaceAll('_', ' ')}</strong>{project.review_issues.length
            ? <button type="button" className="audiobook-status-issues" onClick={() => navigateLibrary('review')}>{project.review_issues.length} issues to resolve</button>
            : <span>{`${completedRenderChapters} / ${renderChapterCount} audiobook chapters rendered`}</span>}</div><b>{renderProgressPercent}%</b><progress max={100} value={renderProgressPercent} /></div>
        </div>
        <section className="audiobook-production-flow" aria-labelledby="audiobook-production-flow-title">
          <div className="audiobook-production-flow-heading"><div><p className="eyebrow">Production path</p><h2 id="audiobook-production-flow-title">From manuscript to audiobook</h2></div><p>Follow these steps in order. Open any step to continue your work.</p></div>
          <ol className="audiobook-production-flow-steps" aria-label="Audiobook production steps">
            {phases.map((step, index) => <li key={step.title} className={step.complete ? 'complete' : ''}>
              <span className="audiobook-production-flow-number" aria-hidden="true">{index + 1}</span>
              <div className="audiobook-production-flow-copy"><strong>{step.title}</strong><small>{step.detail}</small></div>
              <span className="audiobook-production-flow-status">{step.status}</span>
              <button type="button" onClick={step.open}>{step.action}</button>
            </li>)}
          </ol>
        </section>
        {projectSettingsOpen && <div className="audiobook-project-actions">
          <section className="audiobook-settings-card audiobook-source-settings" aria-labelledby="audiobook-source-settings-title">
            <div className="audiobook-settings-heading"><div><p className="eyebrow">Phase 1: Add book</p><h2 id="audiobook-source-settings-title" tabIndex={-1}>Book source</h2></div><span className={`audiobook-source-state ${sourceBusy ? 'working' : project.current_source_revision_id ? 'attached' : 'needed'}`}>{sourceState}</span></div>
            <p className="audiobook-source-filename">{project.source_filename || uploadingName || (extraction ? 'Book received' : 'No book uploaded yet')}</p>
            <p className="audiobook-settings-description">Upload a PDF, EPUB, DOCX, Markdown, HTML, or text file to extract chapters.</p>
            <SourceProgress uploadingName={uploadingName} extraction={extraction} failed={failedExtraction} />
            <div className="audiobook-source-actions">
              <label className={`audiobook-source-file-button${busy || sourceBusy ? ' disabled' : ''}`}><span>Upload from computer</span>
                <input type="file" accept={sourceAccept} disabled={busy || sourceBusy}
                  onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (!file) return; setUploadingName(file.name); void action(async () => { await uploadSource(project.id, file, excludePageRanges, classificationRules[project.id] ?? project.classification_rules ?? ''); setExcludePageRanges(''); }, 'Book uploaded. Extracting chapters now.').finally(() => setUploadingName(null)); }} />
              </label>
              <button type="button" className="audiobook-source-library-trigger" disabled={busy || sourceBusy} onClick={openSourceLibrary}>Choose from local library</button>
            </div>
            <details className="audiobook-source-library" open={sourceLibraryOpen} onToggle={(event) => {
              setSourceLibraryOpen(event.currentTarget.open);
              if (event.currentTarget.open && !sourceLibraryQuery.data) void sourceLibraryQuery.refetch();
            }}>
              <summary>Browse resources\data\audiobooks</summary>
              <p className="audiobook-hint">Choose a book already stored in the local audiobook folder.</p>
              {sourceLibraryQuery.isFetching && <p role="status">Loading local books…</p>}
              {sourceLibraryQuery.isError && <p role="alert">Could not read the local audiobook folder.</p>}
              {sourceLibraryQuery.data && !sourceLibraryQuery.data.files.length && <p role="status">No supported books found in this folder.</p>}
              {sourceLibraryQuery.data && sourceLibraryQuery.data.files.length > 0 && <>
                <label>Source book<select aria-label="Local audiobook source" value={sourceLibraryFilename}
                  onChange={(event) => setSourceLibraryFilename(event.target.value)} disabled={busy}>
                  <option value="">Choose a source book</option>
                  {sourceLibraryQuery.data.files.map((file) => <option key={file.name} value={file.name}>{file.name}</option>)}
                </select></label>
                <button type="button" disabled={busy || !sourceLibraryFilename}
                  onClick={() => void action(async () => { await importLibrarySource(project.id, sourceLibraryFilename, excludePageRanges, classificationRules[project.id] ?? project.classification_rules ?? ''); setExcludePageRanges(''); }, 'Source queued for extraction.')}>Use selected book</button>
              </>}
            </details>
            <details className="audiobook-source-advanced"><summary>PDF page options</summary>
              <label className="audiobook-page-filter">Exclude pages
                <input aria-label="Exclude PDF pages" inputMode="text" placeholder="e.g. 1-3, 42-45"
                  value={excludePageRanges} onChange={(event) => setExcludePageRanges(event.target.value)} disabled={busy} />
                <small>Enter 1-based page numbers. Applied when the next PDF source is uploaded.</small>
              </label>
            </details>
            <label className="audiobook-classification-rules">Custom rules for classification (optional)
              <textarea value={classificationRules[project.id] ?? project.classification_rules ?? ''} maxLength={4000} rows={3}
                disabled={busy || reclassificationRunning}
                onChange={(event) => setClassificationRules((current) => ({ ...current, [project.id]: event.target.value }))}
                placeholder="Character quotes can also be in speaker: quote format." />
            </label>
            <button type="button" disabled={busy || reclassificationRunning}
              onClick={() => void action(() => saveClassificationRules(project.id, classificationRules[project.id] ?? project.classification_rules ?? ''), 'Classification rules saved. They apply to the next import or classification run.')}>Save classification rules</button>
            <ProductionPhaseRows ws={ws} phases={phases} />
          </section>
          <div className="audiobook-settings-side">
            <section className="audiobook-settings-card" aria-labelledby="audiobook-details-settings-title">
              <div className="audiobook-settings-heading"><div><p className="eyebrow">Project</p><h2 id="audiobook-details-settings-title">Details &amp; reading</h2></div></div>
              <div className="audiobook-settings-fields"><label>Title<input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} /></label>
                <label>Author<input value={projectAuthor} onChange={(event) => setProjectAuthor(event.target.value)} /></label></div>
              <label className="audiobook-reading-mode">Audiobook reading mode
                <select aria-label="Audiobook reading mode" value={project.audiobook_mode ?? 'standard'} disabled={busy}
                  onChange={(event) => { const mode = event.target.value as 'standard' | 'story_only' | 'verbatim'; void action(() => setAudiobookReadingMode(project.id, mode), `Reading mode changed to ${mode.replaceAll('_', ' ')}.`); }}>
                  <option value="standard">Standard audiobook</option><option value="story_only">Story only</option><option value="verbatim">Verbatim source</option>
                </select>
                <small>{(project.audiobook_mode ?? 'standard') === 'story_only'
                  ? 'Reads the story and scene headings while skipping front matter, page numbers, and publishing details.'
                  : (project.audiobook_mode ?? 'standard') === 'verbatim'
                    ? 'Reads every extracted block, including headings and publishing details.'
                    : 'Reads story and chapter structure while skipping contents pages and running headers.'}</small>
              </label>
              <button type="button" className="audiobook-settings-save" disabled={busy || !projectTitle.trim() || (projectTitle === project.title && projectAuthor === project.author)}
                onClick={() => void action(() => updateProjectMetadata(project.id, projectTitle.trim(), projectAuthor.trim()), 'Project metadata saved. Export metadata will use the new values.')}>Save details</button>
            </section>
            <section className="audiobook-settings-card audiobook-cover-settings" aria-labelledby="audiobook-cover-settings-title">
              <div className="audiobook-settings-heading"><div><p className="eyebrow">Artwork</p><h2 id="audiobook-cover-settings-title">Book cover</h2></div></div>
              <p className="audiobook-settings-description">{project.cover_asset_id ? 'Cover added. Upload a new image to replace it.' : 'Add a cover image for your audiobook.'}</p>
              <label className="audiobook-source-file-button audiobook-cover-file-button"><span>{project.cover_asset_id ? 'Replace cover' : 'Choose cover image'}</span>
                <input type="file" accept="image/jpeg,image/png" disabled={busy}
                  onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void action(() => uploadCover(project.id, file), 'Cover saved for future exports.'); event.currentTarget.value = ''; }} />
              </label>
            </section>
            <section className="audiobook-settings-card audiobook-danger-settings" aria-labelledby="audiobook-danger-settings-title">
              <div><p className="eyebrow">Danger zone</p><h2 id="audiobook-danger-settings-title">Delete project</h2></div>
              <button type="button" className="audiobook-danger-action" disabled={busy} onClick={() => setDeleteConfirmationOpen(true)}>Delete audiobook</button>
            </section>
          </div>
        </div>}
      </header>
    </>
  );
}
