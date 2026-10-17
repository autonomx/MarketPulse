import type { JobRecord } from '../../api/client';

interface RpgHermesSequenceJobPanelProps {
  activeJob?: JobRecord | null;
  isPending?: boolean;
  onCancel: () => void;
  onPause: () => void;
  onResume: () => void;
  onStart: () => void;
}

function progressLabel(job?: JobRecord | null): string {
  const stages = Array.isArray(job?.stages) ? job.stages : [];
  if (!stages.length) return '0%';
  const done = stages.filter((stage) => stage.status === 'completed').length;
  return `${Math.round((done / stages.length) * 100)}%`;
}

export function RpgHermesSequenceJobPanel({ activeJob, isPending = false, onCancel, onPause, onResume, onStart }: RpgHermesSequenceJobPanelProps) {
  const status = activeJob?.status ?? 'idle';
  return (
    <section className="rpg-card" aria-label="Hermes sequence job">
      <div className="rpg-section-heading">
        <p className="eyebrow">Hermes job</p>
        <span>{status}</span>
      </div>
      <div className="rpg-resource-grid">
        <div>
          <span>Job</span>
          <strong>{activeJob?.id ?? 'none'}</strong>
        </div>
        <div>
          <span>Progress</span>
          <strong>{progressLabel(activeJob)}</strong>
        </div>
      </div>
      <div role="group" className="rpg-survival-actions" aria-label="Hermes sequence job controls">
        <button className="rpg-secondary-button" disabled={isPending} onClick={onStart} type="button">Start</button>
        <button className="rpg-secondary-button" disabled={isPending || !activeJob} onClick={onPause} type="button">Pause</button>
        <button className="rpg-secondary-button" disabled={isPending} onClick={onResume} type="button">Resume</button>
        <button className="rpg-secondary-button" disabled={isPending || !activeJob} onClick={onCancel} type="button">Cancel</button>
      </div>
      <small>Sequence jobs persist through the shared job system; execution still uses approved RPG steps.</small>
    </section>
  );
}
