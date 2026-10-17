import { Button, Progress, Text } from '@mantine/core';
import { useState } from 'react';
import type { JobRecord } from '../../api/client';
import { OmnixStatusPill } from '../../design/primitives';
import { imageAssetUrl } from './imageWorkspaceModel';
import { ImagePreviewDialog } from './ImagePreviewDialog';
import { jobProgressPercent } from '../../api/jobProgress';

const ACTIVE_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running', 'cancel_requested']);
const RETRYABLE_STATUSES = new Set(['failed', 'canceled', 'stale']);
const COLLAPSED_JOB_LIMIT = 4;

interface ImageJobListProps {
  jobs: JobRecord[];
  cancelingJobId?: string;
  retryingJobId?: string;
  onCancel: (jobId: string) => void;
  onRetry: (jobId: string) => void;
  onSelectAsset?: (assetId: string) => void;
}

export function ImageJobList({ jobs, cancelingJobId, retryingJobId, onCancel, onRetry, onSelectAsset }: ImageJobListProps) {
  const [showAll, setShowAll] = useState(false);
  const [preview, setPreview] = useState<{ assetId: string; title: string } | null>(null);
  if (!jobs.length) return <div className="image-empty-state" role="status">No image jobs queued.</div>;

  const visibleJobs = showAll ? jobs : jobs.slice(0, COLLAPSED_JOB_LIMIT);
  const hasHiddenJobs = jobs.length > COLLAPSED_JOB_LIMIT;

  return (
    <div className="image-job-list-wrap">
      <div className="image-job-list">
        {visibleJobs.map((job) => {
          const assetId = firstImageAssetId(job);
          const prompt = imageJobPrompt(job);
          const progress = imageJobProgressPresentation(job);
          const duration = imageJobDurationLabel(job);
          return (
            <article className="image-job-card" key={job.id} aria-label={`Image job ${prompt || job.id}`}>
              <div className="image-job-header">
                <div>
                  <strong title={prompt || job.type}>{prompt || job.type}</strong>
                  <time>{new Date(job.created_at).toLocaleString()}</time>
                </div>
                <OmnixStatusPill>{job.status}</OmnixStatusPill>
              </div>

              <div className="image-job-content">
                {assetId ? (
                  <button
                    type="button"
                    className="image-job-preview-button"
                    aria-label={`Enlarge ${prompt || 'image result'}`}
                    onClick={() => setPreview({ assetId, title: prompt || 'Image result' })}
                  >
                    <img alt="" loading="lazy" src={imageAssetUrl(assetId)} />
                  </button>
                ) : <span className="image-job-placeholder" aria-hidden="true">✦</span>}
                <div className="image-job-details">
                  <Progress className={progress.indeterminate ? 'image-job-progress indeterminate' : 'image-job-progress'} value={progress.value} aria-label={`${job.type} progress`} />
                  <Text size="xs">{progress.label}</Text>
                  {duration ? <Text className="image-job-duration" size="xs">{duration}</Text> : null}
                  {job.error ? <Text c="red" size="xs" role="alert">{job.error.message} ({job.error.code})</Text> : null}
                  <div className="image-job-actions">
                    {assetId ? (
                      <>
                        {onSelectAsset ? (
                          <Button size="compact-xs" variant="light" onClick={() => onSelectAsset(assetId)}>View in Latest Result</Button>
                        ) : (
                          <Button component="a" href={imageAssetUrl(assetId)} size="compact-xs" variant="light">Open result</Button>
                        )}
                        <Button component="a" href={imageAssetUrl(assetId, true)} download size="compact-xs" variant="subtle" aria-label="Download image result">↓</Button>
                      </>
                    ) : null}
                    {canCancelImageJob(job) ? (
                      <Button color="red" disabled={cancelingJobId === job.id || job.status === 'cancel_requested'} loading={cancelingJobId === job.id} onClick={() => onCancel(job.id)} size="compact-xs" variant="subtle">Cancel</Button>
                    ) : null}
                    {canRetryImageJob(job) ? (
                      <Button disabled={retryingJobId === job.id} loading={retryingJobId === job.id} onClick={() => onRetry(job.id)} size="compact-xs" variant="light">Retry</Button>
                    ) : null}
                  </div>
                </div>
              </div>
            </article>
          );
        })}
      </div>
      <footer className="image-job-footer">
        <span>{visibleJobs.length} of {jobs.length} job{jobs.length === 1 ? '' : 's'}</span>
        {hasHiddenJobs ? (
          <button type="button" onClick={() => setShowAll((value) => !value)}>{showAll ? 'Show latest only' : `Show all ${jobs.length}`}</button>
        ) : null}
      </footer>
      {preview ? (
        <ImagePreviewDialog
          downloadUrl={imageAssetUrl(preview.assetId, true)}
          imageUrl={imageAssetUrl(preview.assetId)}
          metadata="Completed image result"
          onClose={() => setPreview(null)}
          title={preview.title}
        />
      ) : null}
    </div>
  );
}

export function canCancelImageJob(job: JobRecord): boolean {
  return ACTIVE_STATUSES.has(job.status);
}

export function canRetryImageJob(job: JobRecord): boolean {
  return RETRYABLE_STATUSES.has(job.status);
}

export function firstImageAssetId(job: JobRecord): string | undefined {
  for (const ref of job.output_refs ?? []) {
    if (ref.type === 'image' && typeof ref.asset_id === 'string' && ref.asset_id) return ref.asset_id;
  }
  return undefined;
}

function imageJobPrompt(job: JobRecord): string {
  const prompt = job.input_payload?.prompt;
  return typeof prompt === 'string' ? prompt : '';
}

interface ImageJobProgressPresentation {
  indeterminate: boolean;
  label: string;
  value: number;
}

function imageJobProgressPresentation(job: JobRecord): ImageJobProgressPresentation {
  const status = String(job.status);
  const message = job.progress?.message?.trim();
  const percent = jobProgressPercent(job.progress);
  if (status === 'completed') return { indeterminate: false, label: 'Completed successfully', value: 100 };
  if (status === 'failed') return { indeterminate: false, label: 'Generation failed', value: percent };
  if (status === 'canceled') return { indeterminate: false, label: 'Canceled', value: percent };
  if (status === 'leased') return { indeterminate: true, label: message || 'Preparing provider...', value: 100 };
  if (status === 'running') {
    if (message && !/^(running|generating image)$/i.test(message)) {
      return { indeterminate: false, label: message, value: percent };
    }
    return { indeterminate: true, label: message ? `${message}...` : 'Generating image...', value: 100 };
  }
  if (message) return { indeterminate: false, label: message, value: percent };
  return { indeterminate: false, label: 'Waiting in queue', value: percent };
}

export function imageJobDurationLabel(job: JobRecord, now = Date.now()): string | undefined {
  const startedAt = timestamp(job.started_at);
  if (!startedAt) return undefined;

  if (job.status === 'completed') {
    const completedAt = timestamp(job.completed_at) ?? timestamp(job.updated_at);
    return completedAt ? `Generated in ${formatDuration(completedAt - startedAt)}` : undefined;
  }

  return ACTIVE_STATUSES.has(job.status) ? `Generating for ${formatDuration(now - startedAt)}` : undefined;
}

function timestamp(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? undefined : parsed;
}

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.floor(milliseconds / 1_000));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainingSeconds = seconds % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${remainingSeconds}s`;
  return `${remainingSeconds}s`;
}

