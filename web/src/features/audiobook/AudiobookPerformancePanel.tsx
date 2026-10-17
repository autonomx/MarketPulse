import { useEffect, useMemo, useState } from 'react';
import { audiobookApi } from './audiobookApi';
import type { AudiobookJobView, BookPerformance, ProjectDetail } from './audiobookTypes';

const DEFAULT_LEVELS = { stability: 0.75, style: 0.35 };
const ACTIVE_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running', 'cancel_requested']);
type Level = keyof typeof DEFAULT_LEVELS;

const LEVEL_LABELS: Record<Level, { label: string; hint: string }> = {
  stability: { label: 'Stability', hint: 'Higher is steadier and more even; lower lets the delivery vary more.' },
  style: { label: 'Style exaggeration', hint: 'Higher allows more varied, expressive prosody.' },
};

function savedPerformance(project: ProjectDetail): BookPerformance {
  return { ...DEFAULT_LEVELS, speakers: {}, ...(project.performance ?? {}) };
}

/**
 * Cast and direct → Performance: the book's Stability and Style, characters' own
 * levels, and "Direct performance", which writes a delivery direction for each
 * dialogue line. Changing either restarts rendering; unchanged lines come from cache.
 */
export function AudiobookPerformancePanel({ project, busy, action }: {
  project: ProjectDetail;
  busy: boolean;
  action: (task: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const saved = useMemo(() => savedPerformance(project), [project]);
  const savedKey = JSON.stringify(saved);
  const [draft, setDraft] = useState<BookPerformance>(saved);
  useEffect(() => setDraft(JSON.parse(savedKey) as BookPerformance), [savedKey, project.id]);
  const dirty = JSON.stringify(draft) !== savedKey;
  const directionJob = project.pipeline_jobs?.find((job) => job.type === 'audiobook.direct-performance');
  const directing = Boolean(directionJob && ACTIVE_STATUSES.has(directionJob.status));
  const castSpeakers = project.speakers.filter((speaker) => speaker.status === 'active');

  function setBookLevel(level: Level, value: number) {
    setDraft((current) => ({ ...current, [level]: value }));
  }

  function setOwnLevels(speakerId: string, own: boolean) {
    setDraft((current) => {
      const speakers = { ...current.speakers };
      if (own) speakers[speakerId] = { stability: current.stability, style: current.style };
      else delete speakers[speakerId];
      return { ...current, speakers };
    });
  }

  function setSpeakerLevel(speakerId: string, level: Level, value: number) {
    setDraft((current) => ({ ...current, speakers: { ...current.speakers, [speakerId]: { ...current.speakers[speakerId], [level]: value } } }));
  }

  return (
    <section className="audiobook-performance" aria-label="Performance">
      <div className="audiobook-section-title"><div><h3>Performance</h3><p className="audiobook-subtitle">How much the voices vary and how expressive they are, for the book and for each character. Saving restarts rendering; unchanged lines are reused from the render cache.</p></div></div>
      <div className="audiobook-performance-levels" role="group" aria-label="Book performance">
        {(Object.keys(DEFAULT_LEVELS) as Level[]).map((level) => (
          <LevelSlider key={level} level={level} label={`Book ${LEVEL_LABELS[level].label.toLowerCase()}`} value={draft[level]} disabled={busy} onChange={(value) => setBookLevel(level, value)} />
        ))}
      </div>
      {castSpeakers.length > 0 && <div className="audiobook-performance-characters">
        {castSpeakers.map((speaker) => {
          const own = draft.speakers[speaker.id];
          return <div className="audiobook-performance-character" key={speaker.id}>
            <label className="audiobook-performance-own"><input type="checkbox" checked={Boolean(own)} disabled={busy} aria-label={`Own performance for ${speaker.canonical_name}`} onChange={(event) => setOwnLevels(speaker.id, event.currentTarget.checked)} /><strong>{speaker.canonical_name}</strong><small>{own ? 'Own levels' : 'Follows the book'}</small></label>
            {own && (Object.keys(DEFAULT_LEVELS) as Level[]).map((level) => (
              <LevelSlider key={level} level={level} label={`${speaker.canonical_name} ${LEVEL_LABELS[level].label.toLowerCase()}`} value={own[level] ?? draft[level]} disabled={busy} onChange={(value) => setSpeakerLevel(speaker.id, level, value)} />
            ))}
          </div>;
        })}
      </div>}
      <div className="audiobook-card-actions">
        <button type="button" className="audiobook-primary-action" disabled={busy || !dirty} onClick={() => void action(() => audiobookApi.setPerformance(project.id, draft), 'Performance saved. Render the book again to hear it.')}>Save performance</button>
        <button type="button" disabled={busy || !dirty} onClick={() => setDraft(saved)}>Discard changes</button>
        <button type="button" disabled={busy || JSON.stringify(draft) === JSON.stringify({ ...DEFAULT_LEVELS, speakers: {} })} onClick={() => setDraft({ ...DEFAULT_LEVELS, speakers: {} })}>Reset to defaults</button>
      </div>
      <div className="audiobook-performance-direction">
        <div><strong>Direct performance</strong><p className="audiobook-subtitle">Your chat model reads each chapter and writes a short delivery direction for every dialogue line (for example “angry, shouting” or “whispering, afraid”). You can review and edit them in Span review; notes you wrote yourself are never replaced, and re-classifying keeps a direction while its line keeps its speaker. Fish Audio S2 Pro speaks them as emotion tags; Qwen3-TTS ignores them.</p></div>
        <label className="audiobook-performance-auto">After classifying<select aria-label="Direct performance after classifying" value={project.auto_direct_performance ?? 'fish'} disabled={busy}
          onChange={(event) => { const policy = event.currentTarget.value as 'always' | 'fish' | 'never'; void action(() => audiobookApi.setAutoDirect(project.id, policy), 'Saved when to direct the performance.'); }}>
          <option value="fish">Direct when Fish S2 Pro is the TTS engine</option>
          <option value="always">Always direct</option>
          <option value="never">Don't direct (use the button)</option>
        </select></label>
        <div className="audiobook-card-actions">
          <button type="button" disabled={busy || directing || !project.current_source_revision_id} onClick={() => void action(() => audiobookApi.directPerformance(project.id), 'Directing the performance…')}>{directing ? 'Directing…' : directionJob?.status === 'completed' ? 'Direct again' : 'Direct performance'}</button>
          {directing && directionJob && <button type="button" disabled={busy} onClick={() => void action(() => audiobookApi.job(project.id, directionJob.id, 'cancel'), 'Directing canceled.')}>Cancel</button>}
        </div>
        <DirectionStatus job={directionJob} />
      </div>
    </section>
  );
}

function LevelSlider({ level, label, value, disabled, onChange }: { level: Level; label: string; value: number; disabled: boolean; onChange: (value: number) => void }) {
  return <label className="audiobook-performance-slider" title={LEVEL_LABELS[level].hint}>
    <span>{LEVEL_LABELS[level].label}</span><b>{value.toFixed(2)}</b>
    <input type="range" min={0} max={1} step={0.05} value={value} disabled={disabled} aria-label={label} onChange={(event) => onChange(Number(event.currentTarget.value))} />
  </label>;
}

function DirectionStatus({ job }: { job: AudiobookJobView | undefined }) {
  if (!job) return null;
  const progress = job.progress ?? {};
  if (job.status === 'failed') return <p className="audiobook-hint" role="alert">Directing failed: {job.error?.message ?? 'unknown error'}</p>;
  if (ACTIVE_STATUSES.has(job.status)) {
    return <p className="audiobook-hint" role="status">{progress.message ?? 'Waiting to start'}{progress.total ? ` · chapter ${Math.min((progress.current ?? 0) + 1, progress.total)} of ${progress.total}` : ''}</p>;
  }
  if (job.status !== 'completed') return null;
  const kept = progress.kept_user_delivery ?? 0;
  return <p className="audiobook-hint" role="status">Last run: {progress.directed ?? 0} lines directed, {progress.changed ?? 0} updated{kept ? `, ${kept} of your own notes kept` : ''}{progress.failed_windows ? `, ${progress.failed_windows} passages failed (run again to retry)` : ''}.</p>;
}
