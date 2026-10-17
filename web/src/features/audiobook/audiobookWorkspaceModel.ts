/** The audiobook workspace's module-level helpers: render status labels, API calls and formatting (WP-9.5 split them out of AudiobookWorkspace). */
import { audiobookApi } from './audiobookApi';
import type { ProjectSummary, AudiobookJobView } from './audiobookTypes';
import { ApiError } from '../../api/errors';
import { unwrapAs } from '../../api/http';
import { uploadBinary } from '../../api/transport';
import { api } from './api/gateway';

export const ACTIVE_RENDER_STATUSES = new Set(['queued', 'waiting', 'leased', 'running', 'retrying', 'cancel_requested', 'paused']);
export const CONTROLLABLE_RENDER_STATUSES = new Set(['queued', 'waiting', 'leased', 'running', 'retrying', 'paused']);
export const QUEUED_RENDER_STATUSES = new Set(['queued', 'waiting', 'retrying']);
export const RUNNING_RENDER_STATUSES = new Set(['leased', 'running']);

export function renderJobLabel(job: AudiobookJobView | undefined, projectState: string): string {
  if (!job) {
    if (projectState === 'rendering') return 'Waiting';
    if (projectState === 'mastering') return 'Mastering';
    if (projectState === 'ready_to_export' || projectState === 'exported') return 'Completed';
    return 'Not started';
  }
  if (job.status === 'completed') return 'Completed';
  if (QUEUED_RENDER_STATUSES.has(job.status)) return 'Queued';
  if (RUNNING_RENDER_STATUSES.has(job.status)) return 'Rendering';
  if (job.status === 'cancel_requested') return 'Cancelling';
  if (job.status === 'paused') return 'Paused';
  if (job.status === 'failed' || job.status === 'dead_letter') return 'Failed';
  if (job.status === 'canceled' || job.status === 'cancelled') return 'Canceled';
  if (job.status === 'stale') return 'Stale';
  return job.status.replaceAll('_', ' ');
}

export function renderJobProgress(job: AudiobookJobView | undefined, projectState: string): number {
  const status = renderJobLabel(job, projectState);
  if (status === 'Completed') return 100;
  const current = Number(job?.progress?.current);
  const total = Number(job?.progress?.total);
  if (!Number.isFinite(current) || !Number.isFinite(total) || total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
}

export function pipelineJobProgress(job: AudiobookJobView | undefined): number {
  return pipelineJobProgressPercent(job) ?? 0;
}

export function pipelineJobProgressPercent(job: AudiobookJobView | undefined): number | null {
  const current = Number(job?.progress?.current);
  const total = Number(job?.progress?.total);
  if (!Number.isFinite(current) || !Number.isFinite(total) || total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((current / total) * 100)));
}

export type LibrarySection = 'library' | 'projects' | 'books' | 'chapters' | 'assets' | 'documents' | 'characters' | 'review' | 'voices' | 'pronunciations' | 'exports';

export type WorkspaceAsset = {
  id: string;
  name: string;
  type: 'Cover Art' | 'Chapter Thumbnail' | 'Audio' | 'Document' | 'Export' | 'Reference';
  detail: string;
  status: 'Used' | 'Unused' | 'Reference' | 'Ready';
  chapters?: string;
  href?: string;
  image?: boolean;
  format?: string;
  assetId?: string;
  deletable?: boolean;
  createdAt?: string | null;
  sizeBytes?: number;
};

export function formatAssetCreationDate(value?: string | null): string {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleString() : 'Date unavailable';
}

export function formatAssetSize(bytes?: number): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / (1024 ** 2)).toFixed(1)} MB`;
  return `${(bytes / (1024 ** 3)).toFixed(1)} GB`;
}

export type WorkspaceDocument = {
  id: string;
  title: string;
  type: string;
  lastEdited: string;
  author: string;
  linked: string;
  status: 'Final';
  preview: string;
  href?: string;
};

export const base = '/api/audiobook';
export const sourceExtensions = new Set([
  'docx', 'epub', 'html', 'htm', 'markdown', 'md', 'pdf', 'text', 'txt',
]);
export const sourceAccept = '.pdf,.epub,.docx,.html,.htm,.txt,.text,.md,.markdown';
export const sourceFormatsLabel = 'PDF, EPUB, DOCX, HTML, TXT, or Markdown';
export const documentRoleOptions = [
  ['story_text', 'Story text'],
  ['scene_heading', 'Scene heading'],
  ['scene_break', 'Scene break'],
  ['chapter_heading', 'Chapter heading'],
  ['part_heading', 'Part heading'],
  ['prologue_heading', 'Prologue heading'],
  ['epilogue_heading', 'Epilogue heading'],
  ['book_title', 'Book title'],
  ['subtitle', 'Subtitle'],
  ['author_name', 'Author name'],
  ['dedication', 'Dedication'],
  ['epigraph', 'Epigraph'],
  ['foreword', 'Foreword'],
  ['preface', 'Preface'],
  ['author_note', 'Author note'],
  ['acknowledgements', 'Acknowledgements'],
  ['footnote_body', 'Footnote body'],
  ['endnote', 'Endnote'],
  ['table_of_contents', 'Table of contents'],
  ['copyright', 'Copyright'],
  ['isbn', 'ISBN'],
  ['publisher_metadata', 'Publisher metadata'],
  ['bibliography', 'Bibliography'],
  ['index', 'Index'],
  ['page_number', 'Page number'],
  ['running_header', 'Running header'],
  ['running_footer', 'Running footer'],
  ['footnote_marker', 'Footnote marker'],
  ['unknown', 'Unknown'],
] as const;

export const failureMessage = (error: ApiError) => error.detail || `Request failed (${error.status})`;

export function audiobook<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  return unwrapAs(call, failureMessage);
}

export async function audiobookUpload(path: `/api/${string}`, file: Blob | null, query: Record<string, string | undefined>): Promise<void> {
  try {
    await uploadBinary<unknown>(path, file, { query });
  } catch (error) {
    throw error instanceof ApiError ? new Error(failureMessage(error)) : error;
  }
}

export function pageExclusion(excludePageRanges: string): string | undefined {
  return excludePageRanges.trim() || undefined;
}

export const projectPath = (projectId: string) => ({ project_id: projectId });

export async function saveClassificationRules(projectId: string, customRules: string): Promise<void> {
  await audiobookApi.saveClassificationRules(projectId, customRules.trim());
}

export async function uploadSource(projectId: string, file: File, excludePageRanges: string, customRules?: string): Promise<void> {
  const extension = file.name.split('.').pop()?.toLowerCase();
  if (!extension || !sourceExtensions.has(extension)) {
    throw new Error(`Choose a ${sourceFormatsLabel} file.`);
  }
  if (customRules !== undefined) await saveClassificationRules(projectId, customRules);
  await audiobookUpload(`${base}/projects/${encodeURIComponent(projectId)}/source`, file, {
    source_format: extension,
    filename: file.name,
    exclude_pages: pageExclusion(excludePageRanges),
  });
}

export async function importLibrarySource(projectId: string, filename: string, excludePageRanges: string, customRules: string): Promise<void> {
  await saveClassificationRules(projectId, customRules);
  const excludePages = pageExclusion(excludePageRanges);
  await audiobook(api.POST('/api/audiobook/projects/{project_id}/source/library', {
    params: { path: projectPath(projectId), query: { filename, ...(excludePages ? { exclude_pages: excludePages } : {}) } },
  }));
}

export async function uploadCover(projectId: string, file: File): Promise<void> {
  await audiobookUpload(`${base}/projects/${encodeURIComponent(projectId)}/cover`, file, { filename: file.name });
}

export async function updateProjectMetadata(projectId: string, title: string, author: string): Promise<void> {
  await audiobook(api.PATCH('/api/audiobook/projects/{project_id}', { params: { path: projectPath(projectId) }, body: { title, author } }));
}

export async function deleteAudiobookProject(projectId: string): Promise<void> {
  await audiobook(api.DELETE('/api/audiobook/projects/{project_id}', { params: { path: projectPath(projectId) } }));
}

export async function deleteAudiobookAsset(projectId: string, assetId: string): Promise<void> {
  await audiobook(api.DELETE('/api/audiobook/projects/{project_id}/assets/{asset_id}', {
    params: { path: { project_id: projectId, asset_id: assetId } },
  }));
}

export async function reclassifyAudiobook(projectId: string, customRules: string): Promise<void> {
  await audiobookApi.reclassify(projectId, customRules.trim());
}

export async function extractQuotes(projectId: string, customRules: string): Promise<void> {
  await audiobookApi.extractQuotes(projectId, customRules.trim());
}

export async function setAudiobookReadingMode(
  projectId: string, mode: 'standard' | 'story_only' | 'verbatim',
): Promise<void> {
  await audiobook(api.PATCH('/api/audiobook/projects/{project_id}/reading-policy', { params: { path: projectPath(projectId) }, body: { mode } }));
}

export async function setDocumentBlockAction(
  projectId: string, blockId: string,
  action: 'DEFAULT' | 'READ' | 'SKIP' | 'READ_ONCE',
  roleOverride: string | null = null,
): Promise<void> {
  await audiobook(api.POST('/api/audiobook/projects/{project_id}/document-overrides', {
    params: { path: projectPath(projectId) },
    body: { scope: 'BLOCK', scope_key: blockId, action, role_override: roleOverride },
  }));
}

export function formatDuration(seconds?: number): string {
  if (!seconds || seconds <= 0) return '—';
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

export function formatStorage(bytes: number): string {
  if (bytes <= 0) return '—';
  if (bytes >= 1024 ** 3) return `${(bytes / (1024 ** 3)).toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / (1024 ** 2)))} MB`;
}

export function formatCount(value?: number | null): string {
  return value == null ? '—' : value.toLocaleString();
}

export function libraryProjectStatus(item: ProjectSummary): string {
  if (item.state === 'exported') return 'Exported';
  if (item.state === 'ready_to_export') return 'Ready';
  if (item.state === 'review_required') return 'Review required';
  if (['rendering', 'mastering'].includes(item.state)) return 'In progress';
  if (item.state === 'ready_to_render') return 'Ready to render';
  if (item.state === 'failed') return 'Needs attention';
  return item.state.replaceAll('_', ' ');
}

export function libraryCardStatus(item: ProjectSummary): string {
  if (item.state === 'exported') return 'Completed';
  if (item.state === 'ready_to_export') return 'Ready to Export';
  if (['rendering', 'mastering'].includes(item.state)) return 'In Production';
  if (item.state === 'ready_to_render') return 'Ready to Render';
  if (item.state === 'review_required') return 'In Review';
  if (item.state === 'failed') return 'Needs Attention';
  return 'Draft';
}
