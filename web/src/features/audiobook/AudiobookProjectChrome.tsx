import { useState } from 'react';
import { audiobookApi } from './audiobookApi';
import { AudiobookRenderQueueTable } from './AudiobookRenderQueue';
import { LibrarySection, renderJobLabel } from './audiobookWorkspaceModel';
import type { AudiobookJobView } from './audiobookTypes';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

/** Delete confirmations for the project and for an asset. */
export function AudiobookProjectDialogs({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    assetDeleteConfirmation, busy, confirmAssetDeletion, confirmProjectDeletion, deleteConfirmationOpen, project,
    setAssetDeleteConfirmation, setDeleteConfirmationOpen,
  } = ws;
  if (!project) return null;
  return (
    <>
      {deleteConfirmationOpen && <div className="audiobook-modal-backdrop" role="presentation" onMouseDown={() => { if (!busy) setDeleteConfirmationOpen(false); }}>
        <section className="audiobook-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-audiobook-title" onMouseDown={(event) => event.stopPropagation()}>
          <p className="eyebrow">Delete audiobook</p>
          <h2 id="delete-audiobook-title">Delete “{project.title}”?</h2>
          <p>This removes the audiobook from your library and cancels active production jobs. Immutable source and production history is retained for audit safety.</p>
          <div className="audiobook-confirmation-actions"><button type="button" disabled={busy} onClick={() => setDeleteConfirmationOpen(false)}>Cancel</button><button type="button" className="audiobook-danger-action" disabled={busy} onClick={confirmProjectDeletion}>{busy ? 'Deleting...' : 'Delete audiobook'}</button></div>
        </section>
      </div>}
       {assetDeleteConfirmation && <div className="audiobook-modal-backdrop" role="presentation" onMouseDown={() => { if (!busy) setAssetDeleteConfirmation(null); }}>
         <section className="audiobook-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-asset-title" onMouseDown={(event) => event.stopPropagation()}>
           <p className="eyebrow">Delete asset</p>
           <h2 id="delete-asset-title">Delete “{assetDeleteConfirmation.name}”?</h2>
           <p>This removes the stored file from this audiobook. The manuscript source stays protected and can only be replaced by uploading a new source.</p>
           <div className="audiobook-confirmation-actions"><button type="button" disabled={busy} onClick={() => setAssetDeleteConfirmation(null)}>Cancel</button><button type="button" className="audiobook-danger-action" disabled={busy} onClick={confirmAssetDeletion}>{busy ? 'Deleting...' : 'Delete asset'}</button></div>
         </section>
       </div>}
    </>
  );
}

/** Project tabs and the pipeline and classification progress. */
export function AudiobookProjectTabs({ ws }: { ws: AudiobookWorkspaceModel }) {
  const {
    action, busy, cancelReclassification, failedPipelineJob, latestPipelineJob, latestPipelineProgress,
    librarySection, navigateLibrary, pauseReclassification, project, reclassificationJob, reclassificationProgress,
    reclassificationRunning, resumeReclassification,
  } = ws;
  if (!project) return null;
  return (
    <>
       <nav className="audiobook-project-tabs" aria-label="Audiobook project sections">
        {([['books', 'Books'], ['chapters', 'Chapters'], ['assets', 'Assets'], ['documents', 'Documents'], ['characters', 'Cast & Voice Tools'], ['review', project.review_issues.length ? `Human review (${project.review_issues.length})` : 'Human review'], ['exports', 'Render & Export']] as [LibrarySection, string][]).map(([section, label]) => (
          <button key={section} type="button" className={librarySection === section ? 'selected' : ''} aria-current={librarySection === section ? 'page' : undefined}
            onClick={() => navigateLibrary(section)}>{label}</button>
        ))}
      </nav>
      {latestPipelineJob && ['queued', 'leased', 'running', 'retrying'].includes(latestPipelineJob.status) &&
        !(reclassificationRunning && latestPipelineJob.id === reclassificationJob?.id) &&
        <div className="audiobook-message audiobook-pipeline-status" role="status" aria-live="polite">
          <p>{latestPipelineJob.type === 'audiobook.ingest' ? 'Phase 2: Extract quotes' : latestPipelineJob.type === 'audiobook.analyze' ? 'Phase 3: Classify text' : latestPipelineJob.type?.replace('audiobook.', '')} · {latestPipelineJob.status}: {latestPipelineJob.progress?.message || 'Processing the book'}</p>
          <div className="audiobook-pipeline-progress">
            <progress aria-label={`${latestPipelineJob.type?.replace('audiobook.', '') || 'Book processing'} progress`} max={100}
              {...(latestPipelineProgress === null ? {} : { value: latestPipelineProgress })} />
            {latestPipelineProgress !== null && <span>{latestPipelineProgress}%</span>}
          </div>
        </div>}
      {reclassificationJob && reclassificationRunning && <div className="audiobook-progress audiobook-reclassification-progress" role="status" aria-live="polite">
        <div className="audiobook-reclassification-header"><strong>Phase 3: Classifying text</strong><span>{reclassificationProgress}%</span><div className="audiobook-reclassification-actions" role="group" aria-label="Text classification controls">
          {(reclassificationJob.status === 'paused'
            ? <button type="button" disabled={busy} onClick={resumeReclassification}>Resume</button>
            : reclassificationJob.status === 'cancel_requested'
              ? <button type="button" disabled={busy} onClick={cancelReclassification}>Cancel now</button>
              : reclassificationJob.pause_requested
                ? <button type="button" disabled>Pausing…</button>
              : <button type="button" disabled={busy} onClick={pauseReclassification}>Pause</button>) }
          {reclassificationJob.status !== 'cancel_requested' && <button type="button" className="audiobook-danger-action" disabled={busy} onClick={cancelReclassification}>Cancel</button>}
        </div></div>
        <progress aria-label="Text classification progress" max={100} value={reclassificationProgress} />
        <small>{`${reclassificationJob.progress?.current ?? 0} / ${reclassificationJob.progress?.total ?? '—'} spans · ${reclassificationJob.progress?.message || reclassificationJob.status}`}</small>
      </div>}
      {failedPipelineJob && <div className="audiobook-message error" role="alert">
        <strong>{failedPipelineJob.type?.replace('audiobook.', '')} {failedPipelineJob.status}</strong>
        {failedPipelineJob.chapter_id && <> · {project.chapters.find((chapter) => chapter.id === failedPipelineJob.chapter_id)?.title || failedPipelineJob.chapter_id}</>}
        {failedPipelineJob.error?.code && <> · {failedPipelineJob.error.code.replace(/_/g, ' ')}</>}
        <> · {failedPipelineJob.error?.message || 'Open the job queue for details.'}</>
        {failedPipelineJob.attempts !== undefined && <> · attempt {failedPipelineJob.attempts}/{failedPipelineJob.max_attempts}</>}
        {failedPipelineJob.error?.retryable !== undefined && <> · {failedPipelineJob.error.retryable ? 'retryable' : 'manual retry required'}</>}
        {failedPipelineJob.can_retry && <button type="button" disabled={busy}
          onClick={() => void action(() => audiobookApi.job(project.id, failedPipelineJob.id, 'retry'), 'Pipeline retry queued.')}>Retry stage</button>}
        <a href="/jobs">Diagnostics</a>
      </div>}
    </>
  );
}

const ACTIVE_EXPORT_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running', 'cancel_requested']);

/** The latest export's progress while it encodes, then its outcome. */
function AudiobookExportStatus({ job, onViewAssets }: { job: AudiobookJobView; onViewAssets: () => void }) {
  const format = (job.format ?? 'audiobook').toUpperCase();
  if (job.status === 'completed') {
    return <div className="audiobook-export-status" role="status"><span>{format} export ready.</span><button type="button" onClick={onViewAssets}>View in Assets</button></div>;
  }
  if (job.status === 'failed' || job.status === 'dead_letter') {
    return <div className="audiobook-export-status error" role="alert"><span>{format} export failed{job.error?.message ? `: ${job.error.message}` : '.'}</span></div>;
  }
  if (!ACTIVE_EXPORT_STATUSES.has(job.status)) return null;
  const started = job.status !== 'queued' && job.status !== 'waiting';
  const total = Number(job.progress?.total);
  const current = Number(job.progress?.current);
  const percent = started && total > 0 && Number.isFinite(current) ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : null;
  return (
    <div className="audiobook-export-status" role="status" aria-live="polite">
      <span>{format} export · {started ? job.progress?.message || 'Starting' : 'Waiting to start'}</span>
      <progress aria-label={`${format} export progress`} max={100} {...(percent === null ? {} : { value: percent })} />
      <span>{percent === null ? '' : `${percent}%`}</span>
    </div>
  );
}

/** The production dashboard: render queue and exports. */
export function AudiobookProduction({ ws }: { ws: AudiobookWorkspaceModel }) {
  // Starting a render checks the installed model and queues every chapter, which can take a while; say so
  // at the button, and show its outcome there too rather than only at the top of the page.
  const [renderStarting, setRenderStarting] = useState(false);
  const [renderError, setRenderError] = useState<string | null>(null);
  const {
    action, busy, cacheHits, canRender, completedRenderChapters, exportFormat, failedRenderJobs,
    getInstalledModelRevision, isPolicySkippedChapter, modelQuery, modelRevision, pauseAllRenderJobs,
    pausedRenderJobs, project, queuedRenderJobs, renderChapterCount, renderJobs, renderProgressPercent,
    renderStateLabel, resumeAllRenderJobs, retryableRenderJobs, runningRenderJobs, setExportFormat,
    setProjectSettingsOpen, skippedRenderChapterCount, stopAllRenderJobs, workspaceMode, navigateLibrary,
  } = ws;
  if (!project) return null;
  const latestExport = project.export_jobs[0];
  const exportActive = Boolean(latestExport && ACTIVE_EXPORT_STATUSES.has(latestExport.status));
  return (
    <>
      {workspaceMode === 'production' && <>
      <section className="audiobook-card audiobook-render-dashboard">
        <div className="audiobook-render-banner"><div><p className="eyebrow">Production</p><h2>Render &amp; Export</h2><p>Batch rendering, chapter mastering, and multi-format export for your audiobook.</p></div><button type="button" onClick={() => setProjectSettingsOpen(true)}>⚙ Open advanced tools</button></div>
        <div className="audiobook-render-metrics"><article><span>▣</span><div><small>Render Queue</small><strong>{renderProgressPercent}%</strong><em>{completedRenderChapters} / {renderChapterCount} audiobook chapters · {renderStateLabel}{skippedRenderChapterCount ? ` · ${skippedRenderChapterCount} skipped by policy` : ''}</em></div></article><article><span>◉</span><div><small>Cache Hits</small><strong>{cacheHits}</strong><em>Completed render units</em></div></article><article><span>▱</span><div><small>Source Size</small><strong>{project.source_size_bytes ? `${(project.source_size_bytes / 1048576).toFixed(1)} MB` : '—'}</strong><em>Original source</em></div></article><article><span>△</span><div><small>Failed / Retryable</small><strong>{failedRenderJobs} failed · {retryableRenderJobs} retry</strong><em>View and retry →</em></div></article><article><span>◷</span><div><small>Estimated Remaining</small><strong>—</strong><em>{Math.max(0, renderChapterCount - completedRenderChapters)} audiobook chapters left · estimate unavailable</em></div></article></div>
        <div className="audiobook-render-columns"><section className="audiobook-render-queue"><div className="audiobook-section-title"><h3>▤ &nbsp;Render Queue</h3><span>{runningRenderJobs} running · {queuedRenderJobs} queued · {pausedRenderJobs} paused</span><div className="audiobook-row-actions"><button type="button" disabled={busy || !(runningRenderJobs + queuedRenderJobs)} onClick={pauseAllRenderJobs}>Pause all chapters</button><button type="button" disabled={busy || !pausedRenderJobs} onClick={resumeAllRenderJobs}>Resume all chapters</button><button type="button" disabled={busy || !(runningRenderJobs + queuedRenderJobs + pausedRenderJobs)} onClick={stopAllRenderJobs}>Stop all chapters</button></div></div>{!renderJobs.length && project.state === 'ready_to_render' && <p className="audiobook-hint">No render run is active. Select Render book to queue all chapters.</p>}<AudiobookRenderQueueTable ws={ws} /></section><section className="audiobook-mastering"><div className="audiobook-section-title"><h3>Chapter Assembly</h3></div>{project.chapters.map((chapter) => { const policySkipped = isPolicySkippedChapter(chapter.id); const assemblyJob = project.pipeline_jobs?.find((candidate) => candidate.type === 'audiobook.assemble-chapter' && candidate.chapter_id === chapter.id); const assemblyStatus = policySkipped ? 'Skipped by policy' : assemblyJob ? renderJobLabel(assemblyJob, project.state) : project.state === 'ready_to_render' ? 'Waiting for render' : project.state === 'rendering' ? 'Waiting for audio' : project.state === 'mastering' ? 'Mastering' : 'Not started'; return <article className="audiobook-mastering-card" key={chapter.id}><div className="audiobook-mastering-card-head"><strong>Chapter {chapter.ordinal + 1} - {chapter.title}</strong><span>{assemblyStatus}</span></div><div className="audiobook-mastering-checks"><span>Chapter assembly <b>{assemblyStatus}</b></span><span>Audio artifact <b>{policySkipped ? 'Not applicable' : assemblyStatus === 'Completed' ? 'Ready' : 'Pending'}</b></span></div></article>; })}</section></div>
        <div className="audiobook-render-footer"><label>Installed model revision<input value={modelRevision} readOnly placeholder={modelQuery.isFetching ? 'Checking installed model' : 'Computed when rendering'} /></label><button type="button" className="audiobook-primary-action" disabled={busy || !canRender} onClick={() => { setRenderStarting(true); setRenderError(null); void action(async () => { try { const modelRevision = await getInstalledModelRevision(); return await audiobookApi.startRender(project.id, { model_revision: modelRevision.trim() }); } catch (cause) { setRenderError(cause instanceof Error ? cause.message : String(cause)); throw cause; } }, 'Chapter render jobs queued.').finally(() => setRenderStarting(false)); }}>{renderStarting ? 'Starting render…' : project.state === 'rendering' ? 'Retry render' : 'Render book'}</button><label>Format<select value={exportFormat} onChange={(event) => setExportFormat(event.target.value)}><option value="m4b">M4B</option><option value="flac">FLAC</option><option value="wav">WAV</option><option value="mp3">MP3</option></select></label><button type="button" disabled={busy || exportActive || !['ready_to_export', 'exported'].includes(project.state)} onClick={() => void action(() => audiobookApi.startExport(project.id, { format: exportFormat }), `${exportFormat.toUpperCase()} export queued.`)}>{exportActive ? 'Exporting…' : 'Export Now'}</button></div>
        {renderStarting && <p className="audiobook-render-request" role="status">Checking the installed model and queuing chapters…</p>}
        {!renderStarting && renderError && <p className="audiobook-render-request error" role="alert">Render not started: {renderError}</p>}
        {latestExport && <AudiobookExportStatus job={latestExport} onViewAssets={() => navigateLibrary('assets')} />}
        {!canRender && project.render_readiness?.blockers.length ? <div className="audiobook-render-blockers" role="status" aria-label="Render book requirements">
          <strong>Before rendering</strong>
          <ul>{project.render_readiness.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
        </div> : null}
      </section>
      </>}
    </>
  );
}
