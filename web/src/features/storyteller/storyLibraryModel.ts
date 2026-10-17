/** The story library: items from the draft, generated jobs and saved assets, and the trash (kept per browser). */
import { type JobRecord } from '../../api/client';
import { SavedStoryDraft, StoryAssetSummary, StoryLibraryItem, StoryLibrarySource, TrashedStoryLibraryItem, cleanStoryTitle, countWords, shortDate, storyDraftStorageKey, storyTrashStorageKey, titleFromStoryText, jobInputString } from './storyModel';

export function fullJobOutputText(job: { output_refs?: Array<{ content?: unknown }>; logs?: Array<{ content?: unknown }> }): string | null {
  const content = job.output_refs?.find((ref) => typeof ref.content === 'string')?.content ??
    job.logs?.find((log) => typeof log.content === 'string')?.content;
  return typeof content === 'string' && content.trim() ? content : null;
}

export function fullJobOutputTitle(job: { output_refs?: Array<{ title?: unknown }> }): string | null {
  const title = job.output_refs?.find((ref) => typeof ref.title === 'string')?.title;
  return cleanStoryTitle(typeof title === 'string' ? title : null);
}

export function buildStoryLibraryItems(savedDraft: SavedStoryDraft | null, jobs: JobRecord[], assets: StoryAssetSummary[]): StoryLibraryItem[] {
  const draftItems: StoryLibraryItem[] = savedDraft?.content ? [{
    id: 'draft:last',
    source: 'draft',
    title: savedDraft.title || 'Saved local draft',
    subtitle: `${countWords(savedDraft.content).toLocaleString()} words • saved draft`,
    content: savedDraft.content,
    jobId: savedDraft.sourceJobId ?? null,
    assetId: null,
  }] : [];
  const jobItems = jobs.map((job) => ({
    id: libraryJobId(job.id),
    source: 'job' as const,
    title: storyJobTitle(job),
    subtitle: `${countWords(fullJobOutputText(job) ?? '').toLocaleString()} words • ${jobInputString(job, 'action') ?? 'draft'}`,
    content: fullJobOutputText(job),
    jobId: job.id,
    assetId: null,
  }));
  const assetItems = assets.map((asset) => ({
    id: libraryAssetId(asset.id),
    source: 'asset' as const,
    title: storyAssetTitle(asset.file_name),
    subtitle: `${asset.type}${asset.created_at ? ` • ${shortDate(asset.created_at)}` : ''}`,
    content: null,
    jobId: null,
    assetId: asset.id,
  }));
  return [...draftItems, ...jobItems, ...assetItems];
}

export function readSavedStoryDraft(): SavedStoryDraft | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(storyDraftStorageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SavedStoryDraft>;
    if (typeof parsed.content !== 'string' || !parsed.content.trim()) return null;
    return {
      title: typeof parsed.title === 'string' && parsed.title.trim() ? parsed.title : 'Saved local draft',
      premise: typeof parsed.premise === 'string' ? parsed.premise : '',
      providerLabel: typeof parsed.providerLabel === 'string' ? parsed.providerLabel : undefined,
      wordCount: typeof parsed.wordCount === 'number' ? parsed.wordCount : undefined,
      chapterCount: typeof parsed.chapterCount === 'number' ? parsed.chapterCount : undefined,
      sourceJobId: typeof parsed.sourceJobId === 'string' ? parsed.sourceJobId : null,
      savedAt: typeof parsed.savedAt === 'string' ? parsed.savedAt : undefined,
      content: parsed.content,
    };
  } catch {
    return null;
  }
}

export function readTrashedStoryLibraryItems(): TrashedStoryLibraryItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(storyTrashStorageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(toTrashedStoryLibraryItem).filter((item): item is TrashedStoryLibraryItem => Boolean(item));
  } catch {
    return [];
  }
}

export function persistTrashedStoryLibraryItems(items: TrashedStoryLibraryItem[]): void {
  if (typeof window === 'undefined') return;
  try {
    if (items.length) {
      window.localStorage.setItem(storyTrashStorageKey, JSON.stringify(items));
    } else {
      window.localStorage.removeItem(storyTrashStorageKey);
    }
  } catch {
    // Best-effort local trash; the visible list still updates from React state.
  }
}

export function toTrashedStoryLibraryItem(value: unknown): TrashedStoryLibraryItem | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (typeof record.id !== 'string' || !isStoryLibrarySource(record.source)) return null;
  return {
    id: record.id,
    source: record.source,
    title: typeof record.title === 'string' && record.title.trim() ? record.title : 'Untitled story',
    subtitle: typeof record.subtitle === 'string' ? record.subtitle : '',
    content: typeof record.content === 'string' ? record.content : null,
    jobId: typeof record.jobId === 'string' ? record.jobId : null,
    assetId: typeof record.assetId === 'string' ? record.assetId : null,
    trashedAt: typeof record.trashedAt === 'string' ? record.trashedAt : new Date(0).toISOString(),
  };
}

export function isStoryLibrarySource(value: unknown): value is StoryLibrarySource {
  return value === 'draft' || value === 'job' || value === 'asset';
}

export function upsertTrashedStoryLibraryItem(
  items: TrashedStoryLibraryItem[],
  item: TrashedStoryLibraryItem,
): TrashedStoryLibraryItem[] {
  return [item, ...items.filter((entry) => entry.id !== item.id)];
}

export function mergeTrashedStoryLibraryItems(
  trashItems: TrashedStoryLibraryItem[],
  currentItems: StoryLibraryItem[],
): TrashedStoryLibraryItem[] {
  return trashItems.map((trashedItem) => {
    const currentItem = currentItems.find((item) => item.id === trashedItem.id);
    return currentItem ? { ...currentItem, trashedAt: trashedItem.trashedAt } : trashedItem;
  });
}

export function libraryJobId(jobId: string): string { return `job:${jobId}`; }

export function libraryAssetId(assetId: string): string { return `asset:${assetId}`; }


export function storyAssetTitle(fileName: string | undefined): string {
  if (!fileName) return 'Untitled Draft';
  const filename = fileName.split(/[\\/]/).pop() ?? fileName;
  return filename.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ') || 'Untitled Draft';
}

export function storyJobTitle(job: JobRecord): string {
  const text = fullJobOutputText(job);
  return cleanStoryTitle(jobInputString(job, 'title')) ??
    fullJobOutputTitle(job) ??
    titleFromStoryText(text) ??
    'Untitled story';
}
