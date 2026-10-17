/** Storyteller's types, settings and small helpers (WP-9.5 split them out of StorytellerWorkspace). */
import type { JobRecord, ProviderFacadePayload, omnixApiClient } from '../../api/client';

export interface StorytellerFormValues {
  providerId: string;
  title: string;
  premise: string;
}

export type StoryActionMode = 'draft' | 'continue' | 'rewrite' | 'expand' | 'dialogue' | 'summarize';

export type StoryQuickActionMode = Exclude<StoryActionMode, 'draft'>;

export type SaveFeedbackKind = 'saved' | 'exported' | 'error';

export type StoryLibrarySource = 'draft' | 'job' | 'asset';

export type StoryWorkspaceMode = 'writing' | 'story';

export type StoryLibrarySection = 'drafts' | 'stories' | 'characters' | 'world-notes' | 'prompts' | 'trash';

export interface StoryGenerationRequest {
  values: StorytellerFormValues;
  action: StoryActionMode;
  sourceText: string | null;
  sourceJobId: string | null;
  sourceLibraryItemId?: string | null;
  sourceStoryTitle?: string | null;
  generateTitle?: boolean;
  interactionMode?: StoryWorkspaceMode;
  userResponse?: string | null;
  suggestedChoice?: string | null;
}

export interface StoryOutlineScene {
  id: string;
  label: string;
  title: string;
  placeholder?: boolean;
}

export interface StoryOutlineChapter {
  id: string;
  number: number;
  label: string;
  title: string;
  scenes: StoryOutlineScene[];
}

export interface StoryTextBlock {
  id?: string;
  kind: 'chapter' | 'scene' | 'paragraph';
  text: string;
}

export interface SaveFeedback {
  kind: SaveFeedbackKind;
  message: string;
}

export interface SavedStoryDraft {
  title: string;
  premise?: string;
  providerLabel?: string;
  wordCount?: number;
  chapterCount?: number;
  sourceJobId?: string | null;
  savedAt?: string;
  content: string;
}

export interface StoryAssetSummary {
  id: string;
  file_name: string;
  type: string;
  created_at?: string;
}

export interface StoryLibraryItem {
  id: string;
  source: StoryLibrarySource;
  title: string;
  subtitle: string;
  content: string | null;
  jobId: string | null;
  assetId: string | null;
}

export interface TrashedStoryLibraryItem extends StoryLibraryItem {
  trashedAt: string;
}

export interface StorySceneAddition {
  id: string;
  sourceItemId: string;
  sourceJobId: string;
  chapterNumber: number;
  sceneNumber: number;
  title: string;
  chapterTitle?: string;
  startsNewChapter?: boolean;
  content: string;
  storyTitle: string | null;
  createdAt: string;
}

export const storyDraftStorageKey = 'omnix:storyteller:last-draft';

export const storyTrashStorageKey = 'omnix:storyteller:trash';

export const storySceneAdditionsStorageKey = 'omnix:storyteller:scene-additions';

export const toneOptions = ['Cozy', 'Hopeful', 'Gentle', 'Mystery'];

export const styleOptions = ['Lyrical & Descriptive', 'Fast-paced', 'Dialogue-heavy', 'Cinematic', 'Literary'];

export const quickActions: Array<{ mode: StoryQuickActionMode; label: string; description: string }> = [
  { mode: 'continue', label: 'Continue Story', description: 'AI continues from here' },
  { mode: 'rewrite', label: 'Rewrite Paragraph', description: 'Improve clarity & flow' },
  { mode: 'expand', label: 'Expand Scene', description: 'Add depth & detail' },
  { mode: 'dialogue', label: 'Dialogue Polish', description: 'Enhance dialogue' },
  { mode: 'summarize', label: 'Summarize', description: 'Condense this section' },
];

export const librarySections: Array<{ id: StoryLibrarySection; label: string }> = [
  { id: 'drafts', label: 'Drafts' },
  { id: 'stories', label: 'Stories' },
  { id: 'characters', label: 'Characters' },
  { id: 'world-notes', label: 'World Notes' },
  { id: 'prompts', label: 'Prompts' },
];

export function librarySectionLabel(section: StoryLibrarySection): string {
  return librarySections.find((entry) => entry.id === section)?.label ?? 'Trash';
}

export function llmCapableProviders(payload: ProviderFacadePayload | undefined) {
  return payload?.providers.filter((provider) => provider.capabilities.includes('chat') || provider.capabilities.includes('completion')) ?? [];
}

export function includeMutationJob(jobs: JobRecord[], mutationJob: JobRecord | null): JobRecord[] {
  return mutationJob ? [mutationJob, ...jobs.filter((job) => job.id !== mutationJob.id)] : jobs;
}

export function latestStoryTitleOverride(additions: StorySceneAddition[]): string | null {
  return additions.slice().reverse().find((addition) => cleanStoryTitle(addition.storyTitle))?.storyTitle ?? null;
}

export function storyDisplayTitle(title: string | null | undefined, storyText: string | null): string {
  return cleanStoryTitle(title) ?? titleFromStoryText(storyText) ?? 'Untitled story';
}

export function shouldGenerateStoryTitle(formTitle: string, displayTitle: string): boolean {
  return !formTitle.trim() && !cleanStoryTitle(displayTitle);
}

export function cleanStoryTitle(value: string | null | undefined): string | null {
  const title = value?.trim();
  if (!title || /^untitled story\b/i.test(title)) return null;
  return title;
}

export function titleFromStoryText(text: string | null): string | null {
  const heading = text?.split(/\r?\n/).map((line) => line.trim()).find((line) => /^#\s+\S/.test(line));
  return cleanStoryTitle(heading?.replace(/^#\s+/, ''));
}

export function providerDisplayName(providers: Array<{ id: string; label: string }>, selectedProviderId: string, fallbackLabel: string | null = null): string {
  return providers.find((provider) => provider.id === selectedProviderId)?.label ?? fallbackLabel ?? 'Omnix LLM';
}

export function countWords(text: string): number { return text.trim() ? text.trim().split(/\s+/).length : 0; }

export function truncate(text: string, length: number): string { return text.length > length ? `${text.slice(0, length)}…` : text; }

export function storyVersionTitle(job: JobRecord, index: number): string {
  return jobInputString(job, 'action') ?? (index === 0 ? 'Just now' : 'Previous');
}

export function shortDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function promptTemplateForAction(action: StoryActionMode): string {
  return `storyteller.${action}.v1`;
}

export function actionLabel(action: StoryActionMode): string {
  return action === 'dialogue' ? 'polish dialogue' : action;
}

export function nextChapterNumber(outline: StoryOutlineChapter[]): number {
  return Math.max(0, ...outline.map((chapter) => chapter.number)) + 1;
}

export function storyModeContext(activeStoryText: string | null, response: string): string {
  return [activeStoryText, `Player response: ${response}`].filter(Boolean).join('\n\n');
}

export function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'story';
}

/** The settings a story job carries besides the request: tone, writing style and chapter. */
export type StoryJobSettings = { tone: string; writingStyle: string; chapter: number };

/** The `story.generate` job for one request: outline, draft and store stages on the LLM. */
export function storyJobRequest(
  {
    values,
    action,
    sourceText,
    sourceJobId,
    sourceLibraryItemId,
    sourceStoryTitle,
    generateTitle,
    interactionMode,
    userResponse,
    suggestedChoice,
  }: StoryGenerationRequest,
  settings: StoryJobSettings,
): Parameters<typeof omnixApiClient.createJob>[0] {
  return {
    module: 'storyteller',
    type: 'story.generate',
    resource_class: 'gpu:llm',
    priority: 0,
    input_payload: {
      title: values.title || null,
      premise: values.premise,
      provider_id: values.providerId || null,
      prompt_template_id: promptTemplateForAction(action),
      action,
      generate_title: Boolean(generateTitle),
      interaction_mode: interactionMode ?? 'writing',
      user_response: userResponse ?? null,
      suggested_choice: suggestedChoice ?? null,
      source_text: sourceText,
      source_job_id: sourceJobId,
      source_library_item_id: sourceLibraryItemId ?? null,
      source_story_title: sourceStoryTitle ?? null,
      tone: settings.tone,
      writing_style: settings.writingStyle,
      chapter: settings.chapter,
    },
    stages: [
      {
        id: 'outline',
        label: action === 'draft' ? 'Build outline' : `Plan ${actionLabel(action)}`,
        resource_class: 'gpu:llm',
        status: 'queued',
      },
      {
        id: 'draft',
        label: action === 'draft' ? 'Draft story' : actionLabel(action),
        resource_class: 'gpu:llm',
        status: 'queued',
      },
      { id: 'store-story', label: 'Store story asset', resource_class: 'cpu', status: 'queued' },
    ],
  };
}

export function jobInputString(job: JobRecord | null, key: string): string | null {
  const input = job?.input_payload;
  if (!input || typeof input !== 'object') return null;
  const value = (input as Record<string, unknown>)[key];
  return typeof value === 'string' && value.trim() ? value : null;
}
