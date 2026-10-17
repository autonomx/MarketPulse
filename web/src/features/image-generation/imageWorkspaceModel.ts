import type { AssetListResponse, JobListResponse, JobRecord, ProviderFacadePayload } from '../../api/client';

export const IMAGE_JOBS_QUERY_KEY = ['image-generation', 'jobs'] as const;
export const IMAGE_ASSETS_QUERY_KEY = ['image-generation', 'assets'] as const;
export const IMAGE_JOB_EVENT_TYPES = ['job.created', 'job.updated', 'job.completed', 'job.failed', 'job.canceled'] as const;
const ACTIVE_IMAGE_JOB_STATUSES = new Set(['queued', 'waiting', 'retrying', 'leased', 'running', 'cancel_requested']);

export type ImageAsset = AssetListResponse['assets'][number];

export function imageCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.family === 'image' && provider.capabilities.includes('image')) ?? [];
}

export function isImageJobEventPayload(value: unknown): boolean {
  const job = imageJobEventPayload(value);
  return Boolean(job && (job.type === 'image.generate' || job.module === 'image' || job.module === 'image-generation'));
}

export function isCompletedImageJobEventPayload(value: unknown): boolean {
  const job = imageJobEventPayload(value);
  if (!job) return false;
  if (job.status === 'completed') return true;
  return Array.isArray(job.output_refs) && job.output_refs.some((ref) => {
    if (!ref || typeof ref !== 'object') return false;
    const candidate = ref as { type?: unknown; asset_id?: unknown };
    return candidate.type === 'image' && typeof candidate.asset_id === 'string' && Boolean(candidate.asset_id);
  });
}

export function hasActiveImageJobs(payload: JobListResponse | undefined): boolean {
  return payload?.jobs.some((job) => ACTIVE_IMAGE_JOB_STATUSES.has(job.status)) ?? false;
}

export function completedImageAssetIds(jobs: JobRecord[]): string[] {
  const result: string[] = [];
  for (const job of jobs) {
    if (job.status !== 'completed') continue;
    for (const ref of job.output_refs ?? []) {
      const assetId = ref.type === 'image' && typeof ref.asset_id === 'string' ? ref.asset_id : '';
      if (assetId && !result.includes(assetId)) result.push(assetId);
    }
  }
  return result;
}

export function selectLatestImageAsset(
  assets: ImageAsset[],
  jobs: JobRecord[],
  selectedAssetId: string | null,
  submittedJobId?: string,
): ImageAsset | undefined {
  const byId = new Map(assets.map((asset) => [asset.id, asset]));
  if (selectedAssetId && byId.has(selectedAssetId)) return byId.get(selectedAssetId);
  if (submittedJobId) {
    const submittedAsset = assets.find((asset) => asset.source_job_id === submittedJobId);
    if (submittedAsset) return submittedAsset;
  }
  for (const job of jobs) {
    for (const ref of job.output_refs ?? []) {
      const assetId = typeof ref.asset_id === 'string' ? ref.asset_id : '';
      if (assetId && byId.has(assetId)) return byId.get(assetId);
    }
  }
  return assets[0];
}

export function imageAssetUrl(assetId: string, download = false): string {
  const suffix = download ? '?download=true' : '?preview=true';
  return `/api/assets/${encodeURIComponent(assetId)}/file${suffix}`;
}

export function imageAssetTitle(asset: ImageAsset): string {
  return metadataString(asset, 'title') || metadataString(asset, 'prompt') || 'Generated image';
}

export function imageAssetMetadata(asset: ImageAsset): string {
  const width = metadataNumber(asset, 'width');
  const height = metadataNumber(asset, 'height');
  const provider = metadataString(asset, 'provider_key') || metadataString(asset, 'provider_id');
  return [width && height ? `${width} x ${height}` : '', provider].filter(Boolean).join(' / ') || asset.mime_type;
}

export function formatCreatedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function parseJobEvent(data: unknown): unknown {
  if (typeof data !== 'string') return undefined;
  try { return JSON.parse(data); } catch { return undefined; }
}

function imageJobEventPayload(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const payload = (value as { payload?: unknown }).payload;
  return payload && typeof payload === 'object' ? payload as Record<string, unknown> : undefined;
}

function metadataString(asset: ImageAsset, key: string): string {
  const value = asset.metadata?.[key];
  return typeof value === 'string' ? value : '';
}

function metadataNumber(asset: ImageAsset, key: string): number | undefined {
  const value = asset.metadata?.[key];
  return typeof value === 'number' ? value : undefined;
}
