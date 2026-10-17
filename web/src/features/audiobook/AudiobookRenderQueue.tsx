import { audiobookApi } from './audiobookApi';
import { CONTROLLABLE_RENDER_STATUSES, renderJobLabel, renderJobProgress } from './audiobookWorkspaceModel';
import { formatPlaybackTime, useChapterPlayback } from './useChapterPlayback';
import type { AudiobookWorkspaceModel } from './useAudiobookWorkspace';

/**
 * The render queue table: one row per chapter with its render status and controls,
 * the render order of waiting chapters, and playback of each finished chapter.
 */
export function AudiobookRenderQueueTable({ ws }: { ws: AudiobookWorkspaceModel }) {
  const { action, busy, isPolicySkippedChapter, project, startChapter } = ws;
  const playback = useChapterPlayback();
  if (!project) return null;
  const projectId = project.id;
  const titleOf = (chapterId: string) => project.chapters.find((chapter) => chapter.id === chapterId)?.title ?? 'chapter';
  // Waiting chapters in the order the worker will render them.
  const waiting = project.render_jobs
    .filter((job) => typeof job.queue_position === 'number' && job.chapter_id)
    .sort((left, right) => (left.queue_position ?? 0) - (right.queue_position ?? 0))
    .map((job) => job.chapter_id as string);
  const ordinalOf = new Map(project.chapters.map((chapter) => [chapter.id, chapter.ordinal]));
  const bookOrder = [...waiting].sort((left, right) => (ordinalOf.get(left) ?? 0) - (ordinalOf.get(right) ?? 0));
  const inBookOrder = bookOrder.every((chapterId, index) => waiting[index] === chapterId);
  const reorder = (order: string[], message: string) => void action(() => audiobookApi.reorderRender(projectId, order), message);
  const move = (chapterId: string, offset: number) => {
    const order = [...waiting];
    const from = order.indexOf(chapterId);
    const to = from + offset;
    if (from < 0 || to < 0 || to >= order.length) return;
    [order[from], order[to]] = [order[to], order[from]];
    reorder(order, `${titleOf(chapterId)} moved ${offset < 0 ? 'up' : 'down'} the render order.`);
  };

  return (
    <>
      {waiting.length > 1 && <div className="audiobook-render-order-bar">
        <small>Waiting chapters render in the order shown by their # number.</small>
        <button type="button" disabled={busy || inBookOrder} onClick={() => reorder(bookOrder, 'Waiting chapters will render in book order.')}>Render in book order</button>
      </div>}
      {playback.error && <p className="audiobook-message error" role="alert">{playback.error}</p>}
      <div className="audiobook-render-table"><div className="audiobook-render-table-head"><span>#</span><span>Chapter</span><span>Status</span><span>Progress</span><span>Time</span><span>Actions</span></div>
        {project.chapters.map((chapter) => {
          const job = project.render_jobs.find((candidate) => candidate.chapter_id === chapter.id);
          const policySkipped = isPolicySkippedChapter(chapter.id);
          const status = policySkipped ? 'Skipped' : renderJobLabel(job, project.state);
          const progress = policySkipped ? 0 : renderJobProgress(job, project.state);
          const position = job?.queue_position ?? null;
          const duration = job?.audio?.duration_seconds ?? null;
          const loaded = playback.chapterId === chapter.id;
          const playing = loaded && playback.playing;
          return <div className="audiobook-render-row" key={chapter.id}>
            <b>{chapter.ordinal + 1}</b>
            <span>{chapter.title}</span>
            <em className={`render-status-${status.toLowerCase()}`}>{status}{position !== null && <small aria-label={`Render order ${position}`}> #{position}</small>}{policySkipped && <small> by reading policy</small>}</em>
            <div className="render-row-progress">{policySkipped ? <small>Not rendered</small> : <><progress max={100} value={progress} /><small>{progress}%</small></>}</div>
            <span className="render-row-time">{duration === null ? '—' : loaded ? `${formatPlaybackTime(playback.position)} / ${formatPlaybackTime(duration)}` : formatPlaybackTime(duration)}</span>
            <div className="audiobook-row-actions">
              {duration !== null && <>
                {playing
                  ? <button type="button" aria-label={`Pause playback of ${chapter.title}`} onClick={playback.pause}>❚❚ Pause</button>
                  : <button type="button" aria-label={`Play ${chapter.title}`} onClick={() => playback.play(chapter.id, audiobookApi.chapterAudioUrl(projectId, chapter.id))}>▶ {loaded ? 'Resume' : 'Play'}</button>}
                <button type="button" aria-label={`Stop playback of ${chapter.title}`} disabled={!loaded} onClick={playback.stop}>■ Stop</button>
              </>}
              {position !== null && <>
                <button type="button" aria-label={`Move ${chapter.title} up the render order`} disabled={busy || position === 1} onClick={() => move(chapter.id, -1)}>↑</button>
                <button type="button" aria-label={`Move ${chapter.title} down the render order`} disabled={busy || position === waiting.length} onClick={() => move(chapter.id, 1)}>↓</button>
                <button type="button" aria-label={`Render ${chapter.title} next`} disabled={busy || position === 1} onClick={() => reorder([chapter.id], `${chapter.title} will render next.`)}>Next</button>
              </>}
              {job && CONTROLLABLE_RENDER_STATUSES.has(job.status)
                ? <>{job.status === 'paused'
                  ? <button type="button" aria-label="Resume chapter" disabled={busy} onClick={() => void action(() => audiobookApi.job(projectId, job.id, 'resume'), 'Chapter resumed.')}>Resume</button>
                  : <button type="button" aria-label="Pause chapter" disabled={busy} onClick={() => void action(() => audiobookApi.job(projectId, job.id, 'pause'), 'Chapter paused.')}>Pause</button>}
                  <button type="button" aria-label="Stop chapter" disabled={busy} onClick={() => void action(() => audiobookApi.job(projectId, job.id, 'cancel'), 'Chapter stopped.')}>Stop</button></>
                : job?.status === 'cancel_requested'
                  ? <button type="button" aria-label="Stopping chapter" disabled>Stopping…</button>
                  : <button type="button" aria-label="Open chapter" onClick={() => startChapter(chapter)}>Open</button>}
            </div>
          </div>;
        })}
      </div>
    </>
  );
}
